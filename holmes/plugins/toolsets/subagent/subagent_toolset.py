"""Sub-agent delegation toolset.

Exposes a `delegate_task` tool that runs a focused child investigation in its
own agentic loop (ToolCallingLLM) with its own context window. When the LLM
issues several `delegate_task` calls in a single response, the existing tool
ThreadPoolExecutor runs them concurrently, so independent sub-investigations
proceed in parallel.

The child loop gets the same tools as the parent minus `delegate_task` itself
(delegation depth is capped at 1) and reports its final answer back as the
tool result. LLM usage incurred by the child is propagated via
StructuredToolResult.llm_usage so the parent request's cost/token stats stay
accurate.
"""

import logging
import os
import re
from typing import Any, Dict, Optional

from holmes.common.env_vars import load_bool
from holmes.core.llm_usage import RequestStats
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)

logger = logging.getLogger(__name__)

DELEGATE_TASK_TOOL_NAME = "delegate_task"
SUBAGENTS_ENABLED_ENV_VAR = "HOLMES_ENABLE_SUBAGENTS"
# Bypass the per-model gate below (used for A/B benchmarking).
SUBAGENTS_FORCE_ENV_VAR = "HOLMES_SUBAGENTS_FORCE"

# Max agentic iterations for a single sub-agent. Sub-agent tasks are focused,
# so they need far fewer steps than a full investigation.
SUBAGENT_MAX_STEPS = int(os.environ.get("HOLMES_SUBAGENT_MAX_STEPS", "40"))

# Models at or above this Opus version investigate wide/deep tasks efficiently
# in a single context: measured A/B (evals 272-274) shows delegation gives them
# no accuracy gain while roughly doubling tokens and latency. Sub-agents are
# therefore not offered to these models even when HOLMES_ENABLE_SUBAGENTS is
# set, unless HOLMES_SUBAGENTS_FORCE=true.
_MIN_GATED_OPUS_VERSION = (4, 6)
_OPUS_VERSION_RE = re.compile(r"opus[-_.]?(\d+)(?:[.\-_](\d+))?")


def is_model_self_sufficient(model: Optional[str]) -> bool:
    """True when the model handles wide investigations better without sub-agents.

    Matches Claude Opus >= 4.6 in any of its model-id spellings
    (claude-opus-4.6, claude-opus-4-6-v1, openai/anthropic/claude-opus-4.6,
    us.anthropic.claude-opus-5-..., etc.).
    """
    if not model:
        return False
    match = _OPUS_VERSION_RE.search(model.lower())
    if not match:
        return False
    major = int(match.group(1))
    minor = int(match.group(2)) if match.group(2) else 0
    if major > 20:
        # Date stuck directly after "opus" (e.g. claude-3-opus-20240229);
        # the family version precedes "opus" and is < 4.6.
        return False
    if minor > 99:
        # Date in the minor slot (e.g. claude-opus-4-20250514 is Opus 4.0).
        minor = 0
    return (major, minor) >= _MIN_GATED_OPUS_VERSION


def subagents_enabled_for_model(model: Optional[str]) -> bool:
    """Whether the delegate_task tool should be offered to this model."""
    if load_bool(SUBAGENTS_FORCE_ENV_VAR, False):
        return True
    return not is_model_self_sufficient(model)

SUBAGENT_PREAMBLE = """You are a sub-agent investigator working for a lead investigation agent.
The lead agent delegated one focused task to you. You do NOT see the original user question or the lead agent's conversation — your task description below is your entire context.

Rules:
* Investigate the task thoroughly using your tools before answering. Never answer from assumption when a tool can verify.
* When the task involves analyzing behavior over time, examine the full history of the data (e.g. complete logs, not just the most recent lines) — trends are invisible in a small tail sample.
* Be calibrated: distinguish sustained trends and real failures from normal jitter, noise or periodic oscillation. Do not report normal variation as a problem.
* Stay strictly within the scope of the delegated task. Do not investigate unrelated resources.
* Your final message is your report to the lead agent — it is the ONLY thing returned to it. Make it self-contained:
  - Lead with your conclusion (e.g. root cause, or 'healthy — no issues found').
  - Back every claim with concrete evidence from tool output: exact resource names, namespaces, error messages, log lines, status fields, counts.
  - Include exact identifiers and error text verbatim; the lead agent cannot re-run your tools cheaply, so do not omit specifics.
  - If you could not complete part of the task, say which part and why (e.g. permission error, no data found, which queries you tried).

"""

SUBAGENT_REPORT_SUFFIX = """

When you are done investigating, reply with your final report as plain text (no tool calls)."""


class DelegateTaskTool(Tool):
    name: str = DELEGATE_TASK_TOOL_NAME
    description: str = (
        "Delegate a self-contained investigation task to a sub-agent that runs in parallel "
        "with other sub-agents and has its own fresh context window. The sub-agent has the "
        "same investigation tools as you (except delegation) but sees ONLY the prompt you "
        "give it. Returns the sub-agent's final report. To run several sub-agents in "
        "parallel, request multiple delegate_task calls in a single response."
    )
    parameters: Dict[str, ToolParameter] = {
        "description": ToolParameter(
            description="Short (3-7 word) human-readable label for this task, e.g. 'Investigate checkout-api crashloop'",
            type="string",
            required=True,
        ),
        "prompt": ToolParameter(
            description=(
                "Complete instructions for the sub-agent. Must be fully self-contained: include exact "
                "resource names, namespaces, time ranges and any already-established facts it needs, "
                "and state precisely what it must report back (root cause, exact error messages, evidence)."
            ),
            type="string",
            required=True,
        ),
    }

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        # Imported lazily to avoid a circular import: tool_calling_llm and
        # prompt-building modules transitively import holmes.core.tools.
        from holmes.core.prompt import PromptComponent, build_system_prompt
        from holmes.core.tool_calling_llm import ToolCallingLLM

        prompt = params.get("prompt", "")
        if not prompt or not prompt.strip():
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="delegate_task requires a non-empty 'prompt' describing the task",
                params=params,
            )

        if context.tool_executor is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Sub-agent delegation is unavailable in this execution context",
                params=params,
            )

        # The child gets the parent's tools minus delegation itself, capping
        # delegation depth at 1 and preventing runaway recursive fan-out.
        child_executor = context.tool_executor.clone_without_tools(
            [DELEGATE_TASK_TOOL_NAME]
        )

        # Pass only enabled toolsets: the rendered toolset instructions otherwise
        # include a long "disabled & failed toolsets" listing that is useless to
        # a sub-agent and is paid per child per iteration.
        system_prompt = build_system_prompt(
            toolsets=child_executor.enabled_toolsets,
            skills=None,
            system_prompt_additions=None,
            cluster_name=None,
            ask_user_enabled=False,
            prompt_component_overrides={
                PromptComponent.TODOWRITE_INSTRUCTIONS: False,
                PromptComponent.STYLE_GUIDE: False,
            },
        )

        messages = [
            {"role": "system", "content": SUBAGENT_PREAMBLE + (system_prompt or "")},
            {"role": "user", "content": prompt + SUBAGENT_REPORT_SUFFIX},
        ]

        child = ToolCallingLLM(
            tool_executor=child_executor,
            max_steps=SUBAGENT_MAX_STEPS,
            llm=context.llm,
            tool_results_dir=None,
        )

        try:
            result = child.call(
                messages=messages,
                request_context=context.request_context,
            )
        except Exception as e:
            logger.error("Sub-agent task failed", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Sub-agent failed while executing the task: {e}",
                params=params,
            )

        usage = {
            field: getattr(result, field)
            for field in RequestStats.model_fields
            if getattr(result, field, None) is not None
        }

        if not result.result or not result.result.strip():
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                error="Sub-agent finished without producing a report",
                params=params,
                llm_usage=usage,
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=result.result,
            params=params,
            llm_usage=usage,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"Sub-agent: {params.get('description', 'investigation task')}"


class SubAgentToolset(Toolset):
    """Toolset that lets the LLM delegate focused tasks to parallel sub-agents."""

    def __init__(self):
        super().__init__(
            name="subagent",
            description=(
                "Delegate focused investigation tasks to sub-agents that run in "
                "parallel, each with its own context window"
            ),
            enabled=bool(load_bool(SUBAGENTS_ENABLED_ENV_VAR, False)),
            experimental=True,
            tools=[DelegateTaskTool()],
            tags=[ToolsetTag.CORE],
        )
        self._reload_instructions()

    def _reload_instructions(self):
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "subagent_instructions.jinja2"
        )

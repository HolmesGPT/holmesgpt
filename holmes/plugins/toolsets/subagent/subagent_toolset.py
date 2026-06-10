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
from typing import Any, Dict

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

# Max agentic iterations for a single sub-agent. Sub-agent tasks are focused,
# so they need far fewer steps than a full investigation.
SUBAGENT_MAX_STEPS = int(os.environ.get("HOLMES_SUBAGENT_MAX_STEPS", "40"))

SUBAGENT_PREAMBLE = """You are a sub-agent investigator working for a lead investigation agent.
The lead agent delegated one focused task to you. You do NOT see the original user question or the lead agent's conversation — your task description below is your entire context.

Rules:
* Investigate the task thoroughly using your tools before answering. Never answer from assumption when a tool can verify.
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

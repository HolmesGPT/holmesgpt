"""Sub-agent delegation toolset.

Exposes a `delegate_task` tool that runs a focused child investigation in its
own agentic loop (ToolCallingLLM) with its own context window. When the LLM
issues several `delegate_task` calls in a single response, the existing tool
ThreadPoolExecutor runs them concurrently, so independent sub-investigations
proceed in parallel.

The child loop gets the same tools as the parent minus `delegate_task` itself
(delegation depth is capped at 1), runs on a lean sub-agent system prompt, and
reports its final answer back as the tool result. LLM usage incurred by the
child is propagated via StructuredToolResult.llm_usage so the parent request's
cost/token stats stay accurate.

Sub-agents can run on a different (typically faster/cheaper) model than the
lead agent: configure `model` (plus optional `api_key`/`api_base`/`api_version`,
which default to the lead agent's connection) in the toolset config, or set
HOLMES_SUBAGENT_MODEL. The lead model orchestrates and synthesizes; worker
models grind through per-target evidence in parallel.
"""

import logging
import os
import threading
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, PrivateAttr

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
SUBAGENT_MODEL_ENV_VAR = "HOLMES_SUBAGENT_MODEL"

# Max agentic iterations for a single sub-agent. Sub-agent tasks are focused,
# so they need far fewer steps than a full investigation.
SUBAGENT_MAX_STEPS = int(os.environ.get("HOLMES_SUBAGENT_MAX_STEPS", "40"))


class SubAgentConfig(BaseModel):
    """Optional configuration for the subagent toolset.

    When `model` is set, sub-agents run on that model instead of the lead
    agent's model. Connection parameters default to the lead agent's, so a
    different model on the same provider (e.g. a faster Claude tier) needs
    only `model`.
    """

    model_config = ConfigDict(extra="allow")

    model: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    api_version: Optional[str] = None


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


def build_subagent_system_prompt(child_executor: Any) -> str:
    """Build a lean system prompt for a sub-agent.

    Deliberately skips the full generic_ask scaffolding (intro, general
    investigation methodology, permission-error procedures, style guide):
    a sub-agent receives a focused, self-contained task from the lead agent,
    and that boilerplate is paid per child per iteration. What it does need
    is how to operate its tools: the global logs-tooling guidance plus each
    enabled toolset's own instructions.
    """
    # Imported lazily to avoid a circular import at plugin load time.
    from holmes.plugins.prompts import load_and_render_prompt

    sections = [SUBAGENT_PREAMBLE]

    enabled = list(getattr(child_executor, "enabled_toolsets", []) or [])
    try:
        logs_guidance = load_and_render_prompt(
            "builtin://_fetch_logs.jinja2", {"toolsets": enabled}
        )
        if logs_guidance and logs_guidance.strip():
            sections.append(logs_guidance.strip())
    except Exception:
        logger.warning("Failed to render logs guidance for sub-agent", exc_info=True)

    toolset_sections = [
        f"## {ts.name}\n{ts.llm_instructions.strip()}"
        for ts in enabled
        if getattr(ts, "llm_instructions", None) and ts.llm_instructions.strip()
    ]
    if toolset_sections:
        sections.append("# Toolset instructions\n\n" + "\n\n".join(toolset_sections))

    return "\n\n".join(sections)


class DelegateTaskTool(Tool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = DELEGATE_TASK_TOOL_NAME
    description: str = (
        "Delegate a self-contained investigation task to a sub-agent with its own fresh "
        "context window and the same investigation tools as you (except delegation). "
        "Sub-agents run concurrently when you request several delegate_task calls in a "
        "single response, and each returns a final report. The sub-agent sees ONLY the "
        "prompt you give it.\n\n"
        "Use this ONLY when the investigation exceeds what you can cover thoroughly in "
        "your own context — e.g. many targets that each require reading substantial "
        "evidence (full log histories, large data dumps), where holding everything "
        "yourself would force you to skim, sample, or drop detail. Each sub-agent is a "
        "full extra agent run with real token and latency cost: for anything you can "
        "investigate well yourself — including wide audits with modest per-target "
        "evidence — do NOT delegate; investigate directly."
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

    def _get_subagent_config(self) -> SubAgentConfig:
        raw = getattr(getattr(self, "toolset", None), "config", None)
        if isinstance(raw, SubAgentConfig):
            return raw
        if isinstance(raw, dict):
            try:
                return SubAgentConfig(**raw)
            except Exception:
                logger.warning("Invalid subagent toolset config; ignoring", exc_info=True)
        return SubAgentConfig()

    def _get_child_llm(self, context: ToolInvokeContext) -> Any:
        """Return the LLM sub-agents should run on.

        Defaults to the lead agent's LLM. When a child model is configured
        (toolset config `model` or HOLMES_SUBAGENT_MODEL), builds a DefaultLLM
        for it, inheriting the lead agent's connection parameters unless
        overridden. The instance is cached: child LLMs are stateless and
        shared safely across concurrent delegations.
        """
        cfg = self._get_subagent_config()
        child_model = cfg.model or os.environ.get(SUBAGENT_MODEL_ENV_VAR)
        if not child_model:
            return context.llm

        parent = context.llm
        if getattr(parent, "model", None) == child_model:
            return parent

        toolset = getattr(self, "toolset", None)
        cache_lock = getattr(toolset, "_child_llm_lock", None) or threading.Lock()
        with cache_lock:
            cached = getattr(toolset, "_child_llm", None) if toolset else None
            if cached is not None and getattr(cached, "model", None) == child_model:
                return cached

            from holmes.core.llm import DefaultLLM

            child_llm = DefaultLLM(
                model=child_model,
                api_key=cfg.api_key or getattr(parent, "api_key", None),
                api_base=cfg.api_base or getattr(parent, "api_base", None),
                api_version=cfg.api_version or getattr(parent, "api_version", None),
                tracer=getattr(parent, "tracer", None),
            )
            if toolset is not None:
                toolset._child_llm = child_llm
            logger.info(f"Sub-agents will run on model {child_model}")
            return child_llm

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        # Imported lazily to avoid a circular import: tool_calling_llm
        # transitively imports holmes.core.tools.
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

        system_prompt = build_subagent_system_prompt(child_executor)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt + SUBAGENT_REPORT_SUFFIX},
        ]

        try:
            child_llm = self._get_child_llm(context)
        except Exception as e:
            logger.error("Failed to construct sub-agent LLM", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to construct sub-agent LLM: {e}",
                params=params,
            )

        child = ToolCallingLLM(
            tool_executor=child_executor,
            max_steps=SUBAGENT_MAX_STEPS,
            llm=child_llm,
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

    config_classes = [SubAgentConfig]

    _child_llm: Optional[Any] = PrivateAttr(default=None)
    _child_llm_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def __init__(self):
        tool = DelegateTaskTool()
        super().__init__(
            name="subagent",
            description=(
                "Delegate focused investigation tasks to sub-agents that run in "
                "parallel, each with its own context window"
            ),
            enabled=bool(load_bool(SUBAGENTS_ENABLED_ENV_VAR, False)),
            experimental=True,
            tools=[tool],
            tags=[ToolsetTag.CORE],
        )
        # Backref so the tool can read this toolset's config (set after
        # construction by config overrides) and cache the child LLM.
        object.__setattr__(tool, "toolset", self)
        self._reload_instructions()

    def _reload_instructions(self):
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "subagent_instructions.jinja2"
        )

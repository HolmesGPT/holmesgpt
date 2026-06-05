"""Subagent (dispatch_agent / Task) tool.

This module implements a Claude Code-style subagent system: the main agent can
spawn focused child agents that share the same model and the same toolset, but
operate with an isolated context window. Each child runs an independent
agentic loop and returns only its final answer to the parent.

The whole feature is gated behind a single boolean flag: ``subagents_enabled``.
When False, the tool is not registered and the LLM never sees it. When True,
the parent ToolCallingLLM exposes ``dispatch_agent`` to the model and child
agents are constructed with ``subagents_enabled=False`` so they cannot
recursively spawn further subagents.

The subagent model can be overridden via the ``SUBAGENT_MODEL`` env var (e.g.
set it to a cheaper/faster model like Haiku while the parent runs on Opus).
When unset, the subagent uses the parent's LLM instance directly.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from holmes.core.tracing import DummySpan, SpanType
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)

DISPATCH_AGENT_TOOL_NAME = "dispatch_agent"

# Default cap on subagent agentic-loop iterations. Subagents are intended to be
# narrowly scoped, so we cap them well below the parent's typical max_steps.
# Lower cap forces the child to converge on a single answer instead of doing
# its own multi-step investigation that duplicates parent reasoning.
#
# Sized for recovery from one tool-format quirk: 1 search → empty → 1 mapping
# lookup → 1 retry → 1 follow-up → 1 final answer. iter 1A evidence showed 3
# steps was insufficient: subagent hit an ES sort-array format quirk on the
# first call, ran out of steps before it could see the mapping and retry,
# returned empty, and the parent had to redo the work — net regression.
DEFAULT_SUBAGENT_MAX_STEPS = 5

# Meta-tools the subagent should never see. TodoWrite plans multi-step
# investigations; fetch_skill loads investigation playbooks; both are overhead
# noise for a one-shot lookup. Removing them from the child executor shrinks
# the tool menu and discourages the subagent from spinning extra turns.
SUBAGENT_EXCLUDED_TOOLS = ("TodoWrite", "fetch_skill")

_REQUEST_STATS_FIELDS = (
    "total_cost",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "max_completion_tokens_per_call",
    "max_prompt_tokens_per_call",
    "num_compactions",
)


def _extract_request_stats(result: Any) -> Dict[str, Any]:
    """Pull just the RequestStats fields out of an LLMResult into a plain dict.

    LLMResult inherits from RequestStats, so all of these attributes exist.
    Returning a dict (not a RequestStats instance) avoids importing RequestStats
    in tools.py — keeps the StructuredToolResult model layering clean.
    """
    return {field: getattr(result, field, None) for field in _REQUEST_STATS_FIELDS}


# Max chars to keep from each subagent tool-call result when summarizing for
# the eval classifier. Long enough to show field selectors and small tool
# outputs (mappings, counts), short enough to avoid ballooning the judge's
# prompt with the same noise the subagent was created to absorb.
_SUBAGENT_TOOL_CALL_RESULT_CHARS = 1500


def _summarize_subagent_tool_calls(result: Any) -> Optional[List[Dict[str, Any]]]:
    """Flatten the subagent's inner tool calls into plain dicts for the parent.

    The eval classifier (property_manager.py) iterates parent-level
    `result.tool_calls` to build the "# Tool Calls" section it shows the
    judge. Without this, an eval criterion like "must use source filtering"
    can't be satisfied when the actual ES query happens inside the subagent
    — the judge only sees the dispatch_agent call and the distilled answer.

    Each entry: {description, result_summary} where result_summary is
    truncated. We use plain dicts instead of ToolCallResult to avoid an
    import cycle from tools.py.
    """
    inner = getattr(result, "tool_calls", None) or []
    if not inner:
        return None
    summarized: List[Dict[str, Any]] = []
    for tc in inner:
        result_text = str(getattr(tc, "result", "") or "")
        if len(result_text) > _SUBAGENT_TOOL_CALL_RESULT_CHARS:
            result_text = (
                result_text[:_SUBAGENT_TOOL_CALL_RESULT_CHARS] + "…[truncated]"
            )
        summarized.append({
            "description": getattr(tc, "description", "") or "",
            "tool_name": getattr(tc, "tool_name", "") or "",
            "result_summary": result_text,
        })
    return summarized


def _build_subagent_llm(parent_llm: Any) -> Any:
    """Return the LLM the subagent should use.

    Reads SUBAGENT_MODEL from env; when set, instantiates a fresh DefaultLLM
    on that model inheriting the parent's api credentials and tracer. When
    unset, returns the parent's LLM instance unchanged so the default
    behavior matches earlier iterations.
    """
    subagent_model = os.environ.get("SUBAGENT_MODEL", "").strip()
    if not subagent_model:
        return parent_llm

    from holmes.core.llm import DefaultLLM

    return DefaultLLM(
        model=subagent_model,
        api_key=getattr(parent_llm, "api_key", None),
        api_base=getattr(parent_llm, "api_base", None),
        api_version=getattr(parent_llm, "api_version", None),
        tracer=getattr(parent_llm, "tracer", None),
        name=f"subagent({subagent_model})",
    )


SUBAGENT_SYSTEM_PROMPT = (
    "You are a sub-agent. Use the fewest tool calls possible to answer. "
    "Make at least one tool call before concluding NOT FOUND — never "
    "guess from training data. "
    "For Elasticsearch/OpenSearch searches that fetch documents, use the "
    "source parameter to include only fields you need to answer the "
    "question — exclude bulky fields like http.request.body, "
    "http.response.body, error.stack_trace. This keeps your own context "
    "small. "
    "For log queries (Loki, Coralogix), pass tight time windows and use "
    "label filters to narrow the result set before scanning. "
    "Final answer: at most 2 short lines, raw facts only (no preamble, "
    "no narration, no \"based on...\", no caveats). Quote IDs, field "
    "names, and counts verbatim. If you applied source filtering or "
    "narrow label filters, say so on a second line (one short sentence). "
    "If the answer truly is not in the data after a tool call, return "
    "exactly: NOT FOUND"
)


class DispatchAgentTool(Tool):
    name: str = DISPATCH_AGENT_TOOL_NAME
    description: str = (
        "Launch a sub-agent in isolated context to extract a small answer "
        "from a tool call whose raw output would otherwise be >5k tokens. "
        "You only see the 1-3 line answer.\n\n"
        "USE THIS WHEN:\n"
        "  • Searching log lines, traces, or ES/OpenSearch documents where "
        "the response body is likely >5k tokens of mostly-irrelevant data "
        "(e.g. a full trace document, a log window with hundreds of lines, "
        "a wide search hit with embedded stack traces or HTTP payloads).\n"
        "  • You need to derive an ID, count, or short summary from such "
        "data — NOT to inspect raw fields one-by-one.\n"
        "  • Investigation involves a follow-up step after the lookup: "
        "the noisy payload would otherwise ride along in your context.\n\n"
        "RULES:\n"
        "  • Each prompt must be self-contained and end with a literal "
        'output spec, e.g. "Return only X. Nothing else."\n'
        "  • To cover many similar sources, dispatch ONCE with a wildcard "
        "tool call inside it — never fan out one dispatch per source.\n"
        "  • Do NOT dispatch when one direct tool call already gives a "
        "small result (e.g. cluster_health, list_indices, get_mapping).\n\n"
        "GOOD prompts:\n"
        '  - "Search index app-X-apm-traces for trace_id=TRACE-ABC. Return '
        'ONLY service name, error code, user_id, and timestamp. Nothing else."\n'
        '  - "Query Loki {namespace=\\"foo\\",level=\\"ERROR\\"} between '
        'T1 and T2. Return the count of distinct user_id values and the '
        'top error message. Nothing else."\n'
        '  - "Call elasticsearch_get_mapping on app-X-*. Return only the '
        'field count per index. Format: index=N, one per line."'
    )
    parameters: Dict[str, ToolParameter] = {
        "task_description": ToolParameter(
            type="string",
            required=True,
            description="A short (3-5 word) label describing the subtask, used for logging.",
        ),
        "prompt": ToolParameter(
            type="string",
            required=True,
            description=(
                "The full task prompt for the subagent. Must be self-contained "
                "because the subagent does NOT see your conversation history. "
                "Include all relevant context, constraints, and what you want back."
            ),
        ),
    }

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        # Late import to avoid a circular dependency: tool_calling_llm imports
        # tools.py, which is loaded before this module.
        from holmes.core.tool_calling_llm import ToolCallingLLM

        # The JSON Schema declares both fields as strings, but the schema
        # coercer only converts FROM string to other types — not the reverse —
        # so a misbehaving model could still hand us a number/array here.
        # Reject explicitly rather than crashing in .strip().
        raw_task_description = params.get("task_description", "subagent task")
        raw_prompt = params.get("prompt", "")

        if raw_task_description is not None and not isinstance(raw_task_description, str):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="dispatch_agent 'task_description' must be a string.",
                params=params,
            )
        if not isinstance(raw_prompt, str):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="dispatch_agent 'prompt' must be a string.",
                params=params,
            )

        task_description = (raw_task_description or "subagent task").strip() or "subagent task"
        prompt = raw_prompt.strip()

        if not prompt:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="dispatch_agent requires a non-empty 'prompt' parameter.",
                params=params,
            )

        parent_agent = context.parent_agent
        if parent_agent is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    "dispatch_agent invoked without a parent agent reference. "
                    "Subagents are not enabled in this run."
                ),
                params=params,
            )

        # Defense in depth: refuse to dispatch if the calling agent itself does
        # not have subagents enabled. This catches cases where dispatch_agent
        # somehow leaks into a child's tool list (e.g. via a shared executor)
        # and prevents recursive subagent spawning.
        if not getattr(parent_agent, "subagents_enabled", False):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    "dispatch_agent cannot be invoked from a subagent. Only the "
                    "top-level agent is allowed to spawn subagents."
                ),
                params=params,
            )

        logging.info(
            f"[subagent] dispatching '{task_description}' "
            f"(prompt length={len(prompt)} chars)"
        )

        child_max_steps = min(
            DEFAULT_SUBAGENT_MAX_STEPS, getattr(parent_agent, "max_steps", DEFAULT_SUBAGENT_MAX_STEPS)
        )

        # Children are spawned with subagents_enabled=False so they cannot
        # recursively dispatch further subagents. This matches the Claude Code
        # convention: only the top-level agent has the Task tool.
        # We pass the parent's *base* executor (the original, un-cloned one
        # that does not contain DispatchAgentTool) so the child's LLM cannot
        # see dispatch_agent in its tool list at all. We also strip meta-tools
        # (TodoWrite, fetch_skill) that only inflate a narrow lookup's turn count.
        base_executor = getattr(
            parent_agent, "_base_tool_executor", parent_agent.tool_executor
        )
        clone_fn = getattr(base_executor, "clone_without_tools", None)
        child_executor = (
            clone_fn(list(SUBAGENT_EXCLUDED_TOOLS)) if callable(clone_fn) else base_executor
        )
        # Allow the subagent to use a different (typically cheaper/faster) model
        # via the SUBAGENT_MODEL env var. When unset, the subagent reuses the
        # parent's LLM instance directly. This keeps the default behavior
        # unchanged while enabling experiments like parent=Opus, sub=Haiku.
        child_llm = _build_subagent_llm(parent_agent.llm)
        child = ToolCallingLLM(
            tool_executor=child_executor,
            max_steps=child_max_steps,
            llm=child_llm,
            tool_results_dir=getattr(parent_agent, "tool_results_dir", None),
            tracer=getattr(parent_agent, "tracer", None),
            subagents_enabled=False,
        )

        # Append an explicit output-format reminder to the parent's prompt so the
        # subagent gets the same expectation regardless of how carefully the
        # parent worded it. The system prompt already says this, but Anthropic
        # caches the system prompt; repeating it in the user prompt keeps the
        # constraint salient to the model on each invocation.
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Output spec: at most 2 short lines, plain text, raw facts only. "
            "No preamble. Quote IDs/field names verbatim. If not in the data "
            "after at least one tool call: NOT FOUND."
        )

        messages = [
            {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
            {"role": "user", "content": wrapped_prompt},
        ]

        # Nest the child's trace under the parent's tool span so Braintrust
        # shows the full call tree (parent LLM call → dispatch_agent tool span
        # → child LLM call → child's own tool spans). Fall back to DummySpan
        # when no tracer is active.
        parent_span = getattr(context, "trace_span", None) or DummySpan()
        try:
            with parent_span.start_span(
                name=f"holmesgpt.subagent.{task_description[:32]}",
                type=SpanType.TASK.value,
            ) as child_span:
                child_span.log(
                    input={"task_description": task_description, "prompt": prompt},
                    metadata={"subagent_max_steps": child_max_steps},
                )
                result = child.call(
                    messages=messages,
                    request_context=context.request_context,
                    trace_span=child_span,
                )
                child_span.log(
                    output=(result.result or "")[:4000],
                    metadata={
                        "num_llm_calls": result.num_llm_calls,
                        "total_tokens": result.total_tokens,
                        "total_cost": result.total_cost,
                    },
                )
        except Exception as e:
            logging.exception(f"[subagent] '{task_description}' failed")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Subagent failed: {e}",
                params=params,
            )

        answer = (result.result or "").strip()
        subagent_tool_calls = _summarize_subagent_tool_calls(result)
        if not answer:
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                error="Subagent finished without producing a final answer.",
                params=params,
                subagent_stats=_extract_request_stats(result),
                subagent_num_llm_calls=result.num_llm_calls,
                subagent_tool_calls=subagent_tool_calls,
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=answer,
            params=params,
            subagent_stats=_extract_request_stats(result),
            subagent_num_llm_calls=result.num_llm_calls,
            subagent_tool_calls=subagent_tool_calls,
        )

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        task = params.get("task_description") or "subtask"
        return f"Dispatch subagent: {task}"


class DispatchAgentToolset(Toolset):
    """Toolset providing the dispatch_agent tool for spawning focused subagents."""

    def __init__(self) -> None:
        super().__init__(
            name="subagent",
            description=(
                "Spawn focused subagents that share the main agent's model and tools "
                "but have isolated context windows."
            ),
            enabled=True,
            tools=[DispatchAgentTool()],
            tags=[ToolsetTag.CORE],
        )

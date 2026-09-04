"""Deterministic proof that code mode reduces the number of tokens a tool's
output contributes to the LLM context.

This does NOT depend on an LLM choosing to use code mode, on a Kubernetes
cluster, or on any network call. It measures the exact quantity that drives
Holmes's input-token cost: how many tokens the ``{"role": "tool", ...}`` message
adds to the conversation, using the SAME formatter (``format_tool_result_data``)
and the SAME tokenizer (``litellm.token_counter``) the product uses to size and
spill tool results.

For an identical underlying dataset and question we compare two paths:

* **classic** — the tool is invoked directly, so its full result is appended to
  the context (and re-sent on every subsequent agentic step).
* **code mode** — a script calls the same tool(s) inside the subprocess, filters
  in Python, and ``print``s only the answer; only that summary is appended.

Two token sinks are covered:

1. *result filtering* — one tool returns a large payload; only a summary returns.
2. *call consolidation* — N sub-calls collapse into a single tool result.
"""

import json
from typing import List

import litellm
import pytest

from holmes.core.models import format_tool_result_data
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    Toolset,
    ToolsetStatusEnum,
    ToolsetTag,
)
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.toolsets.code_execution.code_execution_toolset import (
    CodeExecutionToolset,
)
from tests.conftest import create_mock_tool_invoke_context

pytestmark = pytest.mark.xdist_group("code_execution")

# Real tokenizer, evaluated offline (tiktoken); no API call.
_TOKENIZER_MODEL = "gpt-4o"

# Deterministic dataset knobs (no RNG so the proof is reproducible).
_NUM_EVENTS = 6000
_ERROR_EVERY = 37  # every Nth event is an ERROR
_SERVICES = ["checkout", "payment", "inventory", "search", "profile"]


def _expected_error_count() -> int:
    return sum(1 for i in range(_NUM_EVENTS) if i % _ERROR_EVERY == 0)


def _make_event(i: int) -> dict:
    return {
        "id": i,
        "service": _SERVICES[i % len(_SERVICES)],
        "level": "ERROR" if i % _ERROR_EVERY == 0 else "INFO",
        "region": "us-east-1" if i % 2 == 0 else "eu-west-1",
        "latency_ms": 20 + (i % 400),
        "message": f"request {i} processed by worker-{i % 16} on shard-{i % 8}",
    }


class ListEventsTool(Tool):
    """Returns a large list of event records (no server-side filtering) — the
    kind of raw payload that floods context when returned directly."""

    name: str = "list_events"
    description: str = "Return the full event log as a JSON list."

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        n = int(params.get("n", _NUM_EVENTS))
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=[_make_event(i) for i in range(n)],
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "list_events"


class ShardReportTool(Tool):
    """Returns a moderate per-shard report; used to show call consolidation."""

    name: str = "shard_report"
    description: str = "Return a report for a single shard."

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        shard = int(params.get("shard", 0))
        rows = [
            {
                "shard": shard,
                "key": f"metric_{shard}_{j}",
                "value": (shard * 1000 + j) % 997,
                "note": f"sample row {j} for shard {shard} with some padding text",
            }
            for j in range(120)
        ]
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS, data=rows
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "shard_report"


def _wired(tools: List[Tool]) -> CodeExecutionToolset:
    code_ts = CodeExecutionToolset()
    code_ts.prerequisites_callable({})
    code_ts.status = ToolsetStatusEnum.ENABLED

    data_ts = Toolset(
        name="events_data",
        description="events data toolset",
        tools=tools,
        tags=[ToolsetTag.CORE],
        enabled=True,
    )
    data_ts.status = ToolsetStatusEnum.ENABLED

    executor = ToolExecutor(toolsets=[code_ts, data_ts])
    code_ts.set_tool_executor(executor)
    return code_ts


def _context_tokens(result: StructuredToolResult) -> int:
    """Tokens the tool result contributes to the LLM context — exactly how
    Holmes measures it (format_tool_result_data + litellm.token_counter)."""
    content = format_tool_result_data(
        tool_result=result, tool_call_id="call_x", tool_name="t"
    )
    return litellm.token_counter(
        model=_TOKENIZER_MODEL, messages=[{"role": "tool", "content": content}]
    )


def _run_code(code_ts: CodeExecutionToolset, code: str) -> StructuredToolResult:
    tool = code_ts.tools[0]
    ctx = create_mock_tool_invoke_context(tool_name="run_python_code")
    return tool.invoke({"code": code, "timeout": 30}, ctx)


def test_result_filtering_cuts_context_tokens(capsys):
    """A tool returns 6000 events; the question is 'how many are ERROR?'.

    Classic: the whole 6000-event list enters context.
    Code mode: only 'error_count <N>' enters context.
    """
    code_ts = _wired([ListEventsTool()])
    expected = _expected_error_count()

    # classic: direct tool call → full payload in context
    raw = ListEventsTool()._invoke(
        {"n": _NUM_EVENTS}, create_mock_tool_invoke_context(tool_name="list_events")
    )
    classic_tokens = _context_tokens(raw)

    # code mode: filter in Python, print only the answer
    codemode = _run_code(
        code_ts,
        "import json\n"
        "events = json.loads(holmes.list_events(n=%d))\n"
        "n = sum(1 for e in events if e['level'] == 'ERROR')\n"
        "print('error_count', n)\n" % _NUM_EVENTS,
    )
    codemode_tokens = _context_tokens(codemode)

    # correctness: code mode computed the right answer from the same data
    assert codemode.status == StructuredToolResultStatus.SUCCESS
    assert f"error_count {expected}" in codemode.data

    reduction = 100 * (1 - codemode_tokens / classic_tokens)
    with capsys.disabled():
        print(
            f"\n[result filtering] classic={classic_tokens:,} tokens  "
            f"code_mode={codemode_tokens:,} tokens  "
            f"reduction={reduction:.2f}%  (answer: {expected} errors)"
        )

    # The raw payload is thousands of tokens; the summary is a handful.
    assert classic_tokens > 10_000
    assert codemode_tokens < classic_tokens * 0.02  # >=98% reduction
    # And the raw records genuinely never entered context in code mode.
    assert "latency_ms" not in (codemode.data or "")


def test_call_consolidation_cuts_context_tokens(capsys):
    """8 sub-calls inside one script produce ONE tool result, versus 8 separate
    tool results in the classic path."""
    code_ts = _wired([ShardReportTool()])
    n_shards = 8

    # classic: 8 separate tool results, each appended to context
    classic_total = 0
    for shard in range(n_shards):
        r = ShardReportTool()._invoke(
            {"shard": shard},
            create_mock_tool_invoke_context(tool_name="shard_report"),
        )
        classic_total += _context_tokens(r)

    # code mode: one script fans out to all shards, prints only the aggregate
    codemode = _run_code(
        code_ts,
        "import json\n"
        "total = 0\n"
        "for s in range(%d):\n"
        "    rows = json.loads(holmes.shard_report(shard=s))\n"
        "    total += sum(r['value'] for r in rows)\n"
        "print('sum_of_values', total)\n" % n_shards,
    )
    codemode_tokens = _context_tokens(codemode)

    assert codemode.status == StructuredToolResultStatus.SUCCESS
    assert "sum_of_values" in codemode.data
    assert f"executed {n_shards} tool call(s)" in codemode.data

    reduction = 100 * (1 - codemode_tokens / classic_total)
    with capsys.disabled():
        print(
            f"\n[call consolidation] classic({n_shards} results)={classic_total:,} "
            f"tokens  code_mode(1 result)={codemode_tokens:,} tokens  "
            f"reduction={reduction:.2f}%"
        )

    assert codemode_tokens < classic_total * 0.1  # >=90% reduction

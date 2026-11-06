"""Helpers for the OpenUI visualization evals.

The Robusta UI sends a ``VisualizeOpenUI`` frontend tool with every chat
request; the model answers with an openui-lang program in its ``code``
argument and the UI renders it. These evals send the same tool definition and
check the program three ways:

1. The frontend's checker (parser + headless render against the tool results
   of this run) says whether the program is valid and what it draws.
2. The ``openui:`` block of the test case states properties the program must
   have (a visualization at all, a bound control, a Chart).
3. The LLM judge reads a summary of the visualization next to the answer.

Both artifacts come from the frontend deploy, so an eval tests exactly what is
deployed: ``/api/frontend-tools.json`` and ``/api/openui-check.mjs`` on
production, or on a PR's Netlify preview via ``--frontend-tools-url`` /
``--openui-check-url``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from holmes.core.models import FrontendToolDefinition

OPENUI_TOOL_NAME = "VisualizeOpenUI"
CONTROL_COMPONENTS = ("Select", "Input", "Switch", "Button")

# TEMPORARY, revert before merge: production does not publish these files yet, so
# the first cloud runs read them from the robusta-frontend PR #3892 deploy preview.
# Hard-coded rather than set through repository variables so that every trigger
# (label, /eval comment, workflow_dispatch) uses them without any repository setup.
DEFAULT_FRONTEND_TOOLS_URL = "https://deploy-preview-3892--robusta.netlify.app/api/frontend-tools.json"
DEFAULT_OPENUI_CHECK_URL = "https://deploy-preview-3892--robusta.netlify.app/api/openui-check.mjs"


def resolve_frontend_tools_url(option: Optional[str] = None) -> str:
    """CLI option, then FRONTEND_TOOLS_URL (how CI points at a deploy preview), then production."""
    return option or os.environ.get("FRONTEND_TOOLS_URL") or DEFAULT_FRONTEND_TOOLS_URL


def resolve_check_url(option: Optional[str] = None) -> str:
    return option or os.environ.get("OPENUI_CHECK_URL") or DEFAULT_OPENUI_CHECK_URL


_CHECKER_CACHE: Dict[str, Path] = {}


# ---------------------------------------------------------------------------
# Artifacts published by the frontend
# ---------------------------------------------------------------------------


def fetch_frontend_tools(url: str) -> List[FrontendToolDefinition]:
    """The tool definitions as the UI sends them (``/api/frontend-tools.json``)."""
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    payload = response.json()
    tools = payload.get("tools") if isinstance(payload, dict) else payload
    if not isinstance(tools, list) or not tools:
        raise ValueError(f"{url} did not return a non-empty 'tools' list")
    definitions = [FrontendToolDefinition(**tool) for tool in tools]
    logging.info(
        "frontend tools from %s (commit %s): %s",
        url,
        (payload.get("commit") or "?")[:9] if isinstance(payload, dict) else "?",
        ", ".join(t.name for t in definitions),
    )
    return definitions


def fetch_checker(url: str) -> Path:
    """Download the checker once per URL into a temp dir and return its path."""
    cached = _CHECKER_CACHE.get(url)
    if cached and cached.exists():
        return cached
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    folder = (
        Path(tempfile.gettempdir())
        / "openui-check"
        / hashlib.sha256(url.encode()).hexdigest()[:12]
    )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "openui-check.mjs"
    path.write_bytes(response.content)
    _CHECKER_CACHE[url] = path
    return path


# ---------------------------------------------------------------------------
# From the run's tool calls to the checker's input
# ---------------------------------------------------------------------------


def _tool_call_params(tool_call: Any) -> Dict[str, Any]:
    result = getattr(tool_call, "result", None)
    params = getattr(result, "params", None) if result is not None else None
    return params if isinstance(params, dict) else {}


def extract_openui_programs(tool_calls: Optional[List[Any]]) -> List[Dict[str, str]]:
    """``[{"tool_call_id", "code"}]`` for every VisualizeOpenUI call, in order."""
    programs = []
    for tc in tool_calls or []:
        if getattr(tc, "tool_name", None) != OPENUI_TOOL_NAME:
            continue
        code = _tool_call_params(tc).get("code")
        if isinstance(code, str):
            programs.append(
                {"tool_call_id": getattr(tc, "tool_call_id", ""), "code": code}
            )
    return programs


def tool_calls_to_holmes_tools(tool_calls: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """The ``HolmesTool[]`` shape the UI builds from the chat events, so a
    program's ``Query("get_tool_data", {"toolId": ...})`` resolves the same
    way it does in the browser."""
    tools = []
    for tc in tool_calls or []:
        result = getattr(tc, "result", None)
        status = getattr(result, "status", None)
        tools.append(
            {
                "id": getattr(tc, "tool_call_id", ""),
                "name": getattr(tc, "tool_name", ""),
                "output": {
                    "schema_version": "1",
                    "status": getattr(status, "value", status) or "success",
                    "data": getattr(result, "data", None),
                    "params": getattr(result, "params", None),
                    "error": getattr(result, "error", None),
                },
            }
        )
    return tools


def check_program(
    checker: Path, code: str, tools: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Run the frontend's checker on one program. Returns its JSON report."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError(
            "node is required to run the OpenUI checker but was not found on PATH"
        )
    payload = json.dumps({"code": code, "tools": tools}, default=str)
    completed = subprocess.run(
        [node, str(checker)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode == 2 or not completed.stdout.strip():
        raise RuntimeError(f"openui-check failed to run: {completed.stderr.strip()}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# Expectations
# ---------------------------------------------------------------------------


@dataclass
class OpenUIReport:
    programs: List[Dict[str, str]] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)

    @property
    def judge_block(self) -> str:
        """What the LLM judge reads instead of the raw tool-call repr."""
        if not self.programs:
            return "The LLM did not call the VisualizeOpenUI tool: no visualization was produced."
        parts = []
        for i, (program, check) in enumerate(zip(self.programs, self.checks), 1):
            parts.append(f"## Visualization {i}\n")
            parts.append(f"Valid program: {check.get('valid')}\n")
            summary = check.get("summary") or "(no summary)"
            parts.append(
                "What it draws, from the frontend's renderer:\n```\n"
                + summary
                + "\n```\n"
            )
            parts.append("Program:\n```\n" + program["code"].strip() + "\n```\n")
        return "\n".join(parts)


def _bound_control_count(check: Dict[str, Any]) -> int:
    """Controls bound to a $variable that a Query or a built-in reads."""
    tree = check.get("tree") or {}
    unused = set(tree.get("unusedStateVariables") or [])
    return sum(
        1
        for binding in tree.get("bindings") or []
        if binding.get("variable") and binding["variable"] not in unused
    )


def evaluate_openui(
    test_case: Any, tool_calls: Optional[List[Any]], checker: Path
) -> OpenUIReport:
    expectations = test_case.openui
    report = OpenUIReport(programs=extract_openui_programs(tool_calls))
    if expectations is None:
        return report

    if expectations.expect_visualization and not report.programs:
        report.failures.append("expected a VisualizeOpenUI call, the model made none")
    if not expectations.expect_visualization and report.programs:
        report.failures.append(
            f"expected no visualization, the model made {len(report.programs)} VisualizeOpenUI call(s)"
        )

    holmes_tools = tool_calls_to_holmes_tools(tool_calls)
    for i, program in enumerate(report.programs, 1):
        check = check_program(checker, program["code"], holmes_tools)
        report.checks.append(check)
        tree = check.get("tree") or {}
        if expectations.require_valid:
            if not check.get("valid"):
                report.failures.append(
                    f"visualization {i} is not valid: parse={check.get('parse')} queries={check.get('queries')} runtime={check.get('runtime')}"
                )
            for key in ("emptyCharts", "emptyTables", "emptySelects"):
                if tree.get(key):
                    report.failures.append(f"visualization {i} has {key}: {tree[key]}")
        if expectations.expect_controls is True and _bound_control_count(check) == 0:
            report.failures.append(
                f"visualization {i} has no control bound to a $variable that something reads"
            )
        if expectations.expect_controls is False and tree.get("controls", 0) > 0:
            report.failures.append(
                f"visualization {i} has {tree['controls']} control(s) but none was asked for"
            )
        for component, minimum in (expectations.min_components or {}).items():
            if (tree.get("components") or {}).get(component, 0) < minimum:
                report.failures.append(
                    f"visualization {i} has fewer than {minimum} {component}"
                )
    return report

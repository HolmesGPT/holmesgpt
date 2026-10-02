"""Code mode: let the LLM write one Python script that composes many tool
calls and filters results in code, so only what it ``print()``s enters the
model's context.

Design: ``relay/docs/design/2026-07-28_holmes-code-mode.md``.

The script runs in a subprocess with a generated ``holmes`` object whose
functions relay back to this process (over a unix socket) and dispatch into the
real ToolExecutor. Credentials never leave the parent. Only read-only tools are
exposed (see ``client_generator``); approval-gated tools must be called
directly.
"""

import itertools
import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Any, ClassVar, Dict, List, Optional, Type

from pydantic import PrivateAttr

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.code_execution.bridge import SubToolCall, ToolCallBridge
from holmes.plugins.toolsets.code_execution.client_generator import (
    build_api_reference,
    build_tools_spec,
    eligible_tool_names,
)
from holmes.plugins.toolsets.code_execution.code_execution_config import (
    CodeExecutionConfig,
)
from holmes.utils.memory_limit import check_oom_and_append_hint, get_ulimit_prefix

logger = logging.getLogger(__name__)

_RUNNER_PATH = os.path.join(os.path.dirname(__file__), "runner.py")

_TOOL_DESCRIPTION = (
    "Run a Python 3 script that gathers and FILTERS data by calling Holmes "
    "tools through the injected `holmes` object, returning only what you "
    "print(). Prefer this over many separate tool calls for multi-step "
    "investigations (e.g. list resources, filter in Python, then fetch details "
    "only for the ones that matter) — it keeps large intermediate results out "
    "of the conversation. Call tools as `holmes.<function>(param=value, ...)`; "
    "each returns the tool output as a string (use json.loads if you need "
    "structured data). A failed tool call raises holmes.HolmesToolError. Only "
    "read-only tools are available here; approval-gated tools such as bash or "
    "kubectl must be called directly, not from a script. The list of available "
    "functions is provided in this toolset's instructions."
)


# Only these parent env vars are forwarded to the untrusted subprocess.
_ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
_FALLBACK_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _build_subprocess_env(socket_path: str, user_file: str, tools_file: str) -> dict:
    """Build a minimal environment for the untrusted subprocess.

    Deliberately does NOT inherit the parent's ``os.environ``: credentials
    exposed to Holmes as environment variables (LLM/provider keys, DB
    passwords, other toolsets' secrets) must never be readable by the
    LLM-authored script. Only PATH/locale and the bridge handoff vars are
    passed through.
    """
    env = {
        "HOLMES_CODE_SOCKET": socket_path,
        "HOLMES_CODE_USER_FILE": user_file,
        "HOLMES_CODE_TOOLS": tools_file,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    for key in _ENV_PASSTHROUGH:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    env.setdefault("PATH", _FALLBACK_PATH)
    return env


def _summarize_subcalls(records: List[SubToolCall]) -> str:
    if not records:
        return ""
    lines = [f"[holmes] code mode executed {len(records)} tool call(s):"]
    for rec in records:
        marker = "ok" if rec.status == "ok" else f"error: {rec.error}"
        lines.append(
            f"  - {rec.tool_name} ({rec.elapsed_seconds}s, "
            f"{rec.output_chars} chars) [{marker}]"
        )
    return "\n".join(lines)


class RunPythonCode(Tool):
    toolset: "CodeExecutionToolset"

    def __init__(self, toolset: "CodeExecutionToolset"):
        super().__init__(
            name="run_python_code",
            description=_TOOL_DESCRIPTION,
            parameters={
                "code": ToolParameter(
                    description=(
                        "The Python 3 script to run. Access tools via the "
                        "`holmes` object, e.g. "
                        "`data = json.loads(holmes.some_tool(arg='x')); "
                        "print(len(data))`. print() only the final, filtered "
                        "result."
                    ),
                    type="string",
                    required=True,
                ),
                "timeout": ToolParameter(
                    description="Optional wall-clock timeout in seconds (default 60).",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,  # type: ignore[call-arg]
        )

    def _resolve_timeout(self, params: dict) -> int:
        config = self.toolset.config or CodeExecutionConfig()
        timeout = params.get("timeout") or config.default_timeout_seconds
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = config.default_timeout_seconds
        return max(1, min(timeout, config.max_timeout_seconds))

    def _make_dispatch(self, context: ToolInvokeContext, executor, allowed: set):
        counter = itertools.count()

        def dispatch(tool_name: str, tool_params: dict) -> dict:
            if tool_name not in allowed:
                return {
                    "status": "error",
                    "error": (
                        f"tool '{tool_name}' is not available in code mode "
                        "(only read-only tools are; call approval-gated tools directly)"
                    ),
                }
            init_error = executor.ensure_toolset_initialized(tool_name)
            if isinstance(init_error, str):
                return {"status": "error", "error": init_error}
            tool = executor.get_tool_by_name(tool_name)
            if tool is None:
                return {"status": "error", "error": f"unknown tool '{tool_name}'"}

            sub_context = ToolInvokeContext(
                tool_number=context.tool_number,
                user_approved=False,
                llm=context.llm,
                max_token_count=context.max_token_count,
                tool_name=tool_name,
                tool_call_id=f"{context.tool_call_id}:code:{next(counter)}",
                session_approved_prefixes=context.session_approved_prefixes,
                request_context=context.request_context,
            )
            result = tool.invoke(tool_params, sub_context)
            if result.status == StructuredToolResultStatus.APPROVAL_REQUIRED:
                return {
                    "status": "error",
                    "error": (
                        f"tool '{tool_name}' requires approval and cannot run from "
                        "code mode; call it directly instead"
                    ),
                }
            data, _ = result.stringify_data(compact=True)
            status = (
                "ok"
                if result.status
                in (
                    StructuredToolResultStatus.SUCCESS,
                    StructuredToolResultStatus.NO_DATA,
                )
                else "error"
            )
            return {"status": status, "data": data, "error": result.error}

        return dispatch

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        code = params.get("code")
        if not code or not isinstance(code, str):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="The 'code' parameter is required and must be a string.",
                params=params,
            )

        # Prefer the request-scoped executor from the invoke context (set by the
        # agentic loop) over the toolset's wired reference, so concurrent
        # requests never share/overwrite each other's executor.
        executor = getattr(context, "tool_executor", None) or self.toolset._tool_executor
        if executor is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    "Code mode is unavailable: the tool executor was not wired "
                    "into the code_execution toolset."
                ),
                params=params,
            )

        timeout = self._resolve_timeout(params)
        allowed = eligible_tool_names(executor)
        tools_spec = build_tools_spec(executor)
        records: List[SubToolCall] = []

        work_dir = tempfile.mkdtemp(prefix="holmes_code_")
        socket_path = os.path.join(work_dir, "bridge.sock")
        user_file = os.path.join(work_dir, "user_code.py")
        tools_file = os.path.join(work_dir, "tools.json")
        stdout_path = os.path.join(work_dir, "stdout.txt")

        try:
            with open(user_file, "w") as fh:
                fh.write(code)
            with open(tools_file, "w") as fh:
                json.dump(tools_spec, fh)

            env = _build_subprocess_env(socket_path, user_file, tools_file)
            cmd = get_ulimit_prefix() + f"python3 {shlex.quote(_RUNNER_PATH)}"

            timed_out = False
            with ToolCallBridge(
                dispatch=self._make_dispatch(context, executor, allowed),
                socket_path=socket_path,
            ) as bridge, open(stdout_path, "wb") as out:
                process = subprocess.Popen(
                    cmd,
                    shell=True,
                    executable="/bin/bash",
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=env,
                    cwd=work_dir,
                )
                deadline = time.monotonic() + timeout
                bridge.serve_until_exit(process, deadline)
                if process.poll() is None:
                    timed_out = True
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.warning("code-mode subprocess did not die after kill")
                return_code = process.returncode
                records = bridge.records

            with open(stdout_path, "r", errors="replace") as fh:
                stdout = fh.read().strip()
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        return self._build_result(
            params, code, stdout, return_code, timed_out, timeout, records
        )

    def _build_result(
        self,
        params: dict,
        code: str,
        stdout: str,
        return_code: Optional[int],
        timed_out: bool,
        timeout: int,
        records: List[SubToolCall],
    ) -> StructuredToolResult:
        invocation = code if len(code) <= 400 else code[:399] + "…"
        summary = _summarize_subcalls(records)
        body = f"{summary}\n\n{stdout}".strip() if summary else stdout

        if timed_out:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Code execution timed out after {timeout} seconds.",
                data=body or None,
                params=params,
                invocation=invocation,
            )

        stdout = check_oom_and_append_hint(stdout, return_code)
        body = f"{summary}\n\n{stdout}".strip() if summary else stdout

        if return_code == 0:
            status = (
                StructuredToolResultStatus.SUCCESS
                if body
                else StructuredToolResultStatus.NO_DATA
            )
            return StructuredToolResult(
                status=status,
                data=body or None,
                params=params,
                invocation=invocation,
                return_code=return_code,
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=(
                f"The script exited with code {return_code}. See output for the "
                "traceback; fix the script and try again."
            ),
            data=body or None,
            params=params,
            invocation=invocation,
            return_code=return_code,
        )

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        code = params.get("code") or ""  # tolerate {"code": null}
        first_line = code.strip().splitlines()[0] if code.strip() else ""
        return first_line[:200] + ("…" if len(first_line) > 200 else "")


class CodeExecutionToolset(Toolset):
    config_classes: ClassVar[list[Type[CodeExecutionConfig]]] = [CodeExecutionConfig]
    config: Optional[CodeExecutionConfig] = None
    _tool_executor: Any = PrivateAttr(default=None)

    def __init__(self):
        super().__init__(
            name="code_execution",
            enabled=False,  # opt-in; off by default (feature flagged)
            description=(
                "Code mode: run a Python script that composes read-only Holmes "
                "tools and filters results in code, cutting token cost on "
                "multi-step investigations."
            ),
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/code-execution/",
            icon_url="https://raw.githubusercontent.com/Templarian/MaterialDesign/master/svg/language-python.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[RunPythonCode(self)],
            tags=[ToolsetTag.CORE],
        )
        # Never expose the code-mode toolset itself remotely or recursively.
        self._is_core = True

    def prerequisites_callable(self, config: dict[str, Any]) -> tuple[bool, str]:
        self.config = CodeExecutionConfig(**(config or {}))
        if shutil.which("python3") is None:
            return False, "python3 is not available on PATH"
        return True, ""

    def set_tool_executor(self, tool_executor) -> None:
        """Generic hook (called by ToolCallingLLM) giving code mode a
        back-reference to the ToolExecutor so it can dispatch sibling tools,
        and refreshing the API reference shown to the model."""
        self._tool_executor = tool_executor
        try:
            self.llm_instructions = self._render_instructions()
        except Exception:
            logger.warning("failed to render code_execution instructions", exc_info=True)

    def _render_instructions(self) -> str:
        api = (
            build_api_reference(self._tool_executor)
            if self._tool_executor is not None
            else ""
        )
        return (
            "## Code mode (`run_python_code`)\n"
            "For multi-step data gathering, prefer writing ONE Python script that "
            "calls tools through the `holmes` object and filters results in code, "
            "so only what you `print()` returns to the conversation. This avoids "
            "flooding the context with raw tool output across many turns.\n\n"
            "Rules:\n"
            "- Call tools as `holmes.<function>(param=value, ...)`; each returns the "
            "tool's output as a string. Use `json.loads(...)` for structured data.\n"
            "- A failed tool call raises `holmes.HolmesToolError` (catch it if useful).\n"
            "- `print()` only the final, filtered result — never raw dumps.\n"
            "- Only the read-only functions listed below are available. Approval-gated "
            "tools (e.g. bash, kubectl) are NOT callable here; use direct tool calls for those.\n\n"
            f"{api}"
        )

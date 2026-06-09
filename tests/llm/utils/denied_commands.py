"""Utilities for extracting bash commands that HolmesGPT was denied from running.

During evals the bash toolset is configured with an allow/deny list (see
``tests/llm/utils/default_toolsets.yaml``) and there is no interactive approver,
so any command the LLM attempts that is not pre-approved is effectively denied:

* Commands matching the deny list or a hard-coded block come back with an
  ``ERROR`` status and a "Command blocked..." / "Invalid prefix..." message.
* Commands that would normally require interactive approval come back as
  ``APPROVAL_REQUIRED`` and are then converted to an ``ERROR`` with a
  "rejected for security reasons" message because no approver is available.

This module pulls those commands out of an ``LLMResult`` so they can be surfaced
in the eval report (and verified by tests).
"""

from typing import Any, List

from holmes.core.tools import StructuredToolResultStatus

# Substrings that mark a bash tool ERROR result as a denial rather than a command
# that actually ran and exited non-zero. Kept in sync with the messages produced by
# RunBashCommand._build_deny_error_message and the non-interactive approval rejection
# in ToolCallingLLM._call_stream.
_DENY_ERROR_MARKERS = (
    "Command blocked",  # deny list / hard-coded block
    "Invalid prefix",  # prefix not present in command
    "requires approval",  # approval needed but not granted
    "rejected for security reasons",  # approval-required tool denied in non-interactive mode
)


def _is_denied_result(result: Any) -> bool:
    """Return True if a StructuredToolResult represents a denied bash command."""
    status = getattr(result, "status", None)
    # No interactive approver exists in evals, so approval-required == denied.
    if status == StructuredToolResultStatus.APPROVAL_REQUIRED:
        return True
    if status == StructuredToolResultStatus.ERROR:
        error = getattr(result, "error", None) or ""
        return any(marker in error for marker in _DENY_ERROR_MARKERS)
    return False


def extract_denied_commands(tool_calls: Any) -> List[str]:
    """Return the bash command strings that were denied during a Holmes run.

    Args:
        tool_calls: The ``tool_calls`` list from an ``LLMResult`` (a list of
            ``ToolCallResult``). Safe to pass ``None``.

    Returns:
        The denied command strings, in the order they were attempted.
    """
    denied: List[str] = []
    if not tool_calls:
        return denied
    for tc in tool_calls:
        if getattr(tc, "tool_name", None) != "bash":
            continue
        result = getattr(tc, "result", None)
        if result is None or not _is_denied_result(result):
            continue
        params = getattr(result, "params", None) or {}
        command = getattr(result, "invocation", None) or params.get("command") or getattr(tc, "description", None)
        if command:
            denied.append(str(command))
    return denied

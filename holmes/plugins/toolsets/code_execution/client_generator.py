"""Build the ``holmes`` code-mode client surface from a ToolExecutor.

Two products, both derived from the same eligibility filter:

- ``build_tools_spec`` — the ``[{"attr", "name"}]`` list handed to the
  subprocess runner so it can expose one function per allowed tool.
- ``build_api_reference`` — human/LLM-readable documentation of the available
  ``holmes.<attr>(...)`` functions, injected into the system prompt via the
  toolset's ``llm_instructions``.

Eligibility (v1, deliberately conservative — see the design doc
``relay/docs/design/2026-07-28_holmes-code-mode.md``): read-only tools only.
A tool is excluded when its toolset is internal agent-machinery (``is_core``),
is an approval/mutation surface (bash, kubectl_run), the code_execution toolset
itself (no recursion), or the tool matches an ``approval_required_tools``
pattern. A synchronous script cannot pause for interactive approval, so
approval-gated tools are never exposed here; the model must call those directly.
"""

import fnmatch
import keyword
import re
from typing import TYPE_CHECKING, List, Tuple

from holmes.core.tools import Tool

if TYPE_CHECKING:
    from holmes.core.tools_utils.tool_executor import ToolExecutor

# Toolsets that expose approval-gated or mutating surfaces, or the code-mode
# toolset itself. Excluded from the code-mode client regardless of config.
EXCLUDED_TOOLSET_NAMES = frozenset({"bash", "kubectl_run", "code_execution"})


def _sanitize_attr(name: str, taken: set) -> str:
    """Turn a tool name into a valid, unique Python attribute name."""
    attr = re.sub(r"\W", "_", name)
    if not attr or attr[0].isdigit():
        attr = f"t_{attr}"
    if keyword.iskeyword(attr):
        attr = f"{attr}_"
    base = attr
    i = 2
    while attr in taken:
        attr = f"{base}_{i}"
        i += 1
    taken.add(attr)
    return attr


def _is_eligible(tool: Tool, toolset) -> bool:
    if toolset is None:
        return False
    if getattr(toolset, "is_core", False):
        return False
    if toolset.name in EXCLUDED_TOOLSET_NAMES:
        return False
    real_name = getattr(tool, "mcp_tool_name", "") or tool.name
    for pattern in getattr(toolset, "approval_required_tools", []) or []:
        if fnmatch.fnmatch(real_name, pattern):
            return False
    return True


def eligible_tools(executor: "ToolExecutor") -> List[Tuple[str, Tool]]:
    """Return ``[(attr, tool)]`` for every tool exposable in code mode."""
    result: List[Tuple[str, Tool]] = []
    taken: set = set()
    for name, tool in sorted(executor.tools_by_name.items()):
        toolset = executor._tool_to_toolset.get(name)
        if not _is_eligible(tool, toolset):
            continue
        result.append((_sanitize_attr(name, taken), tool))
    return result


def eligible_tool_names(executor: "ToolExecutor") -> set:
    """The set of real tool names dispatchable from code mode (defense in depth)."""
    return {tool.name for _, tool in eligible_tools(executor)}


def build_tools_spec(executor: "ToolExecutor") -> List[dict]:
    return [{"attr": attr, "name": tool.name} for attr, tool in eligible_tools(executor)]


def _one_line(text: str, limit: int = 200) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _signature(tool: Tool) -> str:
    required = [p for p, spec in tool.parameters.items() if spec.required]
    optional = [p for p, spec in tool.parameters.items() if not spec.required]
    parts = list(required) + [f"{p}=None" for p in optional]
    return ", ".join(parts)


def build_api_reference(executor: "ToolExecutor") -> str:
    """Markdown listing of the available ``holmes.<attr>(...)`` functions."""
    tools = eligible_tools(executor)
    if not tools:
        return (
            "No read-only tools are currently available for code mode. "
            "Use direct tool calls instead."
        )
    lines = ["Available functions on the `holmes` object:"]
    for attr, tool in tools:
        lines.append(f"- `holmes.{attr}({_signature(tool)})` — {_one_line(tool.description)}")
    return "\n".join(lines)

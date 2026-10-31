"""Inspect a single HolmesGPT toolset: status, tools, and redacted config."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from pydantic import SecretStr
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from holmes.core.tools import Tool, ToolParameter, Toolset, ToolsetStatusEnum

_SECRET_KEY_MARKERS = (
    "password",
    "token",
    "api_key",
    "apikey",
    "secret",
    "authorization",
    "credential",
    "private_key",
    "bearer",
    "cookie",
    "access_key",
    "auth_config",
    "passwd",
    "connection_string",
)

_REDACTED = "***"
_CMD_SECRET_NAME = r"[\w-]*(?:password|passwd|token|secret|api[_-]?key|apikey|access[_-]?key|credential|private[_-]?key|auth)[\w-]*"
_CMD_VALUE = r"(?:'[^']*'|\"[^\"]*\"|[^\s'\"]+)"
_CMD_PATTERNS = (
    # Authorization: Bearer abc / Basic abc / Token abc / ApiKey abc
    re.compile(r"(?i)\b(bearer|basic|token|apikey)(\s+)(" + _CMD_VALUE + r")"),
    # Header values such as "X-Api-Key: abc" or "Authorization: abc"
    re.compile(
        r"(?i)\b("
        + _CMD_SECRET_NAME
        + r"|authorization|cookie)(\s*:\s*)("
        + _CMD_VALUE
        + r")"
    ),
    # Flags such as --password=abc or --token abc
    re.compile(r"(?i)(--?" + _CMD_SECRET_NAME + r")(=|\s+)(" + _CMD_VALUE + r")"),
    # Environment assignments such as AWS_SECRET_ACCESS_KEY=abc
    re.compile(r"(?i)\b(" + _CMD_SECRET_NAME + r")(=)(" + _CMD_VALUE + r")"),
    # URL userinfo such as https://user:pass@host
    re.compile(r"(://[^\s/:@'\"]+)(:)([^\s/@'\"]+)(?=@)"),
    # URL userinfo with only a token such as https://token@host
    re.compile(r"(://)()([^\s/:@'\"]+)(?=@)"),
)

_MAX_SUGGESTIONS = 15
_MAX_TEXT = 500
_MAX_COMMAND = 200


def _is_secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def _is_placeholder(value: str) -> bool:
    single_quoted = len(value) >= 2 and value[0] == value[-1] == "'"
    stripped = value.strip("'\"")
    if stripped.lower() in ("bearer", "basic", "token", "apikey"):
        # Auth scheme word; the credential after it is handled separately.
        return True
    if stripped == _REDACTED:
        return True
    # Jinja templates. "}}" covers the scheme pattern matching "token }}"
    # inside "{{ token }}".
    if stripped.startswith(("{{", "}}")):
        return True
    # Shell variables and command substitutions, which a single-quoted
    # string does not expand.
    if not single_quoted and re.fullmatch(r"\$(?:\w+|\{\w+\}|\(.*)", stripped):
        return True
    return False


def redact_command(command: str) -> str:
    """Mask credential-looking values in a tool command, keeping template placeholders."""

    def _mask(match: "re.Match[str]") -> str:
        name, sep, value = match.group(1), match.group(2), match.group(3)
        if _is_placeholder(value):
            return match.group(0)
        return f"{name}{sep}{_REDACTED}"

    for pattern in _CMD_PATTERNS:
        command = pattern.sub(_mask, command)
    return command


def _truncate(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def redact_secrets(value: Any) -> Any:
    """Redact secret-looking keys. Never returns original secret strings."""
    if isinstance(value, SecretStr):
        return "***"
    if isinstance(value, Mapping):
        redacted: Dict[str, Any] = {}
        for key, nested in value.items():
            name = str(key)
            if _is_secret_key(name):
                redacted[name] = "***" if nested not in (None, "", [], {}) else nested
            else:
                redacted[name] = redact_secrets(nested)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return redact_secrets(dump())
        except Exception:
            return type(value).__name__
    return value


def serialize_parameter(param: ToolParameter) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "type": param.type,
        "required": param.required,
    }
    if param.description:
        data["description"] = param.description
    if param.enum:
        data["enum"] = param.enum
    if param.properties:
        data["properties"] = {
            name: serialize_parameter(child) for name, child in param.properties.items()
        }
    return data


def serialize_tool(tool: Tool) -> Dict[str, Any]:
    parameters = {
        name: serialize_parameter(param)
        for name, param in (tool.parameters or {}).items()
    }
    entry: Dict[str, Any] = {
        "name": tool.name,
        "description": _truncate(tool.description, _MAX_TEXT),
        "parameters": parameters,
    }
    command = getattr(tool, "command", None)
    if command:
        entry["command"] = _truncate(redact_command(str(command)), _MAX_COMMAND)
    return entry


def serialize_toolset(toolset: Toolset) -> Dict[str, Any]:
    tools = [serialize_tool(tool) for tool in (toolset.tools or [])]
    status = toolset.status.value if toolset.status else None
    toolset_type = toolset.type.value if toolset.type else None
    tags = [
        tag.value if hasattr(tag, "value") else str(tag) for tag in (toolset.tags or [])
    ]
    return {
        "name": toolset.name,
        "description": toolset.description,
        "status": status,
        "enabled": toolset.enabled,
        "type": toolset_type,
        "path": str(toolset.path) if toolset.path else None,
        "error": _truncate(toolset.error, _MAX_TEXT),
        "docs_url": toolset.docs_url,
        "installation_instructions": _truncate(
            toolset.installation_instructions, _MAX_TEXT
        ),
        "tags": tags,
        "config": redact_secrets(toolset.config) if toolset.config else None,
        "tool_count": len(tools),
        "tools": tools,
    }


def resolve_toolset(
    toolsets: Sequence[Toolset], query: str
) -> Tuple[Optional[Toolset], List[str], Optional[str]]:
    """Return (match, suggestions, error). Error is set when no unique match."""
    needle = query.strip()
    if not needle:
        return None, [], "Toolset name is required"

    exact = [toolset for toolset in toolsets if toolset.name == needle]
    if len(exact) == 1:
        return exact[0], [], None
    if len(exact) > 1:
        names = [toolset.name for toolset in exact]
        return None, names, f"Ambiguous toolset name {needle!r}"

    lowered = needle.lower()
    casefold = [toolset for toolset in toolsets if toolset.name.lower() == lowered]
    if len(casefold) == 1:
        return casefold[0], [], None
    if len(casefold) > 1:
        names = [toolset.name for toolset in casefold]
        return None, names, f"Ambiguous toolset name {needle!r}"

    prefixed = [
        toolset for toolset in toolsets if toolset.name.lower().startswith(lowered)
    ]
    if len(prefixed) == 1:
        return prefixed[0], [], None
    if len(prefixed) > 1:
        names = [toolset.name for toolset in prefixed][:_MAX_SUGGESTIONS]
        return None, names, f"Multiple toolsets start with {needle!r}"

    contained = [toolset for toolset in toolsets if lowered in toolset.name.lower()]
    if len(contained) == 1:
        return contained[0], [], None
    if len(contained) > 1:
        names = [toolset.name for toolset in contained][:_MAX_SUGGESTIONS]
        return None, names, f"Multiple toolsets match {needle!r}"

    suggestions = [toolset.name for toolset in toolsets][:_MAX_SUGGESTIONS]
    return None, suggestions, f"Unknown toolset {needle!r}"


def _status_markup(status: Optional[str], enabled: bool) -> str:
    if status == ToolsetStatusEnum.ENABLED.value:
        return "[green]enabled[/green]"
    if status == ToolsetStatusEnum.FAILED.value and enabled:
        return "[red]failed[/red]"
    if status == ToolsetStatusEnum.FAILED.value and not enabled:
        return "[yellow]unconfigured[/yellow]"
    if status:
        return f"[yellow]{status}[/yellow]"
    return "[yellow]unknown[/yellow]"


def render_toolset(payload: Mapping[str, Any], console: Console) -> None:
    name = str(payload.get("name") or "")
    status = payload.get("status")
    enabled = bool(payload.get("enabled"))
    toolset_type = payload.get("type") or ""
    console.print(
        f"[bold]{name}[/bold]  {_status_markup(status if isinstance(status, str) else None, enabled)}"
        + (f"  {toolset_type}" if toolset_type else "")
    )
    description = payload.get("description")
    if description:
        console.print(str(description))
    docs_url = payload.get("docs_url")
    if docs_url:
        console.print(f"[dim]Docs:[/dim] {docs_url}")
    path = payload.get("path")
    if path:
        console.print(f"[dim]Path:[/dim] {path}")
    error = payload.get("error")
    if error:
        console.print(f"[red]Error:[/red] {error}")
    config = payload.get("config")
    if config:
        console.print("[dim]Config (secrets redacted):[/dim]")
        console.print(json.dumps(config, indent=2, default=str))

    tools = payload.get("tools") or []
    console.print(f"\n[bold]Tools ({payload.get('tool_count', len(tools))}):[/bold]")
    if not tools:
        console.print("[dim]No tools[/dim]")
        return

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Name")
    table.add_column("Parameters")
    table.add_column("Description")
    show_commands = any(
        isinstance(tool, Mapping) and tool.get("command") for tool in tools
    )
    if show_commands:
        table.add_column("Command")
    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        params = tool.get("parameters") or {}
        param_bits = []
        if isinstance(params, Mapping):
            for pname, spec in params.items():
                required = ""
                ptype = "string"
                if isinstance(spec, Mapping):
                    ptype = str(spec.get("type") or "string")
                    if spec.get("required") is False:
                        required = "?"
                param_bits.append(f"{pname}{required}:{ptype}")
        row = [
            str(tool.get("name") or ""),
            ", ".join(param_bits) if param_bits else "—",
            str(tool.get("description") or ""),
        ]
        if show_commands:
            row.append(escape(str(tool.get("command") or "")))
        table.add_row(*row)
    console.print(table)


def dumps_toolset(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, default=str) + "\n"


def dumps_inspect_error(message: str, suggestions: Sequence[str]) -> str:
    return (
        json.dumps({"error": message, "suggestions": list(suggestions)}, indent=2)
        + "\n"
    )


InspectResult = Union[Tuple[Dict[str, Any], None], Tuple[None, Tuple[str, List[str]]]]


def inspect_named_toolset(toolsets: Sequence[Toolset], query: str) -> InspectResult:
    match, suggestions, error = resolve_toolset(toolsets, query)
    if match is None:
        return None, (error or "Unknown toolset", suggestions)
    return serialize_toolset(match), None

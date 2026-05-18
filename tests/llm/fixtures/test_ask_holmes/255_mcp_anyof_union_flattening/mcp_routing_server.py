"""
MCP server that exposes a tool with an anyOf parameter containing two distinct
object shapes.  This tests HolmesGPT's handling of union types in MCP schemas.

The `query_routing` tool accepts a `selector` parameter that is:
  - Shape A: {"level": "<severity>"}           -> returns escalation policy names
  - Shape B: {"group": "<team>", "active": bool} -> returns on-call person + verification code

IMPORTANT: Descriptions intentionally do NOT reveal shape B's parameter names.
The LLM must discover {group, active} from the anyOf schema alone.
After flattening, only Shape A ({level: string}) is visible, making Shape B
unreachable.

The verification code ONCALL-EVAL-9r4w7z is ONLY returned by Shape B.
"""

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

VERIFICATION_CODE = "ONCALL-EVAL-9r4w7z"

# --- Static data ---

LEVEL_POLICIES = {
    "critical": {
        "policy": "Escalate-P1",
        "notify": ["team-alpha", "team-bravo", "team-charlie"],
        "timeout_minutes": 5,
    },
    "warning": {
        "policy": "Escalate-P2",
        "notify": ["team-alpha", "team-delta"],
        "timeout_minutes": 30,
    },
    "info": {
        "policy": "Log-Only",
        "notify": ["team-delta"],
        "timeout_minutes": None,
    },
}

GROUP_MEMBERS = {
    "team-alpha": {
        "all": [
            {"name": "Alice Chen", "role": "senior-sre", "active": True},
            {"name": "Bob Martinez", "role": "sre", "active": False},
            {"name": "Carol Wu", "role": "tech-lead", "active": False},
            {"name": "Dan Okonkwo", "role": "sre", "active": False},
        ],
        "active_only": [
            {"name": "Alice Chen", "role": "senior-sre", "shift": "2026-03-24T00:00Z/2026-03-25T00:00Z"},
        ],
    },
    "team-bravo": {
        "all": [
            {"name": "Eve Tanaka", "role": "principal-sre", "active": False},
            {"name": "Frank Kim", "role": "sre", "active": True},
        ],
        "active_only": [
            {"name": "Frank Kim", "role": "sre", "shift": "2026-03-24T00:00Z/2026-03-25T00:00Z"},
        ],
    },
    "team-charlie": {
        "all": [
            {"name": "Grace Patel", "role": "security-engineer", "active": True},
        ],
        "active_only": [
            {"name": "Grace Patel", "role": "security-engineer", "shift": "2026-03-24T00:00Z/2026-03-25T00:00Z"},
        ],
    },
    "team-delta": {
        "all": [
            {"name": "Hiro Nakamura", "role": "backend-dev", "active": True},
            {"name": "Iris Johansson", "role": "backend-dev", "active": False},
        ],
        "active_only": [
            {"name": "Hiro Nakamura", "role": "backend-dev", "shift": "2026-03-24T00:00Z/2026-03-25T00:00Z"},
        ],
    },
}


def _handle_query_routing(arguments: dict) -> str:
    selector = arguments.get("selector", {})

    if not isinstance(selector, dict):
        return f"Error: selector must be an object, got {type(selector).__name__}"

    # Shape B: group query (with verification code)
    if "group" in selector:
        group_name = selector["group"]
        active_flag = selector.get("active", False)

        data = GROUP_MEMBERS.get(group_name)
        if not data:
            return f"Error: unknown group '{group_name}'. Valid: {list(GROUP_MEMBERS.keys())}"

        if active_flag:
            members = data["active_only"]
            label = "active rotation members"
        else:
            members = data["all"]
            label = "all members"

        lines = [
            f"Group: {group_name}",
            f"Query: {label}",
            f"Verification: {VERIFICATION_CODE}",
            f"Count: {len(members)}",
            "Members:",
        ]
        for m in members:
            lines.append(f"  - {m['name']} ({m['role']})")
            if "shift" in m:
                lines.append(f"    current shift: {m['shift']}")
        return "\n".join(lines)

    # Shape A: level query
    if "level" in selector:
        level = selector["level"]
        policy = LEVEL_POLICIES.get(level)
        if not policy:
            return f"Error: unknown level '{level}'. Valid: {list(LEVEL_POLICIES.keys())}"

        lines = [
            f"Level: {level}",
            f"Policy: {policy['policy']}",
            f"Groups notified: {', '.join(policy['notify'])}",
        ]
        if policy["timeout_minutes"]:
            lines.append(f"Escalation timeout: {policy['timeout_minutes']} minutes")
        return "\n".join(lines)

    return (
        "Error: unrecognized selector shape. "
        "The selector must match one of the documented schema variants."
    )


# --- Tool schema with anyOf (two object shapes) ---

TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selector": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "level": {
                            "type": "string",
                            "enum": ["critical", "warning", "info"],
                        },
                    },
                    "required": ["level"],
                },
                {
                    "type": "object",
                    "properties": {
                        "group": {
                            "type": "string",
                        },
                        "active": {
                            "type": "boolean",
                        },
                    },
                    "required": ["group", "active"],
                },
            ],
        },
    },
    "required": ["selector"],
}


# --- Server ---

ALL_TOOLS = [
    Tool(
        name="query_routing",
        # Description intentionally does NOT mention parameter names for shape B.
        # The LLM must discover {group, active} from the anyOf schema.
        description="Query the alert routing system. Pass a selector object to retrieve routing information.",
        inputSchema=TOOL_SCHEMA,
    ),
]

server = Server("alert-routing")


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return ALL_TOOLS


@server.call_tool(validate_input=False)
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    arguments = arguments or {}

    if name == "query_routing":
        text = _handle_query_routing(arguments)
    else:
        text = f"Unknown tool: {name}"

    return [TextContent(type="text", text=text)]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

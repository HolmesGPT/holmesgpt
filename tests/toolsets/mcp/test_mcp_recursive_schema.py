"""MCP tool input schemas whose definitions refer to themselves."""

import pytest

from holmes.core.openai_formatting import format_tool_to_open_ai_standard
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPTool

FILTER_SCHEMA = {
    "type": "object",
    "properties": {"filter": {"$ref": "#/$defs/Filter"}},
    "required": ["filter"],
    "$defs": {
        "Filter": {
            "type": "object",
            "description": "A field filter, or a group of filters.",
            "properties": {
                "field": {"type": "string"},
                "and": {"type": "array", "items": {"$ref": "#/$defs/Filter"}},
                "not": {"anyOf": [{"$ref": "#/$defs/Filter"}, {"type": "null"}]},
            },
        }
    },
}


def test_self_referencing_definition_stops_at_first_repeat():
    params = RemoteMCPTool.parse_input_schema(FILTER_SCHEMA)

    flt = params["filter"]
    assert flt.type == "object" and flt.required
    assert flt.properties["field"].type == "string"
    nested = flt.properties["and"].items
    assert nested.type == "object" and nested.properties is None
    assert flt.properties["not"].type == "object"
    assert flt.properties["not"].properties is None


def test_mutually_recursive_definitions():
    schema = {
        "type": "object",
        "properties": {"node": {"$ref": "#/$defs/A"}},
        "$defs": {
            "A": {"type": "object", "properties": {"b": {"$ref": "#/$defs/B"}}},
            "B": {"type": "object", "properties": {"a": {"$ref": "#/$defs/A"}, "n": {"type": "integer"}}},
        },
    }
    node = RemoteMCPTool.parse_input_schema(schema)["node"]
    b = node.properties["b"]
    assert b.properties["n"].type == "integer"
    assert b.properties["a"].type == "object" and b.properties["a"].properties is None


def test_reused_definition_in_siblings_is_expanded_in_each():
    schema = {
        "type": "object",
        "properties": {
            "src": {"$ref": "#/$defs/Addr"},
            "dst": {"$ref": "#/$defs/Addr"},
        },
        "$defs": {"Addr": {"type": "object", "properties": {"host": {"type": "string"}}}},
    }
    params = RemoteMCPTool.parse_input_schema(schema)
    assert params["src"].properties["host"].type == "string"
    assert params["dst"].properties["host"].type == "string"


def test_definition_that_is_only_a_self_reference():
    schema = {
        "type": "object",
        "properties": {"x": {"$ref": "#/$defs/Loop"}},
        "$defs": {"Loop": {"$ref": "#/$defs/Loop"}},
    }
    assert RemoteMCPTool.parse_input_schema(schema)["x"].type == "object"


@pytest.mark.parametrize("strict", [True, False])
def test_recursive_schema_formats_for_the_llm(monkeypatch, strict):
    monkeypatch.setattr("holmes.core.openai_formatting.STRICT_TOOL_CALLS_ENABLED", strict)
    params = RemoteMCPTool.parse_input_schema(FILTER_SCHEMA)
    formatted = format_tool_to_open_ai_standard("search", "Search", params)
    flt = formatted["function"]["parameters"]["properties"]["filter"]
    assert flt["properties"]["and"]["items"]["type"] == "object"

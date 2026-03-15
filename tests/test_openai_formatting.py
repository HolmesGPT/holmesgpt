import pytest

from holmes.core.openai_formatting import type_to_open_ai_schema, format_tool_to_open_ai_standard
from holmes.core.tools import ToolParameter


@pytest.mark.parametrize(
    "toolset_type, open_ai_type",
    [
        (
            "int",
            {"type": "int"},
        ),
        (
            "string",
            {"type": "string"},
        ),
        (
            "array[int]",
            {"type": "array", "items": {"type": "int"}},
        ),
        (
            "array[string]",
            {"type": "array", "items": {"type": "string"}},
        ),
    ],
)
def test_type_to_open_ai_schema(toolset_type, open_ai_type):
    param = ToolParameter(type=toolset_type, required=True)
    result = type_to_open_ai_schema(param, strict_mode=False)
    assert result == open_ai_type


def test_strict_mode_sets_additional_properties_false_on_objects():
    param = ToolParameter(
        type="object",
        required=True,
        properties={"name": ToolParameter(type="string", required=True)},
    )
    result = type_to_open_ai_schema(param, strict_mode=True)
    assert result["additionalProperties"] is False
    assert result["required"] == ["name"]


def test_strict_mode_preserves_additional_properties_schema():
    """Objects with additionalProperties schema (dynamic keys) should preserve it, not set False."""
    param = ToolParameter(
        type="object",
        required=True,
        additional_properties={"type": "string"},
    )
    result = type_to_open_ai_schema(param, strict_mode=True)
    assert result["additionalProperties"] == {"type": "string"}


def test_is_strict_compatible_simple_params():
    param = ToolParameter(type="string", required=True)
    assert param.is_strict_compatible() is True


def test_is_strict_compatible_object_with_properties():
    param = ToolParameter(
        type="object",
        required=True,
        properties={"name": ToolParameter(type="string", required=True)},
    )
    assert param.is_strict_compatible() is True


def test_is_strict_compatible_dynamic_keys():
    param = ToolParameter(
        type="object",
        required=True,
        additional_properties={"type": "string"},
    )
    assert param.is_strict_compatible() is False


def test_is_strict_compatible_nested_dynamic_keys():
    inner = ToolParameter(
        type="object",
        required=True,
        additional_properties={"type": "string"},
    )
    outer = ToolParameter(
        type="object",
        required=True,
        properties={"filters": inner},
    )
    assert outer.is_strict_compatible() is False


def test_format_tool_strict_for_compatible_tool(monkeypatch):
    monkeypatch.setattr("holmes.core.openai_formatting.STRICT_TOOL_CALLS_ENABLED", True)
    params = {
        "query": ToolParameter(type="string", required=True, description="The query"),
    }
    result = format_tool_to_open_ai_standard("search", "Search things", params, "any-model")
    assert result["function"]["strict"] is True
    assert result["function"]["parameters"]["additionalProperties"] is False


def test_format_tool_no_strict_for_dynamic_keys(monkeypatch):
    monkeypatch.setattr("holmes.core.openai_formatting.STRICT_TOOL_CALLS_ENABLED", True)
    params = {
        "query": ToolParameter(type="string", required=True, description="The query"),
        "filters": ToolParameter(
            type="object",
            required=False,
            description="Filters",
            additional_properties={"type": "string"},
        ),
    }
    result = format_tool_to_open_ai_standard("search", "Search things", params, "any-model")
    assert "strict" not in result["function"]


def test_format_tool_disabled_via_env(monkeypatch):
    monkeypatch.setattr("holmes.core.openai_formatting.STRICT_TOOL_CALLS_ENABLED", False)
    params = {
        "query": ToolParameter(type="string", required=True, description="The query"),
    }
    result = format_tool_to_open_ai_standard("search", "Search things", params, "any-model")
    assert "strict" not in result["function"]

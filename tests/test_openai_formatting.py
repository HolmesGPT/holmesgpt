import pytest

from holmes.core.openai_formatting import type_to_open_ai_schema, format_tool_to_open_ai_standard
from holmes.core.tools import ToolParameter

from tests.mocks.toolset_mocks import DummyTool


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
    result = format_tool_to_open_ai_standard("search", "Search things", params)
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
    result = format_tool_to_open_ai_standard("search", "Search things", params)
    assert "strict" not in result["function"]


def test_format_tool_disabled_via_env(monkeypatch):
    monkeypatch.setattr("holmes.core.openai_formatting.STRICT_TOOL_CALLS_ENABLED", False)
    params = {
        "query": ToolParameter(type="string", required=True, description="The query"),
    }
    result = format_tool_to_open_ai_standard("search", "Search things", params)
    assert "strict" not in result["function"]


class TestParameterCoercion:
    """Tests for Tool._coerce_params — LLMs sometimes send stringified JSON
    for array/object parameters, especially when strict mode is disabled."""

    def _make_tool(self, parameters: dict) -> DummyTool:
        return DummyTool(parameters=parameters)

    def test_coerce_stringified_array(self):
        tool = self._make_tool({"metrics": ToolParameter(type="array", items=ToolParameter(type="string"))})
        result = tool._coerce_params({"metrics": '["cpu", "memory"]'})
        assert result["metrics"] == ["cpu", "memory"]

    def test_coerce_stringified_object(self):
        tool = self._make_tool({"filters": ToolParameter(type="object")})
        result = tool._coerce_params({"filters": '{"key": "value"}'})
        assert result["filters"] == {"key": "value"}

    def test_no_coercion_for_string_params(self):
        tool = self._make_tool({"query": ToolParameter(type="string")})
        result = tool._coerce_params({"query": '["not", "an", "array"]'})
        assert result["query"] == '["not", "an", "array"]'

    def test_no_coercion_when_already_correct_type(self):
        tool = self._make_tool({"metrics": ToolParameter(type="array")})
        original = ["cpu", "memory"]
        result = tool._coerce_params({"metrics": original})
        assert result["metrics"] is original

    def test_invalid_json_wrapped_in_array(self):
        """Non-JSON string for an array param is now wrapped as a single-element array."""
        tool = self._make_tool({"metrics": ToolParameter(type="array")})
        result = tool._coerce_params({"metrics": "not json at all"})
        assert result["metrics"] == ["not json at all"]

    def test_no_coercion_when_parsed_type_mismatches(self):
        """String parses as JSON but to wrong type — e.g. a string that parses
        to an array when the schema expects an object."""
        tool = self._make_tool({"data": ToolParameter(type="object")})
        result = tool._coerce_params({"data": '["an", "array"]'})
        assert result["data"] == '["an", "array"]'

    def test_coerce_nullable_array(self):
        """Nullable array type ["array", "null"] should still coerce strings."""
        tool = self._make_tool({"metrics": ToolParameter(type=["array", "null"])})
        result = tool._coerce_params({"metrics": '["cpu"]'})
        assert result["metrics"] == ["cpu"]

    def test_no_coercion_with_empty_params(self):
        tool = self._make_tool({"metrics": ToolParameter(type="array")})
        result = tool._coerce_params({})
        assert result == {}

    def test_no_coercion_with_no_schema(self):
        tool = self._make_tool({})
        result = tool._coerce_params({"metrics": '["cpu"]'})
        assert result["metrics"] == '["cpu"]'

    def test_does_not_mutate_original(self):
        tool = self._make_tool({"metrics": ToolParameter(type="array")})
        original = {"metrics": '["cpu"]'}
        result = tool._coerce_params(original)
        assert original["metrics"] == '["cpu"]'
        assert result["metrics"] == ["cpu"]


class TestPrimaryType:
    def test_simple_type(self):
        assert ToolParameter(type="string").primary_type == "string"

    def test_nullable_type(self):
        assert ToolParameter(type=["array", "null"]).primary_type == "array"

    def test_all_null(self):
        assert ToolParameter(type=["null"]).primary_type == "string"

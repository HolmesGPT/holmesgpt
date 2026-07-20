"""Tests for Azure OpenAI schema compatibility (Issue #2297).

Azure OpenAI requires 'required' to only include keys present in 'properties'.
Tools with dynamic object arguments (additionalProperties with schema) violate
this and cause the entire tool catalog to be rejected.
"""

import pytest
from unittest.mock import MagicMock

from holmes.core.openai_formatting import (
    _is_azure_compatible,
    filter_azure_incompatible_tools,
)


def _make_param(type_str="string", required=True, properties=None, additional_properties=None):
    """Create a mock ToolParameter."""
    param = MagicMock()
    param.type = type_str
    param.required = required
    param.properties = properties
    param.additional_properties = additional_properties
    return param


class TestAzureCompatibility:
    def test_simple_string_params_are_compatible(self):
        params = {
            "query": _make_param("string"),
            "limit": _make_param("integer", required=False),
        }
        assert _is_azure_compatible(params) is True

    def test_object_with_explicit_properties_is_compatible(self):
        props = {
            "name": _make_param("string"),
            "value": _make_param("string"),
        }
        params = {
            "config": _make_param("object", properties=props),
        }
        assert _is_azure_compatible(params) is True

    def test_object_with_additional_properties_is_incompatible(self):
        params = {
            "args": _make_param("object", additional_properties={"type": "string"}),
        }
        assert _is_azure_compatible(params) is False

    def test_object_with_additional_properties_true_is_incompatible(self):
        params = {
            "args": _make_param("object", additional_properties=True),
        }
        assert _is_azure_compatible(params) is False

    def test_object_with_additional_properties_false_is_compatible(self):
        params = {
            "config": _make_param("object", additional_properties=False, properties={"key": _make_param()}),
        }
        assert _is_azure_compatible(params) is True

    def test_required_object_without_properties_is_incompatible(self):
        params = {
            "data": _make_param("object", required=True, properties=None),
        }
        assert _is_azure_compatible(params) is False

    def test_optional_object_without_properties_is_compatible(self):
        params = {
            "data": _make_param("object", required=False, properties=None),
        }
        assert _is_azure_compatible(params) is True

    def test_mixed_params_with_one_incompatible(self):
        props = {"name": _make_param("string")}
        params = {
            "tool": _make_param("string"),
            "args": _make_param("object", additional_properties={"type": "string"}),
            "detail": _make_param("string"),
        }
        assert _is_azure_compatible(params) is False


class TestFilterAzureIncompatibleTools:
    def test_filters_out_incompatible_tools(self):
        tool1 = MagicMock()
        tool1.name = "good_tool"
        tool1.parameters = {"query": _make_param("string")}

        tool2 = MagicMock()
        tool2.name = "bad_tool"
        tool2.parameters = {"args": _make_param("object", additional_properties=True)}

        tool3 = MagicMock()
        tool3.name = "another_good_tool"
        tool3.parameters = {"name": _make_param("string")}

        result = filter_azure_incompatible_tools([tool1, tool2, tool3])
        assert len(result) == 2
        assert result[0].name == "good_tool"
        assert result[1].name == "another_good_tool"

    def test_keeps_all_compatible_tools(self):
        tools = []
        for i in range(5):
            tool = MagicMock()
            tool.name = f"tool_{i}"
            tool.parameters = {"query": _make_param("string")}
            tools.append(tool)

        result = filter_azure_incompatible_tools(tools)
        assert len(result) == 5

    def test_handles_tools_without_parameters(self):
        tool = MagicMock()
        tool.name = "no_params"
        tool.parameters = None

        result = filter_azure_incompatible_tools([tool])
        assert len(result) == 1

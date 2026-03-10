from unittest.mock import MagicMock

from holmes.core.tools import ToolsetStatusEnum, ToolsetTag
from holmes.plugins.toolsets.investigator.core_investigation import (
    CoreInvestigationToolset,
    JqQueryTool,
    TodoWriteTool,
)


class TestCoreInvestigationToolset:
    def test_toolset_creation(self):
        """Test that CoreInvestigationToolset is created correctly."""
        toolset = CoreInvestigationToolset()

        assert toolset.name == "core_investigation"
        assert "investigation tools" in toolset.description
        assert toolset.enabled is True
        assert toolset.is_default is True
        assert ToolsetTag.CORE in toolset.tags

    def test_toolset_has_todo_write_tool(self):
        """Test that the toolset includes the TodoWrite and JqQuery tools."""
        toolset = CoreInvestigationToolset()

        assert len(toolset.tools) == 2
        assert isinstance(toolset.tools[0], TodoWriteTool)
        assert toolset.tools[0].name == "TodoWrite"
        assert isinstance(toolset.tools[1], JqQueryTool)
        assert toolset.tools[1].name == "jq_query"

    def test_toolset_check_prerequisites(self):
        """Test that toolset prerequisites check passes."""
        toolset = CoreInvestigationToolset()
        toolset.check_prerequisites()

        # Should be enabled by default with no prerequisites
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset.error is None


class TestJqQueryTool:
    def _make_context(self, previous_tool_calls=None):
        ctx = MagicMock()
        ctx.tool_number = 1
        ctx.tool_call_id = "jq_call_1"
        ctx.tool_name = "jq_query"
        ctx.previous_tool_calls = previous_tool_calls or []
        return ctx

    def _make_previous_call(self, tool_call_id, data):
        """Helper to create a previous tool call entry."""
        return {
            "tool_call_id": tool_call_id,
            "tool_name": "get_issues",
            "role": "tool",
            "result": {"data": data, "status": "success"},
        }

    def test_count_items(self):
        tool = JqQueryTool()
        prev = [self._make_previous_call("call_1", '[{"a":1},{"a":2},{"a":3}]')]
        result = tool._invoke(
            {"tool_call_id": "call_1", "expression": ". | length"},
            self._make_context(previous_tool_calls=prev),
        )
        assert result.data == 3

    def test_group_and_count(self):
        tool = JqQueryTool()
        data = '[{"sev":"high"},{"sev":"low"},{"sev":"high"},{"sev":"low"},{"sev":"low"}]'
        prev = [self._make_previous_call("call_1", data)]
        result = tool._invoke(
            {"tool_call_id": "call_1", "expression": "group_by(.sev) | map({severity: .[0].sev, count: length})"},
            self._make_context(previous_tool_calls=prev),
        )
        assert result.data == [{"severity": "high", "count": 2}, {"severity": "low", "count": 3}]

    def test_filter_and_count(self):
        tool = JqQueryTool()
        data = '[{"status":"open"},{"status":"closed"},{"status":"open"}]'
        prev = [self._make_previous_call("call_1", data)]
        result = tool._invoke(
            {"tool_call_id": "call_1", "expression": '[.[] | select(.status == "open")] | length'},
            self._make_context(previous_tool_calls=prev),
        )
        assert result.data == 2

    def test_missing_tool_call_id(self):
        tool = JqQueryTool()
        prev = [self._make_previous_call("call_1", "[1,2,3]")]
        result = tool._invoke(
            {"tool_call_id": "nonexistent", "expression": ". | length"},
            self._make_context(previous_tool_calls=prev),
        )
        assert result.status.value == "error"
        assert "No previous tool call found" in result.error
        assert "call_1" in result.error  # Shows available IDs

    def test_invalid_expression(self):
        tool = JqQueryTool()
        prev = [self._make_previous_call("call_1", "[1,2,3]")]
        result = tool._invoke(
            {"tool_call_id": "call_1", "expression": ".[[invalid"},
            self._make_context(previous_tool_calls=prev),
        )
        assert result.status.value == "error"
        assert "jq expression error" in result.error

    def test_no_previous_calls(self):
        tool = JqQueryTool()
        result = tool._invoke(
            {"tool_call_id": "call_1", "expression": ". | length"},
            self._make_context(previous_tool_calls=[]),
        )
        assert result.status.value == "error"
        assert "No previous tool call found" in result.error

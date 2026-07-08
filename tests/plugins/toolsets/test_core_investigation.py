from holmes.core.tools import ToolsetStatusEnum, ToolsetTag
from holmes.plugins.toolsets.investigator.core_investigation import (
    CoreInvestigationToolset,
    HypothesisWriteTool,
    TodoWriteTool,
)


class TestCoreInvestigationToolset:
    def test_toolset_creation(self):
        """Test that CoreInvestigationToolset is created correctly."""
        toolset = CoreInvestigationToolset()

        assert toolset.name == "core_investigation"
        assert "investigation tools" in toolset.description
        assert toolset.enabled is True
        assert ToolsetTag.CORE in toolset.tags

    def test_toolset_has_todo_and_hypothesis_tools(self):
        """Test that the toolset includes the TodoWrite and HypothesisWrite tools."""
        toolset = CoreInvestigationToolset()

        tools_by_name = {tool.name: tool for tool in toolset.tools}
        assert len(toolset.tools) == 2
        assert isinstance(tools_by_name["TodoWrite"], TodoWriteTool)
        assert isinstance(tools_by_name["HypothesisWrite"], HypothesisWriteTool)

    def test_toolset_check_prerequisites(self):
        """Test that toolset prerequisites check passes."""
        toolset = CoreInvestigationToolset()
        toolset.check_prerequisites()

        # Should be enabled by default with no prerequisites
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset.error is None

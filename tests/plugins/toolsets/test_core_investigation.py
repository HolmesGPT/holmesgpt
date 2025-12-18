from holmes.plugins.toolsets.investigator.core_investigation import (
    CoreInvestigationToolset,
    TodoWriteTool,
)
from holmes.core.tools import ToolsetStatusEnum, ToolsetTag


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
        """Test that the toolset includes the TodoWrite tool."""
        toolset = CoreInvestigationToolset()

        assert len(toolset.tools) == 1
        assert isinstance(toolset.tools[0], TodoWriteTool)
        assert toolset.tools[0].name == "TodoWrite"

    def test_toolset_check_prerequisites(self):
        """Test that toolset prerequisites check passes."""
        toolset = CoreInvestigationToolset()
        toolset.check_prerequisites()

        # Should be enabled by default with no prerequisites
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset.error is None

    def test_get_example_config(self):
        """Test that example config is returned."""
        toolset = CoreInvestigationToolset()
        config = toolset.get_example_config()

        assert isinstance(config, dict)
        # Core toolset doesn't need configuration

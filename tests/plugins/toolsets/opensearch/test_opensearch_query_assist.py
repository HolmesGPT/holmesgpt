import pytest
from unittest.mock import MagicMock, patch

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolParameter,
    ToolsetTag,
    ToolInvokeContext,
)
from holmes.plugins.toolsets.opensearch.opensearch_query_assist import (
    PplQueryAssistTool,
    OpenSearchQueryAssistToolset,
)


class TestPplQueryAssistTool:
    """Tests for the PPL Query Assist Tool."""

    def setup_method(self):
        """Set up test fixtures."""
        self.toolset = OpenSearchQueryAssistToolset()
        self.tool = PplQueryAssistTool(self.toolset)
        self.mock_context = MagicMock(spec=ToolInvokeContext)

    def test_tool_initialization(self):
        """Test that the tool initializes with correct properties."""
        assert self.tool.name == "opensearch_ppl_query_assist"
        assert (
            "Generate valid OpenSearch Piped Processing Language"
            in self.tool.description
        )
        assert "query" in self.tool.parameters
        assert isinstance(self.tool.parameters["query"], ToolParameter)
        assert self.tool.parameters["query"].required is True
        assert self.tool.parameters["query"].type == "string"

    def test_invoke_with_valid_query(self):
        """Test successful invocation with a valid PPL query."""
        # Arrange
        test_query = "source=logs-* | stats count() by level"
        params = {"query": test_query}

        # Act
        result = self.tool._invoke(params, self.mock_context)

        # Assert
        assert isinstance(result, StructuredToolResult)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["query"] == test_query
        assert result.params == params
        assert result.error is None

    def test_invoke_with_empty_query(self):
        """Test invocation with empty query parameter."""
        # Arrange
        params = {"query": ""}

        # Act
        result = self.tool._invoke(params, self.mock_context)

        # Assert
        assert isinstance(result, StructuredToolResult)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["query"] == ""
        assert result.params == params

    def test_invoke_with_missing_query_parameter(self):
        """Test invocation with missing query parameter."""
        # Arrange
        params = {}

        # Act
        result = self.tool._invoke(params, self.mock_context)

        # Assert
        assert isinstance(result, StructuredToolResult)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["query"] == ""  # Default value when key is missing
        assert result.params == params

    def test_invoke_with_complex_query(self):
        """Test invocation with a complex PPL query."""
        # Arrange
        complex_query = """
        source=ai-agent-logs-*
        | where level="ERROR" and timestamp >= now() - 1h
        | parse message '.*thread_id=(?<tid>[^,]+).*'
        | stats count() by tid, level
        | sort - count
        | head 10
        """.strip()
        params = {"query": complex_query}

        # Act
        result = self.tool._invoke(params, self.mock_context)

        # Assert
        assert isinstance(result, StructuredToolResult)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["query"] == complex_query
        assert result.params == params

    def test_invoke_with_list_query(self):
        """Test invocation with query parameter as a list."""
        # Arrange
        test_query = ["source=logs-*", "stats count() by level"]
        params = {"query": test_query}

        # Act
        result = self.tool._invoke(params, self.mock_context)

        # Assert
        assert isinstance(result, StructuredToolResult)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["query"] == test_query
        assert result.params == params
        assert result.error is None

    def test_invoke_with_none_query(self):
        """Test invocation with None as query parameter."""
        # Arrange
        params = {"query": None}

        # Act
        result = self.tool._invoke(params, self.mock_context)

        # Assert
        assert isinstance(result, StructuredToolResult)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["query"] is None
        assert result.params == params
        assert result.error is None

    def test_invoke_without_user_approval(self):
        """Test invocation without user approval (default behavior)."""
        # Arrange
        test_query = "source=logs-* | stats count() by level"
        params = {"query": test_query}

        # Act
        result = self.tool._invoke(params, self.mock_context)

        # Assert
        assert isinstance(result, StructuredToolResult)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["query"] == test_query
        assert result.params == params
        assert result.error is None

    def test_get_parameterized_one_liner_with_query(self):
        """Test one-liner generation with query parameter."""
        # Arrange
        test_query = "source=logs-* | stats count() by level"
        params = {"query": test_query}

        # Act
        result = self.tool.get_parameterized_one_liner(params)

        # Assert
        expected = f"OpenSearchQueryToolset: Query ({test_query})"
        assert result == expected

    def test_get_parameterized_one_liner_without_query(self):
        """Test one-liner generation without query parameter."""
        # Arrange
        params = {}

        # Act
        result = self.tool.get_parameterized_one_liner(params)

        # Assert
        expected = "OpenSearchQueryToolset: Query ()"
        assert result == expected

    def test_get_parameterized_one_liner_with_empty_query(self):
        """Test one-liner generation with empty query."""
        # Arrange
        params = {"query": ""}

        # Act
        result = self.tool.get_parameterized_one_liner(params)

        # Assert
        expected = "OpenSearchQueryToolset: Query ()"
        assert result == expected

    def test_get_parameterized_one_liner_with_list_query(self):
        """Test one-liner generation with list query."""
        # Arrange
        params = {"query": ["source=logs-*", "stats count()"]}

        # Act
        result = self.tool.get_parameterized_one_liner(params)

        # Assert
        expected = "OpenSearchQueryToolset: Query (['source=logs-*', 'stats count()'])"
        assert result == expected

    def test_get_parameterized_one_liner_with_none_query(self):
        """Test one-liner generation with None query."""
        # Arrange
        params = {"query": None}

        # Act
        result = self.tool.get_parameterized_one_liner(params)

        # Assert
        expected = "OpenSearchQueryToolset: Query (None)"
        assert result == expected


class TestOpenSearchQueryAssistToolset:
    """Tests for the OpenSearch Query Assist Toolset."""

    def setup_method(self):
        """Set up test fixtures."""
        self.toolset = OpenSearchQueryAssistToolset()

    def test_toolset_initialization(self):
        """Test that the toolset initializes with correct properties."""
        assert self.toolset.name == "opensearch/query_assist"
        assert "OpenSearch query assist with PPL queries" in self.toolset.description
        assert self.toolset.experimental is True
        assert self.toolset.enabled is False
        assert self.toolset.is_default is False
        assert ToolsetTag.CORE in self.toolset.tags

        # Check that it has the correct tool
        assert len(self.toolset.tools) == 1
        assert isinstance(self.toolset.tools[0], PplQueryAssistTool)

    def test_get_example_config(self):
        """Test that get_example_config returns empty dict."""
        config = self.toolset.get_example_config()
        assert isinstance(config, dict)
        assert len(config) == 1

    @patch("holmes.plugins.toolsets.opensearch.opensearch_query_assist.os.path.abspath")
    @patch("holmes.plugins.toolsets.opensearch.opensearch_query_assist.os.path.join")
    @patch("holmes.plugins.toolsets.opensearch.opensearch_query_assist.os.path.dirname")
    def test_reload_instructions_template_path_construction(
        self, mock_dirname, mock_join, mock_abspath
    ):
        """Test that _reload_instructions constructs the correct template path."""
        # Arrange
        mock_dirname.return_value = "/path/to/opensearch"
        mock_join.return_value = (
            "/path/to/opensearch/opensearch_query_assist_instructions.jinja2"
        )
        mock_abspath.return_value = (
            "/absolute/path/to/opensearch/opensearch_query_assist_instructions.jinja2"
        )

        # Mock the _load_llm_instructions method
        with patch.object(
            self.toolset, "_load_llm_instructions"
        ) as mock_load_instructions:
            # Act
            self.toolset._reload_instructions()

            # Assert
            mock_dirname.assert_called_once()
            mock_join.assert_called_once_with(
                "/path/to/opensearch", "opensearch_query_assist_instructions.jinja2"
            )
            mock_abspath.assert_called_once_with(
                "/path/to/opensearch/opensearch_query_assist_instructions.jinja2"
            )
            mock_load_instructions.assert_called_once_with(
                jinja_template="file:///absolute/path/to/opensearch/opensearch_query_assist_instructions.jinja2"
            )

    @patch("holmes.plugins.toolsets.opensearch.opensearch_query_assist.os.path.abspath")
    @patch("holmes.plugins.toolsets.opensearch.opensearch_query_assist.os.path.join")
    @patch("holmes.plugins.toolsets.opensearch.opensearch_query_assist.os.path.dirname")
    def test_reload_instructions_with_real_file_path(
        self, mock_dirname, mock_join, mock_abspath
    ):
        """Test _reload_instructions with realistic file paths."""
        # Arrange
        template_dir = "/app/holmes/plugins/toolsets/opensearch"
        template_path = "/app/holmes/plugins/toolsets/opensearch/opensearch_query_assist_instructions.jinja2"
        abs_template_path = "/app/holmes/plugins/toolsets/opensearch/opensearch_query_assist_instructions.jinja2"

        mock_dirname.return_value = template_dir
        mock_join.return_value = template_path
        mock_abspath.return_value = abs_template_path

        # Mock the _load_llm_instructions method
        with patch.object(
            self.toolset, "_load_llm_instructions"
        ) as mock_load_instructions:
            # Act
            self.toolset._reload_instructions()

            # Assert
            expected_file_uri = f"file://{abs_template_path}"
            mock_load_instructions.assert_called_once_with(
                jinja_template=expected_file_uri
            )

    def test_toolset_has_correct_tool_configuration(self):
        """Test that the toolset's tool is properly configured."""
        tool = self.toolset.tools[0]

        # Verify tool name and description
        assert tool.name == "opensearch_ppl_query_assist"
        assert "Generate valid OpenSearch Piped Processing Language" in tool.description

        # Verify tool parameters
        assert "query" in tool.parameters
        query_param = tool.parameters["query"]
        assert query_param.required is True
        assert query_param.type == "string"

    def test_toolset_inheritance(self):
        """Test that the toolset properly inherits from Toolset base class."""
        from holmes.core.tools import Toolset

        assert isinstance(self.toolset, Toolset)
        assert hasattr(self.toolset, "name")
        assert hasattr(self.toolset, "description")
        assert hasattr(self.toolset, "tools")
        assert hasattr(self.toolset, "tags")
        assert hasattr(self.toolset, "experimental")
        assert hasattr(self.toolset, "enabled")
        assert hasattr(self.toolset, "is_default")


class TestIntegration:
    """Integration tests for the OpenSearch Query Assist components."""

    def test_tool_and_toolset_integration(self):
        """Test that the tool works correctly within the toolset."""
        # Arrange
        toolset = OpenSearchQueryAssistToolset()
        tool = toolset.tools[0]
        test_query = (
            "source=ai-agent-logs-* | where level='ERROR' | stats count() by message"
        )
        params = {"query": test_query}

        # Act
        mock_context = MagicMock(spec=ToolInvokeContext)
        result = tool._invoke(params, mock_context)
        one_liner = tool.get_parameterized_one_liner(params)

        # Assert
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["query"] == test_query
        assert test_query in one_liner
        assert "OpenSearchQueryToolset" in one_liner

    def test_toolset_configuration_consistency(self):
        """Test that toolset configuration is consistent."""
        toolset = OpenSearchQueryAssistToolset()

        # Verify toolset properties
        assert toolset.experimental is True
        assert toolset.enabled is False
        assert toolset.is_default is False

        # Verify it has exactly one tool
        assert len(toolset.tools) == 1

        # Verify the tool is the correct type
        assert isinstance(toolset.tools[0], PplQueryAssistTool)

        # Verify tags include CORE
        assert ToolsetTag.CORE in toolset.tags

    @patch("holmes.plugins.toolsets.opensearch.opensearch_query_assist.os.path.exists")
    def test_template_file_existence_check(self, mock_exists):
        """Test behavior when template file exists or doesn't exist."""
        # This test verifies the path construction logic
        toolset = OpenSearchQueryAssistToolset()

        # Mock file existence
        mock_exists.return_value = True

        with patch.object(toolset, "_load_llm_instructions") as mock_load:
            toolset._reload_instructions()

            # Verify that _load_llm_instructions was called
            mock_load.assert_called_once()

            # Verify the call includes the correct file:// prefix
            call_args = mock_load.call_args[1]
            assert "jinja_template" in call_args
            assert call_args["jinja_template"].startswith("file://")
            assert call_args["jinja_template"].endswith(
                "opensearch_query_assist_instructions.jinja2"
            )


if __name__ == "__main__":
    pytest.main([__file__])

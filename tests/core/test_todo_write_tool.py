from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.investigator.core_investigation import TodoWriteTool
from holmes.plugins.toolsets.investigator.model import TaskStatus
from tests.conftest import create_mock_tool_invoke_context


class TestTodoWriteTool:
    def test_todo_write_tool_creation(self):
        """Test that TodoWriteTool can be created with correct parameters."""
        tool = TodoWriteTool()
        assert tool.name == "TodoWrite"
        assert "investigation tasks" in tool.description
        assert "todos" in tool.parameters

    def test_todo_write_tool_empty_params(self):
        """Test TodoWriteTool with empty parameters."""
        tool = TodoWriteTool()
        result = tool._invoke({}, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert isinstance(result.data, str)
        assert "0 tasks" in result.data
        assert "Investigation plan updated" in result.data

    def test_todo_write_tool_with_tasks(self):
        """Test TodoWriteTool with valid task data."""
        tool = TodoWriteTool()
        params = {
            "todos": [
                {
                    "id": "1",
                    "content": "Check pod status",
                    "status": "pending",
                    "priority": "high",
                },
                {
                    "id": "2",
                    "content": "Analyze logs",
                    "status": "in_progress",
                    "priority": "medium",
                },
            ]
        }

        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert isinstance(result.data, str)
        assert "2 tasks" in result.data
        assert "Investigation plan updated" in result.data
        # Should include pretty printed TodoList
        assert "Check pod status" in result.data
        assert "Analyze logs" in result.data

    def test_todo_write_tool_default_values(self):
        """Test TodoWriteTool with minimal task data uses defaults."""
        tool = TodoWriteTool()
        params = {"todos": [{"content": "Test task"}]}

        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert isinstance(result.data, str)
        assert "1 tasks" in result.data
        assert "Investigation plan updated" in result.data
        # Should include pretty printed TodoList
        assert "Test task" in result.data

    def test_todo_write_tool_invalid_enum_values(self):
        """Test TodoWriteTool handles invalid enum values gracefully."""
        tool = TodoWriteTool()
        params = {
            "todos": [
                {
                    "content": "Test task",
                    "status": "invalid_status",
                    "priority": "invalid_priority",
                }
            ]
        }

        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        # Should handle gracefully and return error
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Failed to process tasks" in result.error

    def test_get_parameterized_one_liner(self):
        """Test the parameterized one-liner description."""
        tool = TodoWriteTool()

        params = {"todos": [{"content": "task1"}, {"content": "task2"}]}
        one_liner = tool.get_parameterized_one_liner(params)
        assert one_liner == "Update investigation tasks"

        params = {"todos": []}
        one_liner = tool.get_parameterized_one_liner(params)
        assert one_liner == "Update investigation tasks"

    def test_task_status_enum(self):
        """Test TaskStatus enum values."""
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.IN_PROGRESS == "in_progress"
        assert TaskStatus.COMPLETED == "completed"

    def test_openai_format(self):
        """Test that the tool generates correct OpenAI format."""
        tool = TodoWriteTool()
        openai_format = tool.get_openai_format("azure/gpt-4o")

        assert openai_format["type"] == "function"
        assert openai_format["function"]["name"] == "TodoWrite"
        assert "investigation tasks" in openai_format["function"]["description"]

        # Check parameters schema
        params = openai_format["function"]["parameters"]
        assert params["type"] == "object"
        assert "todos" in params["properties"]

        # Check array schema has items property
        todos_param = params["properties"]["todos"]
        assert todos_param["type"] == "array"
        assert "items" in todos_param
        assert todos_param["items"]["type"] == "object"

        # Check required fields
        assert "todos" in params["required"]

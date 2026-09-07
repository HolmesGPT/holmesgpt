from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.investigator.core_investigation import (
    TodoWriteTool,
    parse_tasks,
)
from holmes.plugins.toolsets.investigator.model import Task, TaskStatus
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

    def test_todo_write_tool_with_string_tasks(self):
        """Test TodoWriteTool handles string task items gracefully."""
        tool = TodoWriteTool()
        params = {"todos": ["Check pod status", "Analyze logs"]}

        result = tool._invoke(params, context=create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "2 tasks" in result.data
        assert "Check pod status" in result.data
        assert isinstance(result.params["todos"], list)
        assert result.params["todos"][0]["content"] == "Check pod status"
        assert result.params["todos"][0]["status"] == "pending"

    def test_todo_write_tool_invalid_enum_values(self):
        """Test TodoWriteTool handles invalid enum values gracefully by sanitizing to pending."""
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

        # Should handle gracefully by sanitizing invalid status to pending
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "1 tasks" in result.data
        assert result.params["todos"][0]["status"] == "pending"
        assert result.params["todos"][0]["content"] == "Test task"

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
        openai_format = tool.get_openai_format()

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


class TestTaskModel:
    def test_task_model_string_coercion(self):
        """Test that Task.model_validate with a string coerces to Task with content and pending status."""
        task = Task.model_validate("Check pod status")
        assert task.content == "Check pod status"
        assert task.status == TaskStatus.PENDING
        assert isinstance(task.id, str)
        assert len(task.id) > 0

    def test_task_model_invalid_status_sanitization(self):
        """Test that Task.model_validate with an unknown or malformed status sanitizes to pending status."""
        for invalid_status in ["unknown_value", None, "", ["pending"]]:
            task = Task.model_validate({"content": "foo", "status": invalid_status})
            assert task.content == "foo"
            assert task.status == TaskStatus.PENDING
            assert isinstance(task.id, str)

    def test_task_model_to_dict(self):
        """Test that Task.to_dict returns a dict with string status."""
        task = Task.model_validate(
            {"id": "custom-id", "content": "foo", "status": "completed"}
        )
        d = task.to_dict()
        assert d == {
            "id": "custom-id",
            "content": "foo",
            "status": "completed",
        }
        assert isinstance(d["status"], str)


class TestParseTasks:
    def test_parse_tasks_with_json_string_list(self):
        """Test parse_tasks with a JSON string of task strings."""
        tasks = parse_tasks('["Check pods", "View logs"]')
        assert len(tasks) == 2
        assert tasks[0].content == "Check pods"
        assert tasks[0].status == TaskStatus.PENDING
        assert tasks[1].content == "View logs"
        assert tasks[1].status == TaskStatus.PENDING

    def test_parse_tasks_with_json_object_list(self):
        """Test parse_tasks with a JSON string of task objects."""
        tasks = parse_tasks(
            '[{"id": "t1", "content": "Check pods", "status": "in_progress"}]'
        )
        assert len(tasks) == 1
        assert tasks[0].id == "t1"
        assert tasks[0].content == "Check pods"
        assert tasks[0].status == TaskStatus.IN_PROGRESS

    def test_parse_tasks_with_plain_string(self):
        """Test parse_tasks with a plain non-JSON string."""
        tasks = parse_tasks("Check single pod")
        assert len(tasks) == 1
        assert tasks[0].content == "Check single pod"
        assert tasks[0].status == TaskStatus.PENDING

    def test_parse_tasks_with_dict_list(self):
        """Test parse_tasks with a Python list of dicts."""
        data = [
            {"content": "Task 1", "status": "completed"},
            {"content": "Task 2", "status": "invalid_status"},
        ]
        tasks = parse_tasks(data)
        assert len(tasks) == 2
        assert tasks[0].status == TaskStatus.COMPLETED
        assert tasks[1].status == TaskStatus.PENDING

    def test_parse_tasks_skips_invalid_items(self):
        """Test parse_tasks gracefully skips unparseable items."""
        data = [None, "", {"non_task_field": 123}, {"content": "Valid task"}]
        tasks = parse_tasks(data)
        assert len(tasks) == 1
        assert tasks[0].content == "Valid task"

    def test_parse_tasks_empty_inputs(self):
        """Test parse_tasks with empty or None input."""
        assert parse_tasks([]) == []
        assert parse_tasks(None) == []
        assert parse_tasks("") == []

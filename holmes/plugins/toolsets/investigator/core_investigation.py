import logging
import os
from typing import Any, Dict
from uuid import uuid4

from holmes.core.todo_tasks_formatter import format_tasks
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.investigator.model import Task, TaskStatus

TODO_WRITE_TOOL_NAME = "TodoWrite"


def parse_tasks(todos_data: Any) -> list[Task]:
    tasks = []

    for todo_item in todos_data:
        if isinstance(todo_item, dict):
            task = Task(
                id=todo_item.get("id", str(uuid4())),
                content=todo_item.get("content", ""),
                status=TaskStatus(todo_item.get("status", "pending")),
            )
            tasks.append(task)

    return tasks


class TodoWriteTool(Tool):
    name: str = TODO_WRITE_TOOL_NAME
    description: str = "Save investigation tasks to break down complex problems into manageable sub-tasks. ALWAYS provide the COMPLETE list of all tasks, not just the ones being updated."
    parameters: Dict[str, ToolParameter] = {
        "todos": ToolParameter(
            description="COMPLETE list of ALL tasks on the task list. Each task should have: id (string), content (string), status (pending/in_progress/completed/failed)",
            type="array",
            required=True,
            items=ToolParameter(
                type="object",
                properties={
                    "id": ToolParameter(type="string", required=True),
                    "content": ToolParameter(type="string", required=True),
                    "status": ToolParameter(
                        type="string",
                        required=True,
                        enum=["pending", "in_progress", "completed", "failed"],
                    ),
                },
            ),
        ),
    }

    # Print a nice table to console/log
    def print_tasks_table(self, tasks):
        if not tasks:
            logging.info("No tasks in the investigation plan.")
            return

        status_icons = {
            "pending": "[ ]",
            "in_progress": "[~]",
            "completed": "[✓]",
            "failed": "[✗]",
        }

        max_id_width = max(len(str(task.id)) for task in tasks)
        max_content_width = max(len(task.content) for task in tasks)
        max_status_display_width = max(
            len(f"{status_icons[task.status.value]} {task.status.value}")
            for task in tasks
        )

        id_width = max(max_id_width, len("ID"))
        content_width = max(max_content_width, len("Content"))
        status_width = max(max_status_display_width, len("Status"))

        separator = f"+{'-' * (id_width + 2)}+{'-' * (content_width + 2)}+{'-' * (status_width + 2)}+"
        header = f"| {'ID':<{id_width}} | {'Content':<{content_width}} | {'Status':<{status_width}} |"
        tasks_to_display = []

        for task in tasks:
            status_display = f"{status_icons[task.status.value]} {task.status.value}"
            row = f"| {task.id:<{id_width}} | {task.content:<{content_width}} | {status_display:<{status_width}} |"
            tasks_to_display.append(row)

        logging.info(
            f"Task List:\n{separator}\n{header}\n{separator}\n"
            + "\n".join(tasks_to_display)
            + f"\n{separator}"
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            todos_data = params.get("todos", [])

            tasks = parse_tasks(todos_data=todos_data)

            logging.debug(f"Tasks: {len(tasks)}")

            self.print_tasks_table(tasks)
            formatted_tasks = format_tasks(tasks)

            response_data = f"✅ Investigation plan updated with {len(tasks)} tasks. Tasks are now stored in session and will appear in subsequent prompts.\n\n"
            if formatted_tasks:
                response_data += formatted_tasks
            else:
                response_data += "No tasks currently in the investigation plan."

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response_data,
                params=params,
            )

        except Exception as e:
            logging.exception("error using todowrite tool")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to process tasks: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return "Update investigation tasks"


class CoreInvestigationToolset(Toolset):
    """Core toolset for investigation management and task planning."""

    def __init__(self):
        super().__init__(
            name="core_investigation",
            description="Core investigation tools for task management and planning",
            enabled=True,
            tools=[TodoWriteTool()],
            tags=[ToolsetTag.CORE],
            is_default=True,
        )

    def get_example_config(self) -> Dict[str, Any]:
        return {}

    def _reload_instructions(self):
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "investigator_instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

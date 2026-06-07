import logging
import os
from typing import Any, Dict
from uuid import uuid4

display_logger = logging.getLogger("holmes.display.core_investigation")

from holmes.core.todo_tasks_formatter import format_tasks
from holmes.core.hypothesis_formatter import format_hypotheses
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.investigator.model import (
    Hypothesis,
    HypothesisStatus,
    Task,
    TaskStatus,
)

TODO_WRITE_TOOL_NAME = "TodoWrite"
HYPOTHESIS_WRITE_TOOL_NAME = "HypothesisWrite"


def parse_hypotheses(hypotheses_data: Any) -> list[Hypothesis]:
    hypotheses = []

    for item in hypotheses_data:
        if isinstance(item, dict):
            hypotheses.append(
                Hypothesis(
                    id=item.get("id", str(uuid4())),
                    statement=item.get("statement", ""),
                    status=HypothesisStatus(item.get("status", "proposed")),
                    evidence=item.get("evidence", "") or "",
                )
            )

    return hypotheses


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
            display_logger.info("No tasks in the investigation plan.")
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

        display_logger.info(
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


class HypothesisWriteTool(Tool):
    name: str = HYPOTHESIS_WRITE_TOOL_NAME
    description: str = (
        "Track competing root-cause hypotheses while investigating, so you weigh "
        "the evidence for each candidate cause instead of latching onto the first "
        "or loudest signal. ALWAYS provide the COMPLETE list of all hypotheses, "
        "not just the ones being updated."
    )
    parameters: Dict[str, ToolParameter] = {
        "hypotheses": ToolParameter(
            description=(
                "COMPLETE list of ALL root-cause hypotheses. Each hypothesis has: "
                "id (string), statement (string - the candidate root cause), "
                "status (proposed/investigating/supported/refuted), and evidence "
                "(string - what supports or refutes it)."
            ),
            type="array",
            required=True,
            items=ToolParameter(
                type="object",
                properties={
                    "id": ToolParameter(type="string", required=True),
                    "statement": ToolParameter(type="string", required=True),
                    "status": ToolParameter(
                        type="string",
                        required=True,
                        enum=["proposed", "investigating", "supported", "refuted"],
                    ),
                    "evidence": ToolParameter(type="string", required=False),
                },
            ),
        ),
    }

    def print_hypotheses_table(self, hypotheses):
        if not hypotheses:
            display_logger.info("No root-cause hypotheses tracked yet.")
            return

        status_icons = {
            "proposed": "[?]",
            "investigating": "[~]",
            "supported": "[✓]",
            "refuted": "[✗]",
        }

        lines = []
        for h in hypotheses:
            icon = status_icons.get(h.status.value, "[?]")
            line = f"  {icon} [{h.id}] ({h.status.value}) {h.statement}"
            if h.evidence:
                line += f" — {h.evidence}"
            lines.append(line)

        display_logger.info("Root-cause hypotheses:\n" + "\n".join(lines))

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            hypotheses = parse_hypotheses(params.get("hypotheses", []))

            self.print_hypotheses_table(hypotheses)
            formatted = format_hypotheses(hypotheses)

            response_data = (
                f"✅ Updated root-cause hypotheses ({len(hypotheses)} tracked). "
                "These are now stored in session and will appear in subsequent prompts.\n\n"
            )
            response_data += formatted or "No hypotheses currently tracked."

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response_data,
                params=params,
            )
        except Exception as e:
            logging.exception("error using HypothesisWrite tool")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to process hypotheses: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return "Update root-cause hypotheses"


class CoreInvestigationToolset(Toolset):
    """Core toolset for investigation management and task planning."""

    def __init__(self):
        super().__init__(
            name="core_investigation",
            description="Core investigation tools for task management and planning",
            enabled=True,
            tools=[TodoWriteTool(), HypothesisWriteTool()],
            tags=[ToolsetTag.CORE],
        )

    def _reload_instructions(self):
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "investigator_instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

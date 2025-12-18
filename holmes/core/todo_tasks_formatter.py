from typing import List

from holmes.plugins.toolsets.investigator.model import Task, TaskStatus


def format_tasks(tasks: List[Task]) -> str:
    """
    Format tasks for tool response
    Returns empty string if no tasks exist.
    """
    if not tasks:
        return ""

    status_order = {
        TaskStatus.PENDING: 0,
        TaskStatus.IN_PROGRESS: 1,
        TaskStatus.COMPLETED: 2,
    }

    sorted_tasks = sorted(
        tasks,
        key=lambda t: (status_order.get(t.status, 3),),
    )

    lines = ["# CURRENT INVESTIGATION TASKS"]
    lines.append("")

    pending_count = sum(1 for t in tasks if t.status == TaskStatus.PENDING)
    progress_count = sum(1 for t in tasks if t.status == TaskStatus.IN_PROGRESS)
    completed_count = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)

    lines.append(
        f"**Task Status**: {completed_count} completed, {progress_count} in progress, {pending_count} pending"
    )
    lines.append("")

    for task in sorted_tasks:
        status_indicator = {
            TaskStatus.PENDING: "[ ]",
            TaskStatus.IN_PROGRESS: "[~]",
            TaskStatus.COMPLETED: "[✓]",
        }.get(task.status, "[?]")

        lines.append(f"{status_indicator} [{task.id}] {task.content}")

    lines.append("")
    lines.append(
        "**Instructions**: Use TodoWrite tool to update task status as you work. Mark tasks as 'in_progress' when starting, 'completed' when finished."
    )

    return "\n".join(lines)

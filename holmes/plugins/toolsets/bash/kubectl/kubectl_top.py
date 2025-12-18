import argparse
from typing import Any, Optional

from holmes.plugins.toolsets.bash.common.bash_command import BashCommand
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.common.stringify import escape_shell_args


class KubectlTopCommand(BashCommand):
    def __init__(self):
        super().__init__("top")

    def add_parser(self, parent_parser: Any):
        parser = parent_parser.add_parser(
            "top",
            help="Display resource (CPU/memory) usage",
            exit_on_error=False,
        )
        parser.add_argument(
            "resource_type",
            choices=["nodes", "node", "pods", "pod"],
            help="Resource type to get usage for",
        )
        parser.add_argument(
            "options",
            nargs=argparse.REMAINDER,  # Captures all remaining arguments
            default=[],  # Default to an empty list
        )

    def validate_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> None:
        pass

    def stringify_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> str:
        parts = ["kubectl", "top", command.resource_type]

        parts += command.options

        return " ".join(escape_shell_args(parts))

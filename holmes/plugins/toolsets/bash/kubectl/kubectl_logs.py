import argparse
from typing import Any, Optional

from holmes.plugins.toolsets.bash.common.bash_command import BashCommand
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.common.stringify import escape_shell_args


class KubectlLogsCommand(BashCommand):
    def __init__(self):
        super().__init__("logs")

    def add_parser(self, parent_parser: Any):
        parser = parent_parser.add_parser(
            "logs",
            exit_on_error=False,
            add_help=False,  # Disable help to avoid conflicts
            prefix_chars="\x00",  # Use null character as prefix to disable option parsing
        )

        parser.add_argument(
            "options",
            nargs=argparse.REMAINDER,
            default=[],
        )

    def validate_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> None:
        pass

    def stringify_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> str:
        parts = ["kubectl", "logs"]

        parts += command.options

        return " ".join(escape_shell_args(parts))

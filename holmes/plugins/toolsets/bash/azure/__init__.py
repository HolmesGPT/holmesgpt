import argparse
from typing import Any, Optional

from holmes.plugins.toolsets.bash.common.bash_command import BashCommand
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.common.stringify import escape_shell_args
from holmes.plugins.toolsets.bash.common.validators import (
    validate_command_and_operations,
)
from holmes.plugins.toolsets.bash.azure.constants import (
    ALLOWED_AZURE_COMMANDS,
    DENIED_AZURE_COMMANDS,
)


class AzureCommand(BashCommand):
    def __init__(self):
        super().__init__("az")

    def add_parser(self, parent_parser: Any):
        azure_parser = parent_parser.add_parser(
            "az", help="Azure Command Line Interface", exit_on_error=False
        )

        azure_parser.add_argument(
            "service", help="Azure service or command (e.g., vm, network, storage)"
        )

        azure_parser.add_argument(
            "options",
            nargs=argparse.REMAINDER,
            default=[],
            help="Azure CLI subcommands, operations, and options",
        )
        return azure_parser

    def validate_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> None:
        if hasattr(command, "options"):
            validate_command_and_operations(
                command=command.service,
                options=command.options,
                allowed_commands=ALLOWED_AZURE_COMMANDS,
                denied_commands=DENIED_AZURE_COMMANDS,
            )

    def stringify_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> str:
        parts = ["az", command.service]

        if hasattr(command, "options") and command.options:
            parts.extend(command.options)

        return " ".join(escape_shell_args(parts))

import argparse
from typing import Any, Optional

from holmes.plugins.toolsets.bash.common.bash_command import BashCommand
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.common.stringify import escape_shell_args
from holmes.plugins.toolsets.bash.common.validators import (
    validate_command_and_operations,
)
from holmes.plugins.toolsets.bash.helm.constants import (
    ALLOWED_HELM_COMMANDS,
    DENIED_HELM_COMMANDS,
)


class HelmCommand(BashCommand):
    def __init__(self):
        super().__init__("helm")

    def add_parser(self, parent_parser: Any):
        """Create Helm CLI parser with safe command validation."""
        helm_parser = parent_parser.add_parser(
            "helm", help="Helm Package Manager for Kubernetes", exit_on_error=False
        )

        # Add command subparser
        helm_parser.add_argument(
            "command", help="Helm command (e.g., list, get, status, show)"
        )

        # Capture remaining arguments
        helm_parser.add_argument(
            "options",
            nargs=argparse.REMAINDER,
            default=[],
            help="Helm CLI subcommands, operations, and options",
        )
        return helm_parser

    def validate_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> None:
        if hasattr(command, "options"):
            validate_command_and_operations(
                command=command.command,
                options=command.options,
                allowed_commands=ALLOWED_HELM_COMMANDS,
                denied_commands=DENIED_HELM_COMMANDS,
            )

    def stringify_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> str:
        """Convert parsed Helm command back to safe command string."""
        # Build command parts
        parts = ["helm", command.command]

        if hasattr(command, "options") and command.options:
            parts.extend(command.options)

        return " ".join(escape_shell_args(parts))

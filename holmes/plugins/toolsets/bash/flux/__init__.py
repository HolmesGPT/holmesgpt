import argparse
from typing import Any, Optional

from holmes.plugins.toolsets.bash.common.bash_command import BashCommand
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.common.stringify import escape_shell_args
from holmes.plugins.toolsets.bash.common.validators import (
    validate_command_and_operations,
)
from holmes.plugins.toolsets.bash.flux.constants import (
    ALLOWED_FLUX_COMMANDS,
    DENIED_FLUX_COMMANDS,
)


class FluxCommand(BashCommand):
    def __init__(self):
        super().__init__("flux")

    def add_parser(self, parent_parser: Any):
        """Create Flux CLI parser with safe command validation."""
        flux_parser = parent_parser.add_parser(
            "flux", help="Flux CD Command Line Interface", exit_on_error=False
        )

        # Add command subparser
        flux_parser.add_argument(
            "command", help="Flux command (e.g., get, check, logs, events)"
        )

        # Capture remaining arguments
        flux_parser.add_argument(
            "options",
            nargs=argparse.REMAINDER,
            default=[],
            help="Flux CLI subcommands, operations, and options",
        )
        return flux_parser

    def validate_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> None:
        if hasattr(command, "options"):
            validate_command_and_operations(
                command.command,
                command.options,
                ALLOWED_FLUX_COMMANDS,
                DENIED_FLUX_COMMANDS,
            )

    def stringify_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> str:
        """Convert parsed Flux command back to safe command string."""
        parts = ["flux", command.command]

        if hasattr(command, "options") and command.options:
            parts.extend(command.options)

        return " ".join(escape_shell_args(parts))


def create_flux_parser(parent_parser: Any):
    flux_command = FluxCommand()
    return flux_command.add_parser(parent_parser)

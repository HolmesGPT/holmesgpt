import argparse
from typing import Any, Optional

from holmes.plugins.toolsets.bash.common.bash_command import BashCommand
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.common.stringify import escape_shell_args
from holmes.plugins.toolsets.bash.common.validators import (
    validate_command_and_operations,
)
from holmes.plugins.toolsets.bash.docker.constants import (
    ALLOWED_DOCKER_COMMANDS,
    DENIED_DOCKER_COMMANDS,
)


class DockerCommand(BashCommand):
    def __init__(self):
        super().__init__("docker")

    def add_parser(self, parent_parser: Any):
        docker_parser = parent_parser.add_parser(
            "docker",
            help="Docker Command Line Interface",
            exit_on_error=False,
        )

        docker_parser.add_argument(
            "command",
            help="Docker command (e.g., ps, images, inspect)",
        )

        docker_parser.add_argument(
            "options",
            nargs=argparse.REMAINDER,
            default=[],
            help="Docker CLI subcommands, operations, and options",
        )
        return docker_parser

    def validate_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> None:
        if hasattr(command, "options"):
            validate_command_and_operations(
                command=command.command,
                options=command.options,
                allowed_commands=ALLOWED_DOCKER_COMMANDS,
                denied_commands=DENIED_DOCKER_COMMANDS,
            )

    def stringify_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> str:
        parts = ["docker", command.command]

        if hasattr(command, "options") and command.options:
            parts.extend(command.options)

        return " ".join(escape_shell_args(parts))

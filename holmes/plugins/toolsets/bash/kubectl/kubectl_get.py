from typing import Any, Optional

import argparse
from holmes.plugins.toolsets.bash.common.stringify import escape_shell_args
from holmes.plugins.toolsets.bash.common.validators import (
    whitelist_validator,
)
from holmes.plugins.toolsets.bash.kubectl.constants import (
    VALID_RESOURCE_TYPES,
)

from holmes.plugins.toolsets.bash.common.bash_command import BashCommand
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig


class KubectlGetCommand(BashCommand):
    def __init__(self):
        super().__init__("get")

    def add_parser(self, parent_parser: Any):
        parser = parent_parser.add_parser(
            "get",
            help="Display one or many resources",
            exit_on_error=False,  # Important for library use
        )
        parser.add_argument(
            "resource_type",
            type=whitelist_validator("resource type", VALID_RESOURCE_TYPES),
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
        parts = ["kubectl", "get", command.resource_type]

        parts += command.options

        return " ".join(escape_shell_args(parts))

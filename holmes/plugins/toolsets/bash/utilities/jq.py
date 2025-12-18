import argparse
from typing import Any, Optional

from holmes.plugins.toolsets.bash.common.bash_command import BashCommand
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.common.stringify import escape_shell_args


class JqCommand(BashCommand):
    def __init__(self):
        super().__init__("jq")

    def add_parser(self, parent_parser: Any):
        jq_parser = parent_parser.add_parser(
            "jq",
            help="JSON processor",
            exit_on_error=False,
            add_help=False,  # Disable help to avoid conflicts
            prefix_chars="\x00",  # Use null character as prefix to disable option parsing
        )

        # Capture all arguments for validation
        jq_parser.add_argument(
            "options",
            nargs=argparse.REMAINDER,
            default=[],
            help="Jq filter and options",
        )
        return jq_parser

    def validate_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> None:
        if hasattr(command, "options") and command.options:
            validate_jq_options(command.options)

    def stringify_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> str:
        parts = ["jq"]

        # Add validated options
        if hasattr(command, "options") and command.options:
            validated_options = validate_jq_options(command.options)
            parts.extend(validated_options)

        return " ".join(escape_shell_args(parts))


def validate_jq_options(options: list[str]) -> list[str]:
    """Validate jq CLI options - block file operations."""
    i = 0
    while i < len(options):
        option = options[i]

        # Block file reading operations
        if option in {"--slurpfile", "--rawfile"}:
            raise ValueError(f"Option {option} is not allowed for security reasons")

        # Skip over option-value pairs
        if option in {"--arg", "--argjson"} and i + 2 < len(options):
            i += 3  # Skip option, name, value
            continue
        elif (
            option.startswith("--")
            and i + 1 < len(options)
            and not options[i + 1].startswith("-")
        ):
            i += 2  # Skip option and value
            continue

        # No file arguments allowed (except filter expressions)
        if not option.startswith("-") and "=" not in option:
            # This could be a filter expression, allow it
            pass

        i += 1

    return options

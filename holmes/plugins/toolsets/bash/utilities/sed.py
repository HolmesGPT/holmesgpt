import argparse
import re
from typing import Any, Optional

from holmes.plugins.toolsets.bash.common.bash_command import BashCommand
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.common.stringify import escape_shell_args

# Blocked sed commands for security
BLOCKED_SED_COMMANDS = {
    "w",  # Write to file
    "W",  # Write first line of pattern space to file
    "r",  # Read file
    "R",  # Read one line from file
    "e",  # Execute command
    "v",  # Version (in some contexts)
    "z",  # Clear pattern space (can be misused)
    "q",  # Quit (can be misused to skip processing)
    "Q",  # Quit immediately
}

# Pattern to detect dangerous sed commands
DANGEROUS_SED_PATTERN = re.compile(
    r"(?:^|;|\n)\s*(?:[0-9,]*\s*)?[wWrRezvqQ](?:\s|$|/)", re.MULTILINE
)


class SedCommand(BashCommand):
    def __init__(self):
        super().__init__("sed")

    def add_parser(self, parent_parser: Any):
        sed_parser = parent_parser.add_parser(
            "sed",
            help="Stream editor for filtering and transforming text",
            exit_on_error=False,
            add_help=False,  # Disable help to avoid conflicts
            prefix_chars="\x00",  # Use null character as prefix to disable option parsing
        )

        # Capture all arguments for validation
        sed_parser.add_argument(
            "options",
            nargs=argparse.REMAINDER,
            default=[],
            help="Sed script and options",
        )
        return sed_parser

    def validate_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> None:
        if hasattr(command, "options") and command.options:
            validate_sed_options(command.options)

    def stringify_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> str:
        parts = ["sed"]

        # Add validated options
        if hasattr(command, "options") and command.options:
            validated_options = validate_sed_options(command.options)
            parts.extend(validated_options)

        return " ".join(escape_shell_args(parts))


def validate_sed_script(script: str) -> None:
    """Validate a sed script for safety."""
    # Check for blocked commands - only check for actual command patterns
    if DANGEROUS_SED_PATTERN.search(script):
        raise ValueError("sed script contains blocked commands (w, r, e, q, etc.)")

    # Check for execute command specifically (more targeted)
    if re.search(r"(?:^|;|\n)\s*(?:[0-9,]*\s*)?e\b", script, re.MULTILINE):
        raise ValueError("sed script contains execute commands")

    # Check for file operations (simpler pattern to catch write/read commands)
    if re.search(r"[wWrR]\s+\S", script):
        raise ValueError("sed script contains file operations")


def validate_sed_options(options: list[str]) -> list[str]:
    """Validate sed CLI options - block file operations and in-place editing."""
    i = 0
    script_found = False

    while i < len(options):
        option = options[i]

        # Check for attached/inlined forms of blocked options
        if (option.startswith("-i") and len(option) > 2) or option.startswith(
            "--in-place="
        ):
            raise ValueError(
                f"Attached in-place option {option} is not allowed for security reasons"
            )
        elif (option.startswith("-f") and len(option) > 2) or option.startswith(
            "--file="
        ):
            raise ValueError(
                f"Attached file option {option} is not allowed for security reasons"
            )

        # Block file reading and in-place editing for security
        elif option in {"-f", "--file", "-i", "--in-place"}:
            raise ValueError(f"Option {option} is not allowed for security reasons")

        # Handle -e and --expression with attached scripts
        elif option.startswith("-e") and len(option) > 2:
            # Handle -eSCRIPT form
            script = option[2:]  # Extract script after "-e"
            validate_sed_script(script)
            script_found = True
            i += 1
            continue
        elif option.startswith("--expression="):
            # Handle --expression=SCRIPT form
            script = option[13:]  # Extract script after "--expression="
            validate_sed_script(script)
            script_found = True
            i += 1
            continue

        # Handle -e and --expression with separate arguments
        elif option in {"-e", "--expression"}:
            if i + 1 >= len(options) or options[i + 1].startswith("-"):
                raise ValueError(f"Option {option} requires a script argument")
            script = options[i + 1]
            validate_sed_script(script)
            script_found = True
            i += 2
            continue

        # Handle long options with values (--opt=val)
        elif "=" in option and option.startswith("--"):
            # Long option with attached value, skip as single unit
            i += 1
            continue

        # Handle other long options with separate values
        elif (
            option.startswith("--")
            and i + 1 < len(options)
            and not options[i + 1].startswith("-")
        ):
            i += 2  # Skip option and value
            continue

        # Handle sed script (non-flag argument)
        elif not option.startswith("-"):
            if not script_found:
                validate_sed_script(option)
                script_found = True
            else:
                # Multiple scripts not allowed
                pass
            i += 1
            continue

        i += 1

    return options

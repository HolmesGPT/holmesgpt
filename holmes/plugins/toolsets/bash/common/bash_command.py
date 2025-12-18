from abc import ABC, abstractmethod
import argparse
from typing import Any, Optional

from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.common.stringify import escape_shell_args


class BashCommand(ABC):
    """Abstract base class for bash command implementations."""

    def __init__(self, name: str):
        super().__init__()
        self.name = name

    @abstractmethod
    def add_parser(self, parent_parser: Any):
        """Return the argument parser for this command."""
        pass

    @abstractmethod
    def validate_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> None:
        """
        Validate the parsed command to ensure it's safe.
        Raises ValueError if validation fails.
        """
        pass

    @abstractmethod
    def stringify_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> str:
        """
        Convert the parsed command back to a safe command string.
        """
        pass


class SimpleBashCommand(BashCommand):
    def __init__(
        self,
        name: str,
        allowed_options: Optional[list[str]] = None,
        denied_options: Optional[list[str]] = None,
    ):
        """
        A simple bash command that works with a whitelist/blacklist of options
        If allowed_options is not empty, an option MUST be present in the allowed_options to be allowed
        If denied_options is not empty, an option MUST NOT be present in the denied_options to be allowed
        """
        super().__init__(name)
        self.allowed_options = allowed_options or []
        self.denied_options = denied_options or []

    def add_parser(self, parent_parser: Any):
        parser = parent_parser.add_parser(
            self.name,
            exit_on_error=False,
            add_help=False,  # Disable help to avoid conflicts
            prefix_chars="\x00",  # Use null character as prefix to disable option parsing
        )

        parser.add_argument(
            "options",
            nargs=argparse.REMAINDER,
            default=[],
        )
        return parser

    def validate_command(self, command, original_command, config):
        for option in command.options:
            allowed = False if self.allowed_options else True

            # Check allowed options
            for allowed_option in self.allowed_options:
                if option == allowed_option:
                    allowed = True
                    break

            # Check denied options
            denied = False
            denied_error_message = None
            for denied_option in self.denied_options:
                # Check for exact match
                if option == denied_option:
                    denied = True
                    denied_error_message = (
                        f"Option {option} is not allowed for security reasons"
                    )
                    break
                # Check for long option equals-form variant (--option=value)
                elif denied_option.startswith("--") and option.startswith(
                    denied_option + "="
                ):
                    denied = True
                    denied_error_message = (
                        f"Option {option} is not allowed for security reasons"
                    )
                    break
                # Check for short option with attached value (-Tvalue)
                elif (
                    denied_option.startswith("-")
                    and not denied_option.startswith("--")
                    and len(denied_option) == 2
                    and option.startswith(denied_option)
                    and len(option) > 2
                ):
                    denied = True
                    denied_error_message = (
                        f"Option {option} is not allowed for security reasons"
                    )
                    break

            # Raise errors with appropriate messages
            if denied:
                raise ValueError(denied_error_message)
            elif not allowed:
                raise ValueError(
                    f"option {option} is not part of the allowed options: {self.allowed_options}"
                )

    def stringify_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> str:
        parts = [self.name]

        parts.extend(command.options)

        return " ".join(escape_shell_args(parts))

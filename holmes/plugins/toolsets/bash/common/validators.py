import argparse
import fnmatch
import re
from typing import Union


def regex_validator(field_name: str, pattern: Union[str, re.Pattern]):
    def check_regex(arg_value):
        if not re.match(pattern, arg_value):
            raise argparse.ArgumentTypeError(f"invalid {field_name} value")
        return arg_value

    return check_regex


def whitelist_validator(field_name: str, whitelisted_values: set[str]):
    def validate_value(value: str) -> str:
        if value not in whitelisted_values:
            whitelisted_values_str = ", ".join(whitelisted_values)
            raise argparse.ArgumentTypeError(
                f"Invalid {field_name} format: '{value}'. Must be one of [{whitelisted_values_str}]"
            )
        return value

    return validate_value


def validate_command_and_operations(
    command: str,
    options: list[str],
    allowed_commands: dict[str, dict],
    denied_commands: dict[str, dict],
) -> None:
    """Validate that the command and operation combination is safe, with wildcard support."""

    # Check denied commands first (including wildcards)
    _check_denied_command_with_wildcards(
        command=command, options=options, denied_commands=denied_commands
    )

    # Check allowed commands (including wildcards)
    _check_allowed_command_with_wildcards(
        command=command, options=options, allowed_commands=allowed_commands
    )


def _check_options_against_denied_commands(
    command: str, options: list[str], denied_commands: dict
):
    for idx, option in enumerate(options):
        new_command = command + " " + option
        for potential_command, children in denied_commands.items():
            option_does_match = fnmatch.fnmatchcase(option, potential_command)
            if option_does_match and children == {}:
                raise ValueError(f"Command is blocked: {new_command}")
            elif option_does_match:
                _check_options_against_denied_commands(
                    command=new_command,
                    options=options[idx + 1 :],
                    denied_commands=children,
                )


def _check_denied_command_with_wildcards(
    command: str, options: list[str], denied_commands: dict[str, dict]
) -> None:
    # Check exact command match first
    for potential_command, children in denied_commands.items():
        command_does_match = fnmatch.fnmatchcase(command, potential_command)
        if command_does_match and children == {}:
            raise ValueError(f"Command is blocked: {command}")
        elif command_does_match:
            _check_options_against_denied_commands(
                command=command, options=options, denied_commands=children
            )


def _do_options_match_an_allowed_command(
    command: str, options: list[str], allowed_commands: dict
) -> bool:
    for idx, option in enumerate(options):
        new_command = command + " " + option
        for potential_command, children in allowed_commands.items():
            option_does_match = fnmatch.fnmatchcase(option, potential_command)
            if option_does_match and children == {}:
                return True
            elif option_does_match:
                is_allowed = _do_options_match_an_allowed_command(
                    command=new_command,
                    options=options[idx + 1 :],
                    allowed_commands=children,
                )
                if is_allowed:
                    return True

    return False


def _check_allowed_command_with_wildcards(
    command: str, options: list[str], allowed_commands: dict[str, dict]
):
    for potential_command, children in allowed_commands.items():
        cursor_does_match = fnmatch.fnmatchcase(command, potential_command)
        if cursor_does_match and children == {}:
            return
        elif cursor_does_match:
            is_allowed = _do_options_match_an_allowed_command(
                command=command, options=options, allowed_commands=children
            )
            if is_allowed:
                return

    raise ValueError(
        f"Command is not in the allowlist: {command + ' ' + ' '.join(options)}"
    )

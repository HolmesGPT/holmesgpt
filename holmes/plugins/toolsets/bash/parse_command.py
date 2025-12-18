import argparse
import logging
import shlex
from typing import Any, Optional

from holmes.plugins.toolsets.bash.common.bash_command import BashCommand
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.kubectl import KubectlCommand
from holmes.plugins.toolsets.bash.aws import AWSCommand
from holmes.plugins.toolsets.bash.azure import AzureCommand
from holmes.plugins.toolsets.bash.argocd import ArgocdCommand
from holmes.plugins.toolsets.bash.docker import DockerCommand
from holmes.plugins.toolsets.bash.helm import HelmCommand

# Utilities imports - all now use Command classes
from holmes.plugins.toolsets.bash.utilities.wc import WCCommand
from holmes.plugins.toolsets.bash.utilities.cut import CutCommand
from holmes.plugins.toolsets.bash.utilities.sort import SortCommand
from holmes.plugins.toolsets.bash.utilities.uniq import UniqCommand
from holmes.plugins.toolsets.bash.utilities.head import HeadCommand
from holmes.plugins.toolsets.bash.utilities.tail import TailCommand
from holmes.plugins.toolsets.bash.utilities.tr import TrCommand
from holmes.plugins.toolsets.bash.utilities.base64_util import Base64Command
from holmes.plugins.toolsets.bash.utilities.jq import JqCommand
from holmes.plugins.toolsets.bash.utilities.sed import SedCommand
from holmes.plugins.toolsets.bash.utilities.grep import GrepCommand


# All commands now use BashCommand classes
AVAILABLE_COMMANDS: list[BashCommand] = [
    WCCommand(),
    KubectlCommand(),
    AWSCommand(),
    AzureCommand(),
    ArgocdCommand(),
    DockerCommand(),
    HelmCommand(),
    GrepCommand(),
    CutCommand(),
    SortCommand(),
    UniqCommand(),
    HeadCommand(),
    TailCommand(),
    TrCommand(),
    Base64Command(),
    JqCommand(),
    SedCommand(),
]

command_name_to_command_map: dict[str, BashCommand] = {
    cmd.name: cmd for cmd in AVAILABLE_COMMANDS
}


class QuietArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _print_message(self, message, file=None):
        if message:
            logging.debug(message.strip())

    def error(self, message):
        logging.debug(f"Error: {message}")
        self.exit(2)


def create_parser() -> argparse.ArgumentParser:
    parser = QuietArgumentParser(
        prog="command_parser",  # Set explicit program name
        description="Parser for commands",
        exit_on_error=False,
        add_help=False,  # Disable help to avoid conflicts with -h in subcommands
    )
    commands_parser = parser.add_subparsers(
        dest="cmd", required=True, help="The tool to command (e.g., kubectl)"
    )

    # Add all BashCommand classes
    for command in AVAILABLE_COMMANDS:
        command.add_parser(commands_parser)

    return parser


def validate_command(
    command: Any, original_command: str, config: Optional[BashExecutorConfig]
):
    bash_command_instance = command_name_to_command_map.get(command.cmd)

    if bash_command_instance:
        bash_command_instance.validate_command(command, original_command, config)


def stringify_command(
    command: Any, original_command: str, config: Optional[BashExecutorConfig]
) -> str:
    bash_command_instance = command_name_to_command_map.get(command.cmd)

    if bash_command_instance:
        return bash_command_instance.stringify_command(
            command, original_command, config
        )
    else:
        # This code path should not happen b/c the parsing of the command should catch an unsupported command
        supported_commands = [cmd.name for cmd in AVAILABLE_COMMANDS]
        raise ValueError(
            f"Unsupported command '{command.cmd}' in {original_command}. Supported commands are: {', '.join(supported_commands)}"
        )


command_parser = create_parser()


def split_into_separate_commands(command_str: str) -> list[list[str]]:
    """
    Splits a single bash command into sub commands based on the pipe '|' delimiter.

    Example:
        >>> "ls -l" -> [
            ['ls', '-l']
        ]
        >>> "kubectl get pods | grep holmes" -> [
            ['kubectl', 'get', 'pods'],
            ['grep', 'holmes']
        ]
    """
    parts = shlex.split(command_str)
    if not parts:
        return []

    commands_list: list[list[str]] = []
    current_command: list[str] = []

    for part in parts:
        if part == "|":
            if current_command:
                commands_list.append(current_command)
            current_command = []
        elif part == "&&":
            raise ValueError(
                'Double ampersand "&&" is not a supported way to chain commands. Run each command separately.'
            )
        else:
            current_command.append(part)

    if current_command:
        commands_list.append(current_command)

    return commands_list


def make_command_safe(command_str: str, config: Optional[BashExecutorConfig]) -> str:
    commands = split_into_separate_commands(command_str)

    parsed_commands = []
    for individual_command in commands:
        try:
            parsed_commands.append(command_parser.parse_args(individual_command))

        except SystemExit:
            # argparse throws a SystemExit error when it can't parse command or arguments
            # This ideally should be captured differently by ensuring all possible args
            # are accounted for in the implementation for each command.
            # When falling back, we raise a generic error
            raise ValueError(
                f"The following command failed to be parsed for safety: {command_str}"
            ) from None
    for command in parsed_commands:
        validate_command(command=command, original_command=command_str, config=config)

    safe_commands_str = [
        stringify_command(command=command, original_command=command_str, config=config)
        for command in parsed_commands
    ]

    return " | ".join(safe_commands_str)

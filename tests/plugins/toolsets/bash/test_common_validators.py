import pytest
from holmes.plugins.toolsets.bash.common.validators import (
    validate_command_and_operations,
)

ALLOWED_COMMANDS: dict[str, dict] = {
    "list": {},
    "ls": {},
    "plugin": {"list": {}, "common-wildcard-*": {}},
    "get": {
        "all": {},
        "hooks": {},
        "manifest": {},
        "notes": {},
        "values": {},
        "metadata-*": {},
    },
}

DENIED_COMMANDS: dict[str, dict] = {
    # Repository management
    "repo": {
        "add": {},
        "remove": {},
    },
    "get": {
        "nested": {"option": {}},
    },
    # Chart packaging and publishing
    "create": {},
    # Plugin management
    "plugin": {
        "install": {},
        "uninstall": {},
        "update-*": {},
        "common-wildcard-denied": {},  # deny overrides allow wildcard
    },
}


@pytest.mark.parametrize(
    "command,options",
    [
        ("list", []),
        ("get", ["all"]),
        ("get", ["all", "foo"]),
        ("get", ["all", "--option"]),
        ("get", ["metadata-wildcard1", "--option"]),
        ("get", ["metadata-wildcard2"]),
        ("plugin", ["list"]),
        ("plugin", ["common-wildcard-allowed"]),
    ],
)
def test_validate_command_and_operations(command: str, options: list[str]):
    validate_command_and_operations(
        command=command,
        options=options,
        allowed_commands=ALLOWED_COMMANDS,
        denied_commands=DENIED_COMMANDS,
    )


@pytest.mark.parametrize(
    "command,options",
    [
        ("get", []),
        ("plugin", ["install"]),
        ("plugin", ["unknown"]),
        ("plugin", ["unknown"]),
        ("create", ["all"]),
        ("plugin", ["common-wildcard-denied"]),
    ],
)
def test_invalid_command_and_operations(command: str, options: list[str]):
    with pytest.raises(ValueError):
        validate_command_and_operations(
            command=command,
            options=options,
            allowed_commands=ALLOWED_COMMANDS,
            denied_commands=DENIED_COMMANDS,
        )


def test_deny_message():
    with pytest.raises(ValueError, match="Command is blocked: get nested option"):
        validate_command_and_operations(
            command="get",
            options=["nested", "option", "--name", "myblob"],
            allowed_commands=ALLOWED_COMMANDS,
            denied_commands=DENIED_COMMANDS,
        )

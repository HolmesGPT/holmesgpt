from typing import Any, Optional
from holmes.plugins.toolsets.bash.common.bash_command import BashCommand
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.kubectl.constants import (
    SAFE_NAME_PATTERN,
    SAFE_NAMESPACE_PATTERN,
    SAFE_SELECTOR_PATTERN,
    VALID_RESOURCE_TYPES,
)
from holmes.plugins.toolsets.bash.kubectl.kubectl_describe import KubectlDescribeCommand
from holmes.plugins.toolsets.bash.kubectl.kubectl_events import KubectlEventsCommand
from holmes.plugins.toolsets.bash.kubectl.kubectl_logs import KubectlLogsCommand
from holmes.plugins.toolsets.bash.kubectl.kubectl_top import KubectlTopCommand
from holmes.plugins.toolsets.bash.kubectl.kubectl_get import KubectlGetCommand


class KubectlCommand(BashCommand):
    def __init__(self):
        super().__init__("kubectl")

        self.sub_commands = [
            KubectlDescribeCommand(),
            KubectlEventsCommand(),
            KubectlLogsCommand(),
            KubectlTopCommand(),
            KubectlGetCommand(),
        ]

    def add_parser(self, parent_parser: Any):
        kubectl_parser = parent_parser.add_parser(
            "kubectl", help="Kubernetes command-line tool", exit_on_error=False
        )
        action_subparsers = kubectl_parser.add_subparsers(
            dest="action",
            required=True,
            help="Action to perform (e.g., get, apply, delete)",
        )

        for sub_command in self.sub_commands:
            sub_command.add_parser(action_subparsers)

    def validate_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> None:
        """
        Validate common kubectl command fields to prevent injection attacks.
        Raises ValueError if validation fails.
        """

        # Validate resource type
        if (
            hasattr(command, "resource_type")
            and command.resource_type.lower() not in VALID_RESOURCE_TYPES
        ):
            raise ValueError(f"Invalid resource type: {command.resource_type}")

        # Validate resource name if provided
        if hasattr(command, "resource_name") and command.resource_name:
            if not SAFE_NAME_PATTERN.match(command.resource_name):
                raise ValueError(f"Invalid resource name: {command.resource_name}")
            if len(command.resource_name) > 253:
                raise ValueError("Resource name too long")

        # Validate namespace if provided
        if hasattr(command, "namespace") and command.namespace:
            if not SAFE_NAMESPACE_PATTERN.match(command.namespace):
                raise ValueError(f"Invalid namespace: {command.namespace}")
            if len(command.namespace) > 63:
                raise ValueError("Namespace name too long")

        # Validate selectors if provided
        if hasattr(command, "selector") and command.selector:
            if not SAFE_SELECTOR_PATTERN.match(command.selector):
                raise ValueError(f"Invalid label selector: {command.selector}")
            if len(command.selector) > 1000:
                raise ValueError("Label selector too long")

        # Delegate to sub-command-specific validation
        if hasattr(command, "action"):
            for sub_command in self.sub_commands:
                if command.action == sub_command.name:
                    if hasattr(sub_command, "validate_command"):
                        sub_command.validate_command(command, original_command, config)
                    break

    def stringify_command(
        self, command: Any, original_command: str, config: Optional[BashExecutorConfig]
    ) -> str:
        if command.cmd == "kubectl":
            for sub_command in self.sub_commands:
                if command.action == sub_command.name:
                    return sub_command.stringify_command(
                        command=command,
                        original_command=original_command,
                        config=config,
                    )
            raise ValueError(
                f"Unsupported {command.tool_name} action {command.action}. Supported actions are: get, describe, events, top, run"
            )
        else:
            raise ValueError(f"Unsupported command {command.tool_name}")

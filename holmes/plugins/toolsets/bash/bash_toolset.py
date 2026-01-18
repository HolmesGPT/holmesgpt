"""
Bash toolset with prefix-based command validation.

This toolset enables bash command execution with dynamic whitelisting.
Commands are validated against allow/deny lists using prefix matching.
"""

import logging
import os
import random
import re
import string
from typing import Any, Dict, Optional

import sentry_sdk

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.toolsets.bash.common.bash import BashResult, execute_bash_command
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.kubectl.constants import SAFE_NAMESPACE_PATTERN
from holmes.plugins.toolsets.bash.kubectl.kubectl_run import validate_image_and_commands
from holmes.plugins.toolsets.bash.validation import (
    DenyReason,
    ValidationStatus,
    get_effective_lists,
    validate_command,
)
from holmes.plugins.toolsets.utils import get_param_or_raise


def bash_result_to_structured(
    result: BashResult, cmd: str, timeout: int, params: dict
) -> StructuredToolResult:
    """
    Convert a BashResult to a StructuredToolResult.

    Args:
        result: The BashResult from execute_bash_command
        cmd: The original command (for error messages)
        timeout: The timeout value (for error messages)
        params: Parameters to include in the result

    Returns:
        StructuredToolResult suitable for the tool response
    """
    if result.timed_out:
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=f"Error: Command '{cmd}' timed out after {timeout} seconds.",
            data=f"{cmd}\n{result.stdout}" if result.stdout else None,
            params=params,
            invocation=cmd,
        )

    result_data = f"{cmd}\n{result.stdout}"

    if result.return_code == 0:
        status = (
            StructuredToolResultStatus.SUCCESS
            if result.stdout
            else StructuredToolResultStatus.NO_DATA
        )
        error = None
    else:
        status = StructuredToolResultStatus.ERROR
        error = (
            f'Error: Command "{cmd}" returned non-zero exit status {result.return_code}'
        )

    return StructuredToolResult(
        status=status,
        error=error,
        data=result_data,
        params=params,
        invocation=cmd,
        return_code=result.return_code,
    )


class BaseBashExecutorToolset(Toolset):
    config: Optional[BashExecutorConfig] = None

    def get_example_config(self):
        example_config = BashExecutorConfig()
        return example_config.model_dump()


class BaseBashTool(Tool):
    toolset: BaseBashExecutorToolset


class KubectlRunImageCommand(BaseBashTool):
    """Tool for running a container image via kubectl run."""

    def __init__(self, toolset: BaseBashExecutorToolset):
        super().__init__(
            name="kubectl_run_image",
            description=(
                "Executes `kubectl run <name> --image=<image> ... -- <command>` return the result"
            ),
            parameters={
                "image": ToolParameter(
                    description="The image to run",
                    type="string",
                    required=True,
                ),
                "command": ToolParameter(
                    description="The command to execute on the deployed pod",
                    type="string",
                    required=True,
                ),
                "namespace": ToolParameter(
                    description="The namespace in which to deploy the temporary pod",
                    type="string",
                    required=False,
                ),
                "timeout": ToolParameter(
                    description=(
                        "Optional timeout in seconds for the command execution. "
                        "Defaults to 60s."
                    ),
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _build_kubectl_command(self, params: dict, pod_name: str) -> str:
        namespace = params.get("namespace", "default")
        image = get_param_or_raise(params, "image")
        command_str = get_param_or_raise(params, "command")
        return f"kubectl run {pod_name} --image={image} --namespace={namespace} --rm --attach --restart=Never -i -- {command_str}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        timeout = params.get("timeout", 60)

        image = get_param_or_raise(params, "image")
        command_str = get_param_or_raise(params, "command")

        namespace = params.get("namespace")

        if namespace and not re.match(SAFE_NAMESPACE_PATTERN, namespace):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Error: The namespace is invalid. Valid namespaces must match the following regexp: {SAFE_NAMESPACE_PATTERN}",
                params=params,
            )

        try:
            validate_image_and_commands(
                image=image, container_command=command_str, config=self.toolset.config
            )
        except ValueError as e:
            # Report unsafe kubectl run command attempt to Sentry
            sentry_sdk.capture_event(
                {
                    "message": f"Unsafe kubectl run command attempted: {image}",
                    "level": "warning",
                    "extra": {
                        "image": image,
                        "command": command_str,
                        "namespace": namespace,
                        "error": str(e),
                    },
                }
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

        pod_name = (
            "holmesgpt-debug-pod-"
            + "".join(random.choices(string.ascii_letters, k=8)).lower()
        )
        full_kubectl_command = self._build_kubectl_command(params, pod_name)
        try:
            result = execute_bash_command(cmd=full_kubectl_command, timeout=timeout)
        except FileNotFoundError:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Error: Bash executable not found. Ensure /bin/bash is available.",
                params=params,
                invocation=full_kubectl_command,
            )
        return bash_result_to_structured(result, full_kubectl_command, timeout, params)

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        return self._build_kubectl_command(params, "<pod_name>")


class RunBashCommand(BaseBashTool):
    """
    Tool for executing bash commands with prefix-based validation.

    Commands are validated against allow/deny lists using the suggested_prefixes
    parameter. Each command segment (separated by |, &&, etc.) requires its own prefix.
    """

    def __init__(self, toolset: BaseBashExecutorToolset):
        super().__init__(
            name="bash",
            description=(
                "Executes a simple one-liner bash command and returns its output. "
                "Only supports: single commands, pipes (|), && , ||, ;, &. "
                "NOT supported: for/while/until loops, if/case statements, subshells $() or backticks. "
                "You must provide suggested_prefixes - one prefix per command segment. "
                "Example: for 'kubectl get pods | grep error', provide "
                "suggested_prefixes=['kubectl get', 'grep']."
            ),
            parameters={
                "command": ToolParameter(
                    description="The bash command string to execute.",
                    type="string",
                    required=True,
                ),
                "suggested_prefixes": ToolParameter(
                    description=(
                        "Array of command prefixes, one per command segment. "
                        "Include command name and subcommand (e.g., 'kubectl get', 'grep'). "
                        "Do NOT include resource names, namespaces, or flag values."
                    ),
                    type="array",
                    items=ToolParameter(type="string"),
                    required=True,
                ),
                "timeout": ToolParameter(
                    description=(
                        "Optional timeout in seconds for the command execution. "
                        "Defaults to 30s."
                    ),
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        command_str = params.get("command")
        suggested_prefixes = params.get("suggested_prefixes", [])
        timeout = params.get("timeout", 30)

        # Validate required parameters
        if not command_str:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="The 'command' parameter is required and was not provided.",
                params=params,
            )

        if not isinstance(command_str, str):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"The 'command' parameter must be a string, got {type(command_str).__name__}.",
                params=params,
            )

        if not suggested_prefixes:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="The 'suggested_prefixes' parameter is required. Provide one prefix per command segment.",
                params=params,
            )

        if not isinstance(suggested_prefixes, list):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"The 'suggested_prefixes' parameter must be an array, got {type(suggested_prefixes).__name__}.",
                params=params,
            )

        # Refresh CLI-approved prefixes (in case user approved new ones this session)
        if hasattr(self.toolset, "_merge_cli_approved_prefixes"):
            self.toolset._merge_cli_approved_prefixes()

        # Get config (default if not set)
        config = self.toolset.config or BashExecutorConfig()

        # Build the effective allow/deny lists (copies, not references to shared config)
        allow_list, deny_list = get_effective_lists(config)

        # Merge session-approved prefixes from conversation history (server flow)
        # This modifies our local copy, not the shared config
        if context.session_approved_prefixes:
            existing = set(allow_list)
            for prefix in context.session_approved_prefixes:
                if prefix not in existing:
                    allow_list.append(prefix)
            logging.debug(
                f"Merged {len(context.session_approved_prefixes)} session-approved prefixes"
            )

        # Validate command unless user has already approved
        if not context.user_approved:
            validation_result = validate_command(
                command_str, suggested_prefixes, allow_list, deny_list
            )

            if validation_result.status == ValidationStatus.DENIED:
                sentry_sdk.capture_event(
                    {
                        "message": f"Bash command denied: {validation_result.deny_reason}",
                        "level": "warning",
                        "extra": {
                            "command": command_str,
                            "suggested_prefixes": suggested_prefixes,
                            "deny_reason": validation_result.deny_reason.value
                            if validation_result.deny_reason
                            else None,
                            "message": validation_result.message,
                        },
                    }
                )
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=self._build_deny_error_message(validation_result),
                    params=params,
                    invocation=command_str,
                )

            if validation_result.status == ValidationStatus.APPROVAL_REQUIRED:
                logging.info(f"Bash command requires approval: {command_str}")
                return StructuredToolResult(
                    status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                    error="Command not in allow list.",
                    params={
                        "command": command_str,
                        "suggested_prefixes": validation_result.prefixes_needing_approval
                        or suggested_prefixes,
                    },
                    invocation=command_str,
                )

        # Execute command (user_approved or validation passed)
        logging.info(f"Executing bash command: {command_str}")
        try:
            result = execute_bash_command(cmd=command_str, timeout=timeout)
        except FileNotFoundError:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Error: Bash executable not found. Ensure /bin/bash is available.",
                params=params,
                invocation=command_str,
            )
        return bash_result_to_structured(result, command_str, timeout, params)

    def _build_deny_error_message(self, validation_result) -> str:
        """Build an appropriate error message based on the deny reason."""
        if validation_result.deny_reason == DenyReason.HARDCODED_BLOCK:
            return f"Command blocked: {validation_result.message}"

        elif validation_result.deny_reason == DenyReason.DENY_LIST:
            return f"Command blocked by configuration: {validation_result.message}"

        elif validation_result.deny_reason == DenyReason.SUBSHELL_DETECTED:
            return f"Security error: {validation_result.message}"

        elif validation_result.deny_reason == DenyReason.PARSE_ERROR:
            return f"Parse error: {validation_result.message}"

        elif validation_result.deny_reason == DenyReason.PREFIX_MISMATCH:
            return f"Invalid prefixes: {validation_result.message}"

        elif validation_result.deny_reason == DenyReason.PREFIX_COUNT_MISMATCH:
            return f"Invalid prefixes: {validation_result.message}"

        else:
            return validation_result.message or "Command denied."

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        command = params.get("command", "N/A")
        display_command = command[:200] + "..." if len(command) > 200 else command
        return display_command


class BashExecutorToolset(BaseBashExecutorToolset):
    """
    Toolset for executing bash commands with prefix-based validation.

    Commands are validated against allow/deny lists. Users can approve
    commands on-the-fly and build their trusted command set over time.
    """

    def __init__(self):
        super().__init__(
            name="bash",
            enabled=True,
            description="Execute bash commands validated against prefix-based allow/deny lists, with user approval for unknown commands.",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/bash/",
            icon_url="https://upload.wikimedia.org/wikipedia/commons/d/da/GNOME_Terminal_icon_2019.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[RunBashCommand(self), KubectlRunImageCommand(self)],
            tags=[ToolsetTag.CORE],
            is_default=True,
        )

        self._reload_llm_instructions()

    def _reload_llm_instructions(self):
        """Reload LLM instructions with effective allow/deny lists."""
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "bash_instructions.jinja2")
        )

        # Compute effective lists (includes defaults if include_default_allow_deny_list is True)
        config = self.config or BashExecutorConfig()
        effective_allow, effective_deny = get_effective_lists(config)

        # Create a config-like dict with effective lists for the template
        effective_config = {
            "allow": effective_allow,
            "deny": effective_deny,
        }

        tool_names = [t.name for t in self.tools]
        self.llm_instructions = load_and_render_prompt(
            prompt=f"file://{template_file_path}",
            context={"tool_names": tool_names, "config": effective_config},
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> tuple[bool, str]:
        if config:
            self.config = BashExecutorConfig(**config)
        else:
            self.config = BashExecutorConfig()

        # Load CLI-approved prefixes and merge with allow list
        self._merge_cli_approved_prefixes()

        # Reload instructions to include allow list
        self._reload_llm_instructions()

        return True, ""

    def _merge_cli_approved_prefixes(self) -> None:
        """Merge CLI-approved prefixes from ~/.holmes/bash_approved_prefixes.yaml."""
        try:
            from holmes.interactive import get_cli_approved_prefixes

            cli_prefixes = get_cli_approved_prefixes()
            if cli_prefixes and self.config:
                # Merge without duplicates
                existing = set(self.config.allow)
                for prefix in cli_prefixes:
                    if prefix not in existing:
                        self.config.allow.append(prefix)
                logging.debug(f"Merged {len(cli_prefixes)} CLI-approved prefixes")
        except ImportError:
            # interactive module may not be available in all contexts
            pass
        except Exception as e:
            logging.warning(f"Failed to load CLI-approved prefixes: {e}")

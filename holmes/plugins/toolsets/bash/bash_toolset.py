"""
Bash toolset with prefix-based command validation.

This toolset enables bash command execution with dynamic whitelisting.
Commands are validated against allow/deny lists using prefix matching.
"""

import logging
import os
from typing import Any, Dict, Optional

from holmes.core.tools import (
    ApprovalRequirement,
    CallablePrerequisite,
    ClassVar,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
    Type,
)
from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.toolsets.bash.common.bash import BashResult, execute_bash_command
from holmes.plugins.toolsets.bash.common.cli_prefixes import (
    load_cli_bash_tools_approved_prefixes,
)
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.validation import (
    DenyReason,
    ValidationStatus,
    get_effective_lists,
    validate_command,
)


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


class RunBashCommand(Tool):
    """
    Tool for executing bash commands with prefix-based validation.

    Commands are validated against allow/deny lists using the suggested_prefixes
    parameter. Each command segment (separated by |, &&, etc.) requires its own prefix.
    """

    toolset: "BashExecutorToolset"

    def __init__(self, toolset: "BashExecutorToolset"):
        super().__init__(
            name="bash",
            description=(
                "Executes a bash command and returns its output. "
                "Supports: single commands, pipes (|), &&, ||, ;, &. "
                "Also supports (requires user approval): for/while/until loops, if/case statements, "
                "subshells $() and backticks. "
                "You must provide suggested_prefixes - one prefix per command segment. "
                "Example: for 'kubectl get pods | grep error', provide "
                "suggested_prefixes=['kubectl get', 'grep']. "
                "For scripts with loops/conditionals, provide prefixes for the key operations inside."
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
            toolset=toolset,  # type: ignore[call-arg]
        )

    def _validate_command(
        self, command_str: str, suggested_prefixes: list, context: ToolInvokeContext
    ):
        """Validate command against effective allow/deny lists."""
        # Refresh CLI-approved prefixes (no-op in server mode due to CLI mode flag)
        self.toolset._merge_cli_approved_prefixes()

        config = self.toolset.config or BashExecutorConfig()
        allow_list, deny_list = get_effective_lists(config)

        # Merge session-approved prefixes from conversation history (server flow)
        if context.session_approved_prefixes:
            existing = set(allow_list)
            for prefix in context.session_approved_prefixes:
                if prefix not in existing:
                    allow_list.append(prefix)

        return validate_command(command_str, suggested_prefixes, allow_list, deny_list)

    def requires_approval(
        self, params: Dict[str, Any], context: ToolInvokeContext
    ) -> Optional[ApprovalRequirement]:
        """
        Check if bash command requires approval based on prefix-based validation.

        This method is called BEFORE _invoke() to determine if user approval is needed.
        It can be called multiple times (e.g., to re-check after a previous approval
        updated the allow list).
        """
        command_str = params.get("command", "")
        suggested_prefixes = params.get("suggested_prefixes", [])

        if not command_str or not suggested_prefixes:
            return None  # Let _invoke() handle validation errors

        validation_result = self._validate_command(
            command_str, suggested_prefixes, context
        )

        if validation_result.status == ValidationStatus.DENIED:
            # Denied commands don't need approval - they'll be rejected in _invoke()
            return None

        if validation_result.status == ValidationStatus.APPROVAL_REQUIRED:
            logging.info(f"Bash command requires approval: {command_str}")
            prefixes_to_save = validation_result.prefixes_needing_approval
            return ApprovalRequirement(
                needs_approval=True,
                reason=f"Command requires approval. {validation_result.message}",
                prefixes_to_save=prefixes_to_save,
            )

        # ALLOWED - no approval needed
        return None

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

        # If not user_approved, validate the command
        if not context.user_approved:
            validation_result = self._validate_command(
                command_str, suggested_prefixes, context
            )

            if validation_result.status == ValidationStatus.DENIED:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=self._build_deny_error_message(validation_result),
                    params=params,
                    invocation=command_str,
                )

            if validation_result.status == ValidationStatus.APPROVAL_REQUIRED:
                # This shouldn't happen - requires_approval() should have been called first
                logging.warning(
                    f"Unexpected APPROVAL_REQUIRED in _invoke() for command: {command_str}. "
                    "This indicates requires_approval() was bypassed."
                )
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Command requires approval but was not approved. This may be a bug.",
                    params=params,
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

        elif validation_result.deny_reason == DenyReason.PREFIX_NOT_IN_COMMAND:
            return f"Invalid prefix: {validation_result.message}"

        else:
            return validation_result.message or "Command denied."

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        command = params.get("command", "N/A")
        display_command = command[:200] + "..." if len(command) > 200 else command
        return display_command


class BashExecutorToolset(Toolset):
    """
    Toolset for executing bash commands with prefix-based validation.

    Commands are validated against allow/deny lists. Users can approve
    commands on-the-fly and build their trusted command set over time.
    """

    config_classes: ClassVar[list[Type[BashExecutorConfig]]] = [BashExecutorConfig]
    config: Optional[BashExecutorConfig] = None

    def __init__(self):
        super().__init__(
            name="bash",
            enabled=True,
            description="Execute bash commands validated against prefix-based allow/deny lists, with user approval for unknown commands.",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/bash/",
            icon_url="https://raw.githubusercontent.com/Templarian/MaterialDesign/master/svg/console.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[RunBashCommand(self)],
            tags=[ToolsetTag.CORE],
            is_default=True,
        )

        self._reload_llm_instructions()

    def _reload_llm_instructions(self):
        """Reload LLM instructions with effective allow/deny lists."""
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "bash_instructions.jinja2")
        )

        config = self.config or BashExecutorConfig()
        logging.debug(
            f"Reloading bash toolset with builtin_allowlist: {config.builtin_allowlist}"
        )
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
        self.config = BashExecutorConfig(**config)

        # Load CLI-approved prefixes and merge with allow list
        self._merge_cli_approved_prefixes()

        # Reload instructions to include allow list
        self._reload_llm_instructions()

        return True, ""

    def _merge_cli_approved_prefixes(self) -> None:
        """Merge CLI-approved prefixes from ~/.holmes/bash_approved_prefixes.yaml."""
        cli_prefixes = load_cli_bash_tools_approved_prefixes()
        if cli_prefixes and self.config:
            # Build new list instead of mutating (preserves order, dedupes)
            merged = list(dict.fromkeys(self.config.allow + cli_prefixes))
            self.config.allow = merged
            logging.debug(f"Merged {len(cli_prefixes)} CLI-approved prefixes")

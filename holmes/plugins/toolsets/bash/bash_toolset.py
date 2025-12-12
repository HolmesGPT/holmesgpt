import argparse
import logging
import os
import random
import re
import string
from typing import Dict, Any, Optional

import sentry_sdk


from holmes.common.env_vars import (
    BASH_TOOL_UNSAFE_ALLOW_ALL,
)
from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    StructuredToolResultStatus,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.bash.common.bash import execute_bash_command
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.kubectl.constants import SAFE_NAMESPACE_PATTERN
from holmes.plugins.toolsets.bash.kubectl.kubectl_run import validate_image_and_commands
from holmes.plugins.toolsets.bash.parse_command import make_command_safe
from holmes.plugins.toolsets.utils import get_param_or_raise


class BaseBashExecutorToolset(Toolset):
    config: Optional[BashExecutorConfig] = None

    def get_example_config(self):
        example_config = BashExecutorConfig()
        return example_config.model_dump()


class BaseBashTool(Tool):
    toolset: BaseBashExecutorToolset


class KubectlRunImageCommand(BaseBashTool):
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
        return execute_bash_command(
            cmd=full_kubectl_command, timeout=timeout, params=params
        )

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        return self._build_kubectl_command(params, "<pod_name>")


class RunBashCommand(BaseBashTool):
    def __init__(self, toolset: BaseBashExecutorToolset):
        super().__init__(
            name="run_bash_command",
            description=(
                "Executes a given bash command and returns its standard output, "
                "standard error, and exit code."
                "The command is executed via 'bash -c \"<command>\"'."
                "Only some commands are allowed."
            ),
            parameters={
                "command": ToolParameter(
                    description="The bash command string to execute.",
                    type="string",
                    required=True,
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

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        command_str = params.get("command")
        timeout = params.get("timeout", 60)

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

        command_to_execute = command_str

        # Only run the safety check if user has NOT approved the command
        if not context.user_approved:
            try:
                command_to_execute = make_command_safe(command_str, self.toolset.config)

            except (argparse.ArgumentError, ValueError) as e:
                with sentry_sdk.configure_scope() as scope:
                    scope.set_extra("command", command_str)
                    scope.set_extra("error", str(e))
                    scope.set_extra("unsafe_allow_all", BASH_TOOL_UNSAFE_ALLOW_ALL)
                    sentry_sdk.capture_exception(e)

                if not BASH_TOOL_UNSAFE_ALLOW_ALL:
                    logging.info(f"Refusing LLM tool call {command_str}")

                    return StructuredToolResult(
                        status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                        error=f"Refusing to execute bash command. {str(e)}",
                        params=params,
                        invocation=command_str,
                    )

        return execute_bash_command(
            cmd=command_to_execute, timeout=timeout, params=params
        )

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        command = params.get("command", "N/A")
        display_command = command[:200] + "..." if len(command) > 200 else command
        return display_command


class BashExecutorToolset(BaseBashExecutorToolset):
    def __init__(self):
        super().__init__(
            name="bash",
            enabled=False,
            description=(
                "Toolset for executing arbitrary bash commands on the system where Holmes is running. "
                "WARNING: This toolset provides powerful capabilities and should be "
                "enabled and used with extreme caution due to significant security risks. "
                "Ensure that only trusted users have access to this tool."
            ),
            docs_url="",  # TODO: Add relevant documentation URL
            icon_url="https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Bash_Logo_Colored.svg/120px-Bash_Logo_Colored.svg.png",  # Example Bash icon
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[RunBashCommand(self), KubectlRunImageCommand(self)],
            tags=[ToolsetTag.CORE],
            is_default=False,
        )

        self._reload_llm_instructions()

    def _reload_llm_instructions(self):
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "bash_instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

    def prerequisites_callable(self, config: dict[str, Any]) -> tuple[bool, str]:
        if config:
            self.config = BashExecutorConfig(**config)
        else:
            self.config = BashExecutorConfig()
        return True, ""

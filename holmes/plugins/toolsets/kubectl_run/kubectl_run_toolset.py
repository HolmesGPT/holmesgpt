"""
Kubectl Run toolset for running container images via kubectl run.

This toolset is disabled by default and must be explicitly enabled.
It provides the ability to run temporary pods for debugging purposes.
"""

from __future__ import annotations

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
)
from holmes.plugins.toolsets.bash.bash_toolset import bash_result_to_structured
from holmes.plugins.toolsets.bash.common.bash import execute_bash_command
from holmes.plugins.toolsets.kubectl_run.config import KubectlRunConfig
from holmes.plugins.toolsets.kubectl_run.validation import validate_image_and_commands
from holmes.plugins.toolsets.utils import get_param_or_raise

# Regex pattern for safe Kubernetes namespace names
SAFE_NAMESPACE_PATTERN = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"


class KubectlRunImageCommand(Tool):
    """Tool for running a container image via kubectl run."""

    toolset: "KubectlRunToolset"

    def __init__(self, toolset: "KubectlRunToolset"):
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
            toolset=toolset,  # type: ignore
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


class KubectlRunToolset(Toolset):
    """
    Toolset for running container images via kubectl run.

    This toolset is disabled by default and must be explicitly enabled.
    It provides the ability to run temporary pods for debugging purposes.
    """

    config: Optional[KubectlRunConfig] = None

    def __init__(self):
        super().__init__(
            name="kubectl-run",
            enabled=False,  # Disabled by default
            description="Run temporary debug pods using kubectl run. Must be explicitly enabled.",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/kubectl-run/",
            icon_url="https://raw.githubusercontent.com/kubernetes/kubernetes/master/logo/logo.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[KubectlRunImageCommand(self)],
            llm_instructions="""The tool `kubectl_run_image` will run an image:
- `kubectl run <name> --image=<image> --rm --attach --restart=Never --i --tty -- <command>`""",
            # Not a default toolset - must be explicitly enabled
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> tuple[bool, str]:
        self.config = KubectlRunConfig(**config) if config else KubectlRunConfig()
        return True, ""

    def get_example_config(self):
        example_config = KubectlRunConfig()
        return example_config.model_dump()

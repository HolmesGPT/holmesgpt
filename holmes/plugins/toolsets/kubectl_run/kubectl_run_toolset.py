"""
Kubectl Run toolset for running container images via kubectl run.

This toolset is disabled by default and must be explicitly enabled.
It provides the ability to run temporary pods for debugging purposes.
"""

from __future__ import annotations

import random
import re
import shlex
import string
from typing import Any, Dict, List, Optional

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
from holmes.plugins.toolsets.bash.common.bash import execute_argv_command
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
                "Runs a command in a temporary, auto-deleted pod via "
                "`kubectl run <name> --image=<image> --rm --attach --restart=Never -i -- <command>` "
                "and returns the output. The image AND command must match an allow-list configured "
                "by the operator - if no allow-list is configured, or the image/command is not "
                "whitelisted, the call is rejected. The pod is ephemeral (created and deleted for "
                "this single call) and runs in the 'default' namespace unless one is specified."
            ),
            parameters={
                "image": ToolParameter(
                    description="The image to run (must be allow-listed by the operator)",
                    type="string",
                    required=True,
                ),
                "command": ToolParameter(
                    description="The command to execute on the deployed pod (must match the operator's allow-list)",
                    type="string",
                    required=True,
                ),
                "namespace": ToolParameter(
                    description="The namespace in which to deploy the temporary pod. Defaults to 'default'.",
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

    def _build_kubectl_argv(self, params: dict, pod_name: str) -> List[str]:
        """
        Build the kubectl invocation as an argv list (for shell=False execution).

        The container command is split with shlex into discrete arguments and placed
        after ``--``, so it is delivered to the pod as an argv vector and is never
        interpreted by a host shell. Raises ValueError if the command cannot be
        parsed (e.g. unbalanced quotes).
        """
        namespace = params.get("namespace") or "default"
        image = get_param_or_raise(params, "image")
        command_str = get_param_or_raise(params, "command")
        return [
            "kubectl",
            "run",
            pod_name,
            f"--image={image}",
            f"--namespace={namespace}",
            "--rm",
            "--attach",
            "--restart=Never",
            "-i",
            "--",
            *shlex.split(command_str),
        ]

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
        try:
            argv = self._build_kubectl_argv(params, pod_name)
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Error: could not parse command '{command_str}': {e}",
                params=params,
            )

        display_command = shlex.join(argv)
        try:
            # shell=False: the container command is passed as an argv vector and is
            # never interpreted by a host shell.
            result = execute_argv_command(argv=argv, timeout=timeout)
        except FileNotFoundError:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Error: kubectl executable not found. Ensure kubectl is installed and on PATH.",
                params=params,
                invocation=display_command,
            )
        return bash_result_to_structured(result, display_command, timeout, params)

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        try:
            return shlex.join(self._build_kubectl_argv(params, "<pod_name>"))
        except ValueError:
            # Fall back to a readable rendering if the command can't be parsed.
            image = params.get("image", "")
            namespace = params.get("namespace") or "default"
            command_str = params.get("command", "")
            return (
                f"kubectl run <pod_name> --image={image} --namespace={namespace} "
                f"--rm --attach --restart=Never -i -- {command_str}"
            )


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
            icon_url="https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/kubernetes.svg",
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

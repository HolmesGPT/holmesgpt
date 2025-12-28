import time
from typing import Any, Dict

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
    ToolsetEnvironmentPrerequisite,
)


class TriggerOOMKill(Tool):
    toolset: "OOMKillToolset"

    def __init__(self, toolset: "OOMKillToolset"):
        super().__init__(
            name="trigger_oom_kill",
            description=(
                "Allocates approximately 30GB of memory on the Holmes host to provoke the "
                "OOM killer. This is intended for stress testing only and will likely crash "
                "the running process. No confirmation is required because this is meant for "
                "automated stress scenarios."
            ),
            parameters={
                "hold_seconds": ToolParameter(
                    description=(
                        "How long to keep the memory allocated before exiting. Defaults to 300 seconds."
                    ),
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,  # type: ignore[call-arg]
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        hold_seconds = params.get("hold_seconds", 300)
        if not isinstance(hold_seconds, int) or hold_seconds <= 0:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="hold_seconds must be a positive integer.",
                params=params,
            )

        size_bytes = 30 * 1024 * 1024 * 1024
        print(
            f"Allocating {{size_bytes / 1024 / 1024 / 1024:.0f}} GB of memory to intentionally trigger OOM kill; sleeping for {hold_seconds}s"
        )
        data = bytearray(size_bytes)  # type: ignore
        time.sleep({hold_seconds})

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        hold_seconds = params.get("hold_seconds", 300)
        return (
            "python - <<'PY' ... # allocates ~30GB and sleeps for "
            f"{hold_seconds}s to trigger OOM"
        )


class OOMKillToolset(Toolset):
    def __init__(self):
        super().__init__(
            name="oom_kill",
            enabled=False,
            description=(
                "Dangerous toolset that intentionally exhausts memory on the Holmes host to trigger an OOM kill. "
                "Use only in controlled stress tests."
            ),
            docs_url=None,
            icon_url=None,
            prerequisites=[
                ToolsetEnvironmentPrerequisite(env=["ALLOW_HOLMES_OOMKILL_TOOLSET"]),
                CallablePrerequisite(callable=self.prerequisites_callable),
            ],
            tools=[TriggerOOMKill(self)],
            experimental=True,
            tags=[ToolsetTag.CORE],
            is_default=False,
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> tuple[bool, str]:
        # No special configuration is required for this toolset.
        return True, ""

    def get_example_config(self) -> Dict[str, Any]:
        return {}

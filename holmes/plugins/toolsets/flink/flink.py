import os
from typing import Any, ClassVar, Dict, List, Tuple, Type, cast

from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.http_api_base import (
    HTTPAPITool,
    HTTPAPIToolset,
    HTTPAPIToolsetConfig,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class FlinkConfig(HTTPAPIToolsetConfig):
    max_items: int = Field(default=100, ge=1, le=1000)


class FlinkToolset(HTTPAPIToolset):
    config_classes: ClassVar[List[Type[FlinkConfig]]] = [FlinkConfig]

    def __init__(self) -> None:
        super().__init__(
            name="flink",
            description="Inspect Apache Flink jobs, failures, and checkpoints",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/flink/",
            icon_url="https://flink.apache.org/img/logo/png/500/flink_squirrel_500.png",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
        )
        self.tools = [
            FlinkListJobs(self),
            FlinkGetJob(self),
            FlinkGetExceptions(self),
            FlinkGetCheckpoints(self),
        ]
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    @property
    def flink_config(self) -> FlinkConfig:
        return cast(FlinkConfig, self.config)

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        return self._health_check(FlinkConfig, config, "/overview")


class _FlinkTool(HTTPAPITool):
    def _bounded(
        self, result: StructuredToolResult, collection_key: str
    ) -> StructuredToolResult:
        if result.status != StructuredToolResultStatus.SUCCESS or not isinstance(
            result.data, dict
        ):
            return result
        values = result.data.get(collection_key)
        if isinstance(values, list):
            max_items = cast(FlinkToolset, self._toolset).flink_config.max_items
            result.data[collection_key] = values[:max_items]
            result.data["holmes_truncated"] = len(values) > max_items
        return result


class FlinkListJobs(_FlinkTool):
    def __init__(self, toolset: FlinkToolset):
        super().__init__(
            toolset,
            name="flink_list_jobs",
            description=(
                "List running and recently completed Flink jobs with their states."
            ),
            parameters={},
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return self._bounded(self._get_json("/jobs/overview", params, context), "jobs")

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: jobs"


class _FlinkJobTool(_FlinkTool):
    endpoint_suffix: ClassVar[str] = ""

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        job_id = str(params.get("job_id", "")).strip()
        if not job_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing required parameter 'job_id'",
                params=params,
            )
        return self._get_json(f"/jobs/{job_id}{self.endpoint_suffix}", params, context)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: "
            f"{self.name} {params.get('job_id', '')}"
        )


def _job_id_parameter() -> Dict[str, ToolParameter]:
    return {
        "job_id": ToolParameter(
            type="string", required=True, description="32-character Flink job ID"
        )
    }


class FlinkGetJob(_FlinkJobTool):
    def __init__(self, toolset: FlinkToolset):
        super().__init__(
            toolset,
            name="flink_get_job",
            description="Get execution details and vertices for a Flink job.",
            parameters=_job_id_parameter(),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return self._bounded(super()._invoke(params, context), "vertices")


class FlinkGetExceptions(_FlinkJobTool):
    endpoint_suffix: ClassVar[str] = "/exceptions"

    def __init__(self, toolset: FlinkToolset):
        super().__init__(
            toolset,
            name="flink_get_job_exceptions",
            description="Get the most recent handled exceptions for a Flink job.",
            parameters=_job_id_parameter(),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        result = super()._invoke(params, context)
        if result.status == StructuredToolResultStatus.SUCCESS and isinstance(
            result.data, dict
        ):
            history = result.data.get("exceptionHistory")
            if isinstance(history, dict) and isinstance(history.get("entries"), list):
                max_items = cast(FlinkToolset, self._toolset).flink_config.max_items
                entries = history["entries"]
                history["entries"] = entries[:max_items]
                history["holmes_truncated"] = len(entries) > max_items
        return result


class FlinkGetCheckpoints(_FlinkJobTool):
    endpoint_suffix: ClassVar[str] = "/checkpoints"

    def __init__(self, toolset: FlinkToolset):
        super().__init__(
            toolset,
            name="flink_get_job_checkpoints",
            description=(
                "Get checkpoint counts, latest checkpoints, and checkpoint history."
            ),
            parameters=_job_id_parameter(),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        result = super()._invoke(params, context)
        if result.status == StructuredToolResultStatus.SUCCESS and isinstance(
            result.data, dict
        ):
            history = result.data.get("history")
            if isinstance(history, list):
                max_items = cast(FlinkToolset, self._toolset).flink_config.max_items
                result.data["history"] = history[:max_items]
                result.data["holmes_truncated"] = len(history) > max_items
        return result

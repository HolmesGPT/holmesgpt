import os
from typing import Any, ClassVar, Dict, List, Tuple, Type, cast
from urllib.parse import quote

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


class AirflowConfig(HTTPAPIToolsetConfig):
    api_version: str = Field(
        default="v2",
        pattern=r"^v[12]$",
        description="Airflow REST API version (v2 for Airflow 3, v1 for Airflow 2)",
    )
    max_items: int = Field(default=100, ge=1, le=1000)
    max_log_characters: int = Field(default=20000, ge=1000, le=200000)


class AirflowToolset(HTTPAPIToolset):
    config_classes: ClassVar[List[Type[AirflowConfig]]] = [AirflowConfig]

    def __init__(self) -> None:
        super().__init__(
            name="airflow",
            description=(
                "Inspect Apache Airflow DAG runs, task instances, and task logs"
            ),
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/airflow/",
            icon_url="https://airflow.apache.org/favicons/favicon-32x32.png",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
        )
        self.tools = [
            AirflowListDags(self),
            AirflowListDagRuns(self),
            AirflowListTaskInstances(self),
            AirflowGetTaskLog(self),
        ]
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    @property
    def airflow_config(self) -> AirflowConfig:
        return cast(AirflowConfig, self.config)

    @property
    def api_prefix(self) -> str:
        return f"/api/{self.airflow_config.api_version}"

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.config = AirflowConfig(**config)
        except Exception as error:
            return False, f"Failed to validate airflow configuration: {error}"
        return self._health_check(
            AirflowConfig, config, f"/api/{self.airflow_config.api_version}/version"
        )


class _AirflowTool(HTTPAPITool):
    def _bounded(
        self, result: StructuredToolResult, collection_key: str
    ) -> StructuredToolResult:
        if result.status != StructuredToolResultStatus.SUCCESS or not isinstance(
            result.data, dict
        ):
            return result
        values = result.data.get(collection_key)
        if isinstance(values, list):
            max_items = cast(AirflowToolset, self._toolset).airflow_config.max_items
            result.data[collection_key] = values[:max_items]
            result.data["holmes_truncated"] = len(values) > max_items
        return result

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: {self.name}"


class AirflowListDags(_AirflowTool):
    def __init__(self, toolset: AirflowToolset):
        super().__init__(
            toolset,
            name="airflow_list_dags",
            description=(
                "List Airflow DAGs with bounded pagination and optional tag filtering."
            ),
            parameters={
                "limit": ToolParameter(type="integer", required=False),
                "offset": ToolParameter(type="integer", required=False),
                "tags": ToolParameter(
                    type="array",
                    items=ToolParameter(type="string"),
                    required=False,
                    description="Optional DAG tags",
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        max_items = cast(AirflowToolset, self._toolset).airflow_config.max_items
        query = {
            "limit": max(1, min(int(params.get("limit", max_items)), max_items)),
            "offset": max(int(params.get("offset", 0)), 0),
        }
        if params.get("tags"):
            query["tags"] = params["tags"]
        endpoint = f"{cast(AirflowToolset, self._toolset).api_prefix}/dags"
        return self._bounded(
            self._get_json(endpoint, params, context, query_params=query), "dags"
        )


def _dag_parameter() -> Dict[str, ToolParameter]:
    return {
        "dag_id": ToolParameter(
            type="string", required=True, description="Airflow DAG ID"
        ),
        "limit": ToolParameter(type="integer", required=False),
        "offset": ToolParameter(type="integer", required=False),
    }


class AirflowListDagRuns(_AirflowTool):
    def __init__(self, toolset: AirflowToolset):
        super().__init__(
            toolset,
            name="airflow_list_dag_runs",
            description="List recent runs for one Airflow DAG.",
            parameters=_dag_parameter(),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        dag_id = quote(str(params.get("dag_id", "")), safe="")
        if not dag_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing required parameter 'dag_id'",
                params=params,
            )
        max_items = cast(AirflowToolset, self._toolset).airflow_config.max_items
        query = {
            "limit": max(1, min(int(params.get("limit", max_items)), max_items)),
            "offset": max(int(params.get("offset", 0)), 0),
            "order_by": "-logical_date",
        }
        endpoint = (
            f"{cast(AirflowToolset, self._toolset).api_prefix}/dags/{dag_id}/dagRuns"
        )
        return self._bounded(
            self._get_json(endpoint, params, context, query_params=query), "dag_runs"
        )


class AirflowListTaskInstances(_AirflowTool):
    def __init__(self, toolset: AirflowToolset):
        parameters = _dag_parameter()
        parameters["dag_run_id"] = ToolParameter(
            type="string", required=True, description="Airflow DAG run ID"
        )
        super().__init__(
            toolset,
            name="airflow_list_task_instances",
            description="List task instances and states for one Airflow DAG run.",
            parameters=parameters,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        dag_id = quote(str(params.get("dag_id", "")), safe="")
        run_id = quote(str(params.get("dag_run_id", "")), safe="")
        if not dag_id or not run_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Both 'dag_id' and 'dag_run_id' are required",
                params=params,
            )
        max_items = cast(AirflowToolset, self._toolset).airflow_config.max_items
        query = {
            "limit": max(1, min(int(params.get("limit", max_items)), max_items)),
            "offset": max(int(params.get("offset", 0)), 0),
        }
        endpoint = (
            f"{cast(AirflowToolset, self._toolset).api_prefix}/dags/{dag_id}"
            f"/dagRuns/{run_id}/taskInstances"
        )
        return self._bounded(
            self._get_json(endpoint, params, context, query_params=query),
            "task_instances",
        )


class AirflowGetTaskLog(_AirflowTool):
    def __init__(self, toolset: AirflowToolset):
        super().__init__(
            toolset,
            name="airflow_get_task_log",
            description="Fetch a bounded log for one Airflow task attempt.",
            parameters={
                "dag_id": ToolParameter(type="string", required=True),
                "dag_run_id": ToolParameter(type="string", required=True),
                "task_id": ToolParameter(type="string", required=True),
                "try_number": ToolParameter(type="integer", required=True),
                "map_index": ToolParameter(type="integer", required=False),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        required = ("dag_id", "dag_run_id", "task_id", "try_number")
        if any(params.get(key) is None or params.get(key) == "" for key in required):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Required parameters: {', '.join(required)}",
                params=params,
            )
        dag_id = quote(str(params["dag_id"]), safe="")
        run_id = quote(str(params["dag_run_id"]), safe="")
        task_id = quote(str(params["task_id"]), safe="")
        endpoint = (
            f"{cast(AirflowToolset, self._toolset).api_prefix}/dags/{dag_id}"
            f"/dagRuns/{run_id}/taskInstances/{task_id}/logs/"
            f"{int(params['try_number'])}"
        )
        result = self._get_json(
            endpoint,
            params,
            context,
            query_params={
                "full_content": "false",
                "map_index": int(params.get("map_index", -1)),
            },
        )
        if result.status == StructuredToolResultStatus.SUCCESS:
            max_chars = cast(
                AirflowToolset, self._toolset
            ).airflow_config.max_log_characters
            text = result.get_stringified_data()
            if len(text) > max_chars:
                result.data = {
                    "content": text[:max_chars],
                    "holmes_truncated": True,
                    "original_characters": len(text),
                }
        return result

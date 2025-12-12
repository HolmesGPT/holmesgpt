import requests  # type: ignore
import logging
from typing import Any, Dict, Tuple, List
from holmes.core.tools import (
    CallablePrerequisite,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)

from pydantic import BaseModel, PrivateAttr
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from requests.auth import HTTPDigestAuth  # type: ignore
import gzip
import io
from datetime import datetime, timedelta, timezone
import os
from collections import Counter

from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class MongoDBConfig(BaseModel):
    public_key: str
    private_key: str
    project_id: str


# https://www.mongodb.com/docs/atlas/reference/api-resources-spec/v2/
class MongoDBAtlasToolset(Toolset):
    name: str = "MongoDBAtlas"
    description: str = "The MongoDB Atlas API allows access to Mongodb projects and processes. You can find logs, alerts, events, slow queries and various metrics to understand the state of Mongodb projects."
    docs_url: str = (
        "https://www.mongodb.com/docs/atlas/reference/api-resources-spec/v2/"
    )
    icon_url: str = "https://webimages.mongodb.com/_com_assets/cms/kuyjf3vea2hg34taa-horizontal_default_slate_blue.svg?auto=format%252Ccompress"
    tags: List[ToolsetTag] = [ToolsetTag.CORE]
    _session: requests.Session = PrivateAttr(default=requests.Session())

    def __init__(self):
        super().__init__(
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                ReturnProjectAlerts(toolset=self),
                ReturnProjectProcesses(toolset=self),
                ReturnProjectSlowQueries(toolset=self),
                ReturnEventsFromProject(toolset=self),
                ReturnLogsForProcessInProject(toolset=self),
                ReturnEventTypeFromProject(toolset=self),
            ],
        )
        instructions_filepath = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{instructions_filepath}")

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, "Missing config credentials."

        try:
            self.config: Dict = MongoDBConfig(**config).model_dump()
            self._session.headers.update(
                {"Accept": "application/vnd.atlas.2025-03-12+json"}
            )
            self._session.auth = HTTPDigestAuth(
                self.config.get("public_key"),  # type: ignore
                self.config.get("private_key"),  # type: ignore
            )
            return True, ""
        except Exception:
            logging.exception(
                "Invalid Atlas config. Failed to set up MongoDBAtlas toolset"
            )
            return False, "Invalid Atlas config"

    def get_example_config(self) -> Dict[str, Any]:
        return {}


class MongoDBAtlasBaseTool(Tool):
    toolset: MongoDBAtlasToolset

    def return_result(
        self, response: requests.Response, params: Any, field: str = "results"
    ) -> StructuredToolResult:
        response.raise_for_status()
        if response.ok:
            res = response.json()
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS
                if res.get(field, [])
                else StructuredToolResultStatus.NO_DATA,
                data=res,
                params=params,
            )
        else:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed {self.name}.\n{response.text}",
                return_code=response.status_code,
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        # Default implementation - will be overridden by subclasses
        return f"MongoDB {self.name} project {self.toolset.config.get('project_id')} {params}"


# https://www.mongodb.com/docs/atlas/reference/api-resources-spec/v2/#tag/Alerts/operation/listAlertsByAlertConfigurationId
class ReturnProjectAlerts(MongoDBAtlasBaseTool):
    name: str = "atlas_return_project_alerts"
    description: str = "Returns all project alerts. These alerts apply to all components in one project. You receive an alert when a monitored component meets or exceeds a value you set."

    def get_parameterized_one_liner(self, params) -> str:
        project_id = self.toolset.config.get("project_id", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Project Alerts ({project_id})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            url = "https://cloud.mongodb.com/api/atlas/v2/groups/{project_id}/alerts".format(
                project_id=self.toolset.config.get("project_id")
            )
            response = self.toolset._session.get(url=url)
            return self.return_result(response, params)
        except Exception as e:
            logging.exception(self.get_parameterized_one_liner(params))
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data=f"Exception {self.name}: {str(e)}",
                params=params,
            )


# https://www.mongodb.com/docs/atlas/reference/api-resources-spec/v2/#tag/Monitoring-and-Logs/operation/listAtlasProcesses
class ReturnProjectProcesses(MongoDBAtlasBaseTool):
    name: str = "atlas_return_project_processes"
    description: str = "Returns details of all processes for the specified project. Useful for getting logs and data for specific project"

    def get_parameterized_one_liner(self, params) -> str:
        project_id = self.toolset.config.get("project_id", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Project Processes ({project_id})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            url = "https://cloud.mongodb.com/api/atlas/v2/groups/{project_id}/processes".format(
                project_id=self.toolset.config.get("project_id")
            )
            response = self.toolset._session.get(url)
            return self.return_result(response, params)
        except Exception as e:
            logging.exception(self.get_parameterized_one_liner(params))
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Exception {self.name}: {str(e)}",
                params=params,
            )


# https://www.mongodb.com/docs/atlas/reference/api-resources-spec/v2/#tag/Performance-Advisor/operation/listSlowQueries
class ReturnProjectSlowQueries(MongoDBAtlasBaseTool):
    name: str = "atlas_return_project_processes_slow_queries"
    description: str = "Returns log lines for slow queries that the Performance Advisor and Query Profiler identified for a specific process in a specific project. requires fetching the project processes first. returns queries from the last 24 hours."
    url: str = "https://cloud.mongodb.com/api/atlas/v2/groups/{project_id}/processes/{process_id}/performanceAdvisor/slowQueryLogs?includeMetrics=true"
    parameters: Dict[str, ToolParameter] = {
        "process_id": ToolParameter(
            description="Combination of host and port that serves the MongoDB process. call tool atlas_return_project_processes tool to get host+port of project procecess.",
            type="string",
            required=True,
        ),
    }

    def get_parameterized_one_liner(self, params) -> str:
        process_id = params.get("process_id", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Slow Queries ({process_id})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            url = self.url.format(
                project_id=self.toolset.config.get("project_id"),
                process_id=params.pop("process_id", ""),
            )
            response = self.toolset._session.get(url)
            return self.return_result(response, params, "slowQueries")
        except Exception as e:
            logging.exception(self.get_parameterized_one_liner(params))
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Exception {self.name}: {str(e)}",
                params=params,
            )


# https://www.mongodb.com/docs/atlas/reference/api-resources-spec/v2/#tag/Events/operation/listProjectEvents
class ReturnEventsFromProject(MongoDBAtlasBaseTool):
    name: str = "atlas_return_events_from_project"
    description: str = "Returns all events occurrences for the specified project. Events identify significant database, security activities or status changes. can only query the last 4 hours."
    url: str = "https://cloud.mongodb.com/api/atlas/v2/groups/{projectId}/events"

    def get_parameterized_one_liner(self, params) -> str:
        project_id = self.toolset.config.get("project_id", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Project Events ({project_id})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        params.update({"itemsPerPage": 500})
        try:
            now_utc = datetime.now(timezone.utc)
            four_hours_ago = now_utc - timedelta(hours=4)
            iso_timestamp = four_hours_ago.isoformat()
            url = self.url.format(projectId=self.toolset.config.get("project_id"))
            response = self.toolset._session.get(
                url=url,
                params={"minDate": iso_timestamp},
            )
            response.raise_for_status()
            if response.ok:
                res = response.json()
                events_counter = Counter(
                    [event.get("eventTypeName") for event in res.get("results", [])]
                )
                data = f"last 4 hours eventTypeName and # of occurrences list: {events_counter} \n to get more information about a given eventTypeName call atlas_return_events_type_from_project"
                status = (
                    StructuredToolResultStatus.SUCCESS
                    if events_counter
                    else StructuredToolResultStatus.NO_DATA
                )
                return StructuredToolResult(status=status, data=data, params=params)
            else:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Failed {self.name}. \n{response.text}",
                    return_code=response.status_code,
                    params=params,
                )
        except Exception as e:
            logging.exception(self.get_parameterized_one_liner(params))
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Exception {self.name}: {str(e)}",
                params=params,
            )


# https://www.mongodb.com/docs/atlas/reference/api-resources-spec/v2/#tag/Monitoring-and-Logs/operation/getHostLogs
class ReturnLogsForProcessInProject(MongoDBAtlasBaseTool):
    name: str = "atlas_return_logs_for_host_in_project"
    description: str = "Returns log messages for the specified host for the specified project of the last 1 hour."
    url: str = "https://cloud.mongodb.com/api/atlas/v2/groups/{project_id}/clusters/{process_id}/logs/mongodb.gz"
    parameters: Dict[str, ToolParameter] = {
        "hostName": ToolParameter(
            description="The host must be the hostname, FQDN, IPv4 address, or IPv6 address of the host that runs the MongoDB process (mongod or mongos).",
            type="string",
            required=True,
        ),
    }

    def get_parameterized_one_liner(self, params) -> str:
        hostname = params.get("hostName", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Host Logs ({hostname})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        try:
            url = self.url.format(
                project_id=self.toolset.config.get("project_id"),
                process_id=params.get("hostName", ""),
            )
            response = self.toolset._session.get(
                url=url,
                headers={"Accept": "application/vnd.atlas.2025-03-12+gzip"},
                params={"startDate": int(one_hour_ago.timestamp())},
            )
            response.raise_for_status()
            if response.ok:
                with gzip.GzipFile(fileobj=io.BytesIO(response.content)) as gz:
                    text_data = gz.read().decode("utf-8")
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=text_data,
                    params=params,
                )
            else:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Failed {self.name}. \n{response.text}",
                    return_code=response.status_code,
                    params=params,
                )
        except Exception as e:
            logging.exception(self.get_parameterized_one_liner(params))
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Exception {self.name}: {str(e)}",
                params=params,
            )


# https://www.mongodb.com/docs/atlas/reference/api-resources-spec/v2/#tag/Events/operation/listProjectEvents
class ReturnEventTypeFromProject(MongoDBAtlasBaseTool):
    name: str = "atlas_return_events_type_from_project"
    description: str = "Returns all events of specific EventType for the specified project. can only query the last 4 hours."
    url: str = "https://cloud.mongodb.com/api/atlas/v2/groups/{projectId}/events"
    parameters: Dict[str, ToolParameter] = {
        "eventType": ToolParameter(
            description="A label of an eventType, all capital letters with snake case. examples:  INSIDE_METRIC_THRESHOLD, PRIMARY_ELECTED and DATA_EXPLORER. NEVER call this before first calling atlas_return_events_from_project to get a list of last 4 hours eventTypes.",
            type="string",
            required=True,
        ),
    }

    def get_parameterized_one_liner(self, params) -> str:
        event_type = params.get("eventType", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Event Details ({event_type})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            url = self.url.format(projectId=self.toolset.config.get("project_id"))

            now_utc = datetime.now(timezone.utc)
            four_hours_ago = now_utc - timedelta(hours=4)
            iso_timestamp = four_hours_ago.isoformat()
            params.update({"itemsPerPage": 500, "minDate": iso_timestamp})
            response = self.toolset._session.get(
                url=url,
                params=params,
            )
            return self.return_result(response, params)
        except Exception as e:
            logging.exception(self.get_parameterized_one_liner(params))
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Exception {self.name}: {str(e)}",
                params=params,
            )

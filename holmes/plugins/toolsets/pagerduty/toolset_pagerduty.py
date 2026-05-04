"""PagerDuty toolset for read-only incident and alert operations."""

import json
import logging
from typing import Any, List, Optional, Tuple, Type

import requests
from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite,
)
from holmes.core.tools import ClassVar as ToolsClassVar
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig

PAGERDUTY_API_BASE = "https://api.pagerduty.com"


class PagerDutyConfig(ToolsetConfig):
    """Configuration for PagerDuty API access."""

    api_key: str = Field(
        title="API Key",
        description="PagerDuty REST API key (v2). Generate one at: Account Settings → API Access Keys",
        examples=["u+xxxxxxxxxxxxxxxxxxxx"],
    )
    api_url: str = Field(
        default=PAGERDUTY_API_BASE,
        title="API URL",
        description="PagerDuty API base URL. Override for on-prem forks or local mocks.",
    )
    default_limit: int = Field(
        default=25,
        title="Default Result Limit",
        description="Maximum number of results to return per query",
    )
    team_ids: Optional[List[str]] = Field(
        default=None,
        title="Team IDs (project scope)",
        description="When set, all list queries are filtered to these PagerDuty team IDs. Leave unset for no filter.",
    )
    service_ids: Optional[List[str]] = Field(
        default=None,
        title="Service IDs (project scope)",
        description="When set, all list queries are filtered to these PagerDuty service IDs. Leave unset for no filter.",
    )


class PagerDutyToolset(Toolset):
    """PagerDuty toolset for querying incidents, services, and on-call schedules."""

    config_classes: ToolsClassVar[list[Type[PagerDutyConfig]]] = [PagerDutyConfig]

    pd_config: Optional[PagerDutyConfig] = None

    def __init__(self):
        super().__init__(
            name="pagerduty",
            description="Read-only access to PagerDuty incidents, services, escalation policies, and on-call schedules",
            docs_url="https://developer.pagerduty.com/api-reference/",
            icon_url="https://www.pagerduty.com/wp-content/uploads/2020/02/pd-logo-green.png",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                ListPagerDutyIncidents(toolset=self),
                GetPagerDutyIncident(toolset=self),
                ListPagerDutyServices(toolset=self),
                ListPagerDutyAlerts(toolset=self),
                GetPagerDutyOnCall(toolset=self),
            ],
            tags=[ToolsetTag.CORE],
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, "Missing PagerDuty configuration. Provide api_key."
        try:
            self.pd_config = PagerDutyConfig(**config)
            return self._health_check()
        except Exception as e:
            return False, f"Failed to configure PagerDuty toolset: {e}"

    def _health_check(self) -> Tuple[bool, str]:
        assert self.pd_config is not None
        try:
            # Use /services with limit=1 as a lightweight health check.
            # The /abilities endpoint was deprecated by PagerDuty and may
            # return 410 Gone or 404 on newer accounts.
            resp = requests.get(
                f"{self.pd_config.api_url}/services",
                headers=self._headers(),
                params={"limit": 1},
                timeout=10,
            )
            if resp.status_code == 200:
                return True, ""
            if resp.status_code == 401:
                return False, "PagerDuty API key is invalid or expired"
            return (
                False,
                f"PagerDuty API returned {resp.status_code}: {resp.text[:200]}",
            )
        except Exception as e:
            return False, f"PagerDuty health check failed: {e}"

    def _headers(self) -> dict:
        assert self.pd_config is not None
        return {
            "Authorization": f"Token token={self.pd_config.api_key}",
            "Accept": "application/vnd.pagerduty+json;version=2",
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        assert self.pd_config is not None
        url = f"{self.pd_config.api_url}{path}"
        resp = requests.get(
            url, headers=self._headers(), params=params or {}, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def _apply_scope_filters(
        self, query: dict, params: dict
    ) -> Tuple[dict, Optional[str]]:
        """
        Apply instance-level team/service scope to a query dict.

        - If the instance has team_ids or service_ids set, those are treated as
          the maximum permitted scope.
        - If the user (LLM) passes the same filter via tool params, the result is
          the intersection of user-supplied values and instance scope.
        - If the user passes IDs outside the instance scope, they are dropped
          (never widens beyond instance scope) and a note is returned so the LLM
          sees why.

        Returns (query_with_filters_appended, optional_note_string).
        """
        assert self.pd_config is not None
        note_parts: list[str] = []

        def _merge(field_name: str, instance_values: Optional[List[str]]) -> None:
            user_raw = params.get(field_name)
            user_values: Optional[List[str]] = None
            if user_raw:
                user_values = [v.strip() for v in user_raw.split(",") if v.strip()]

            if instance_values is not None:
                if user_values is None:
                    final = list(instance_values)
                else:
                    final = [v for v in user_values if v in instance_values]
                    dropped = [v for v in user_values if v not in instance_values]
                    if dropped:
                        note_parts.append(
                            f"Filter narrowed to project scope: "
                            f"{field_name} allowed={instance_values} "
                            f"applied={final} "
                            f"(dropped out-of-scope IDs: {dropped})"
                        )
                query[f"{field_name}[]"] = final
            elif user_values is not None:
                # No instance scope — user filter passes through unchanged.
                query[f"{field_name}[]"] = user_values

        _merge("service_ids", self.pd_config.service_ids)
        _merge("team_ids", self.pd_config.team_ids)

        note = "; ".join(note_parts) if note_parts else None
        return query, note


class BasePagerDutyTool(Tool):
    toolset: "PagerDutyToolset"


class ListPagerDutyIncidents(BasePagerDutyTool):
    def __init__(self, toolset: "PagerDutyToolset"):
        super().__init__(
            name="list_pagerduty_incidents",
            description="[pagerduty toolset] List PagerDuty incidents with optional filters",
            parameters={
                "statuses": ToolParameter(
                    description="Comma-separated statuses to filter by: triggered, acknowledged, resolved. Default: triggered,acknowledged",
                    type="string",
                    required=False,
                ),
                "service_ids": ToolParameter(
                    description="Comma-separated PagerDuty service IDs to filter by",
                    type="string",
                    required=False,
                ),
                "urgency": ToolParameter(
                    description="Filter by urgency: high or low",
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum number of incidents to return (default: 25)",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        statuses = params.get("statuses", "triggered,acknowledged")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List incidents (status={statuses})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.pd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="PagerDuty not configured",
                params=params,
            )
        try:
            statuses_raw = params.get("statuses", "triggered,acknowledged")
            statuses = [s.strip() for s in statuses_raw.split(",") if s.strip()]
            query: dict[str, Any] = {
                "limit": params.get("limit", self.toolset.pd_config.default_limit),
                "sort_by": "created_at:desc",
            }
            query["statuses[]"] = statuses

            if params.get("urgency"):
                query["urgencies[]"] = [params["urgency"]]

            query, scope_note = self.toolset._apply_scope_filters(query, params)

            data = self.toolset.get("/incidents", params=query)
            payload = json.dumps(data, indent=2)
            if scope_note:
                payload = f"[{scope_note}]\n{payload}"
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=payload,
                params=params,
                url="https://app.pagerduty.com/incidents",
            )
        except Exception as e:
            logging.exception("Failed to list PagerDuty incidents")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )


class GetPagerDutyIncident(BasePagerDutyTool):
    def __init__(self, toolset: "PagerDutyToolset"):
        super().__init__(
            name="get_pagerduty_incident",
            description="[pagerduty toolset] Get details of a specific PagerDuty incident by ID",
            parameters={
                "incident_id": ToolParameter(
                    description="The PagerDuty incident ID (e.g., P1234AB)",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get incident {params.get('incident_id', '')}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.pd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="PagerDuty not configured",
                params=params,
            )
        incident_id = params.get("incident_id", "")
        if not incident_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="incident_id is required",
                params=params,
            )
        try:
            data = self.toolset.get(f"/incidents/{incident_id}")
            incident = data.get("incident", data)
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=json.dumps(incident, indent=2),
                params=params,
                url=incident.get("html_url", ""),
            )
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Incident {incident_id} not found",
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )
        except Exception as e:
            logging.exception("Failed to get PagerDuty incident")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )


class ListPagerDutyServices(BasePagerDutyTool):
    def __init__(self, toolset: "PagerDutyToolset"):
        super().__init__(
            name="list_pagerduty_services",
            description="[pagerduty toolset] List PagerDuty services (integrations/applications being monitored)",
            parameters={
                "query": ToolParameter(
                    description="Filter services by name substring",
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum number of services to return (default: 25)",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        q = params.get("query", "all")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List services (query={q})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.pd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="PagerDuty not configured",
                params=params,
            )
        try:
            query: dict[str, Any] = {
                "limit": params.get("limit", self.toolset.pd_config.default_limit)
            }
            if params.get("query"):
                query["query"] = params["query"]

            query, scope_note = self.toolset._apply_scope_filters(query, params)

            data = self.toolset.get("/services", params=query)
            payload = json.dumps(data, indent=2)
            if scope_note:
                payload = f"[{scope_note}]\n{payload}"
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=payload,
                params=params,
                url="https://app.pagerduty.com/services",
            )
        except Exception as e:
            logging.exception("Failed to list PagerDuty services")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )


class ListPagerDutyAlerts(BasePagerDutyTool):
    def __init__(self, toolset: "PagerDutyToolset"):
        super().__init__(
            name="list_pagerduty_alerts",
            description="[pagerduty toolset] List alerts (log entries) for a specific PagerDuty incident",
            parameters={
                "incident_id": ToolParameter(
                    description="The PagerDuty incident ID to list alerts for",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List alerts for incident {params.get('incident_id', '')}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.pd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="PagerDuty not configured",
                params=params,
            )
        incident_id = params.get("incident_id", "")
        if not incident_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="incident_id is required",
                params=params,
            )
        try:
            data = self.toolset.get(f"/incidents/{incident_id}/alerts")
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=json.dumps(data, indent=2),
                params=params,
            )
        except Exception as e:
            logging.exception("Failed to list PagerDuty alerts")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )


class GetPagerDutyOnCall(BasePagerDutyTool):
    def __init__(self, toolset: "PagerDutyToolset"):
        super().__init__(
            name="get_pagerduty_oncall",
            description="[pagerduty toolset] Get who is currently on-call for a given escalation policy or schedule",
            parameters={
                "escalation_policy_ids": ToolParameter(
                    description="Comma-separated escalation policy IDs to filter by",
                    type="string",
                    required=False,
                ),
                "schedule_ids": ToolParameter(
                    description="Comma-separated schedule IDs to filter by",
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get on-call users"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.pd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="PagerDuty not configured",
                params=params,
            )
        try:
            query: dict[str, Any] = {}
            if params.get("escalation_policy_ids"):
                query["escalation_policy_ids[]"] = [
                    p.strip() for p in params["escalation_policy_ids"].split(",")
                ]
            if params.get("schedule_ids"):
                query["schedule_ids[]"] = [
                    s.strip() for s in params["schedule_ids"].split(",")
                ]

            # /oncalls does not support service_ids. Drop user-supplied service_ids
            # from params AND temporarily clear the instance's service_ids so the
            # scope helper skips that dimension.
            filtered_params = {k: v for k, v in params.items() if k != "service_ids"}
            saved_service_ids = self.toolset.pd_config.service_ids
            self.toolset.pd_config.service_ids = None
            try:
                query, scope_note = self.toolset._apply_scope_filters(
                    query, filtered_params
                )
            finally:
                self.toolset.pd_config.service_ids = saved_service_ids

            data = self.toolset.get("/oncalls", params=query)
            payload = json.dumps(data, indent=2)
            if scope_note:
                payload = f"[{scope_note}]\n{payload}"
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=payload,
                params=params,
                url="https://app.pagerduty.com/on-call-coverage",
            )
        except Exception as e:
            logging.exception("Failed to get PagerDuty on-call")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )

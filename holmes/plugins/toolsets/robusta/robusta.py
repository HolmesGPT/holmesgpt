import logging
import os
from typing import Dict, Optional

from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import (
    StaticPrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)

PARAM_FINDING_ID = "id"


class FetchRobustaFinding(Tool):
    _dal: Optional[SupabaseDal]

    def __init__(self, dal: Optional[SupabaseDal]):
        super().__init__(
            name="fetch_finding_by_id",
            description="Fetches a robusta finding. Findings are events, like a Prometheus alert or a deployment update and configuration change.",
            parameters={
                PARAM_FINDING_ID: ToolParameter(
                    description="The id of the finding to fetch",
                    type="string",
                    required=True,
                )
            },
        )
        self._dal = dal

    def _fetch_finding(self, finding_id: str) -> Optional[Dict]:
        if self._dal and self._dal.enabled:
            return self._dal.get_issue_data(finding_id)
        else:
            error = f"Failed to find a finding with finding_id={finding_id}: Holmes' data access layer is not enabled."
            logging.error(error)
            return {"error": error}

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        finding_id = params[PARAM_FINDING_ID]
        try:
            finding = self._fetch_finding(finding_id)
            if finding:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=finding,
                    params=params,
                )
            else:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data=f"Could not find a finding with finding_id={finding_id}",
                    params=params,
                )
        except Exception as e:
            logging.error(e)
            logging.error(
                f"There was an internal error while fetching finding {finding_id}. {str(e)}"
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data=f"There was an internal error while fetching finding {finding_id}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"Robusta: Fetch finding data {params}"


class RobustaToolset(Toolset):
    def __init__(self, dal: Optional[SupabaseDal]):
        dal_prereq = StaticPrerequisite(
            enabled=True if dal else False,
            disabled_reason="Integration with Robusta cloud is disabled",
        )
        if dal:
            dal_prereq = StaticPrerequisite(
                enabled=dal.enabled, disabled_reason="Data access layer is disabled"
            )

        # fetch_resource_issues_metadata, fetch_configuration_changes_metadata
        # and fetch_resource_recommendation are no longer defined here: they are
        # served by the Robusta platform MCP server (relay's platform-mcp app)
        # and discovered through the robusta_platform_mcp toolset, which
        # enforces per-user cluster RBAC server-side. The LLM instructions for
        # them remain in robusta_instructions.jinja2 below.
        tools = [
            FetchRobustaFinding(dal),
        ]

        super().__init__(
            icon_url="https://cdn.prod.website-files.com/633e9bac8f71dfb7a8e4c9a6/646be7710db810b14133bdb5_logo.svg",
            description="Fetches detailed data about Robusta findings (alerts and configuration changes)",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/robusta/",
            name="robusta",
            prerequisites=[dal_prereq],
            tools=tools,
            tags=[
                ToolsetTag.CORE,
            ],
        )
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "robusta_instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

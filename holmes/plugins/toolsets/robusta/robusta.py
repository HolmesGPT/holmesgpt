import logging
import os
from typing import Dict, List, Optional

from holmes.core.supabase_dal import FindingType, SupabaseDal
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
START_TIME = "start_datetime"
END_TIME = "end_datetime"
NAMESPACE = "namespace"
WORKLOAD = "workload"
DEFAULT_LIMIT_CHANGE_ROWS = 100
MAX_LIMIT_CHANGE_ROWS = 200
DEFAULT_LIMIT_KRR_ROWS = 10
MAX_LIMIT_KRR_ROWS = 1000


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


class FetchResourceRecommendation(Tool):
    _dal: Optional[SupabaseDal]

    def __init__(self, dal: Optional[SupabaseDal]):
        super().__init__(
            name="fetch_resource_recommendation",
            description=(
                "Fetch KRR (Kubernetes Resource Recommendations) for CPU and memory optimization. "
                "KRR provides AI-powered recommendations based on actual historical usage patterns for right-sizing workloads. "
                "Supports two usage modes: "
                "(1) Specific workload lookup - Use name_pattern with an exact name, namespace, and kind to get recommendations for a single workload. "
                "(2) Discovery mode - Use limit and sort_by to get a ranked list of top optimization opportunities. "
                "Optionally filter by namespace, name_pattern (wildcards supported), kind, or container. "
                "Returns current configured resources alongside recommended values. In discovery mode, results are sorted by potential savings. "
                "CLUSTER SCOPE: By default, queries the current cluster only. Set all_clusters=true to search across all clusters, "
                "or provide a specific list of cluster names in the 'clusters' parameter."
            ),
            parameters={
                "limit": ToolParameter(
                    description=f"Maximum number of recommendations to return (default: {DEFAULT_LIMIT_KRR_ROWS}, max: {MAX_LIMIT_KRR_ROWS}).",
                    type="integer",
                    required=False,
                ),
                "sort_by": ToolParameter(
                    description=(
                        "Field to sort recommendations by potential savings. Options: "
                        "'cpu_total' (default) - Total CPU savings (requests + limits), "
                        "'memory_total' - Total memory savings (requests + limits), "
                        "'cpu_requests' - CPU requests savings, "
                        "'memory_requests' - Memory requests savings, "
                        "'cpu_limits' - CPU limits savings, "
                        "'memory_limits' - Memory limits savings, "
                        "'priority' - Use scan priority field."
                    ),
                    type="string",
                    required=False,
                ),
                "namespace": ToolParameter(
                    description="Filter by Kubernetes namespace (exact match). Leave empty to search all namespaces.",
                    type="string",
                    required=False,
                ),
                "name_pattern": ToolParameter(
                    description=(
                        "Filter by workload name pattern. Supports SQL LIKE patterns: "
                        "Use '%' as wildcard (e.g., '%app%' matches any name containing 'app', "
                        "'prod-%' matches names starting with 'prod-'). "
                        "Leave empty to match all names."
                    ),
                    type="string",
                    required=False,
                ),
                "kind": ToolParameter(
                    description=(
                        "Filter by Kubernetes resource kind. "
                        "Must be one of: Deployment, StatefulSet, DaemonSet, Job. "
                        "Leave empty to include all kinds."
                    ),
                    type="string",
                    required=False,
                ),
                "container": ToolParameter(
                    description="Filter by container name (exact match). Leave empty to include all containers.",
                    type="string",
                    required=False,
                ),
                "all_clusters": ToolParameter(
                    description=(
                        "If true, search across ALL clusters in the account instead of just the current cluster. "
                        "Default is false (current cluster only). Use this when investigating cross-cluster issues "
                        "or when the user asks about recommendations across their entire infrastructure."
                    ),
                    type="boolean",
                    required=False,
                ),
                "clusters": ToolParameter(
                    description=(
                        "Optional list of specific cluster names to query. If provided, overrides all_clusters. "
                        "Use this to query a specific subset of clusters. Example: ['prod-us-east', 'prod-us-west']. "
                        "Leave empty to use default behavior (current cluster or all clusters based on all_clusters flag)."
                    ),
                    type="array",
                    required=False,
                ),
            },
        )
        self._dal = dal

    def _fetch_recommendations(self, params: Dict) -> Optional[List[Dict]]:
        if self._dal and self._dal.enabled:
            # Set default values and enforce max limit
            limit = min(
                params.get("limit") or DEFAULT_LIMIT_KRR_ROWS,
                MAX_LIMIT_KRR_ROWS,
            )
            sort_by = params.get("sort_by") or "cpu_total"

            # Determine cluster scope
            clusters: Optional[List[str]] = None
            if params.get("clusters"):
                clusters = params["clusters"]
            elif params.get("all_clusters"):
                clusters = ["*"]
            # else: None means current cluster only

            return self._dal.get_resource_recommendation(
                limit=limit,
                sort_by=sort_by,
                namespace=params.get("namespace"),
                name_pattern=params.get("name_pattern"),
                kind=params.get("kind"),
                container=params.get("container"),
                clusters=clusters,
            )
        return None

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            recommendations = self._fetch_recommendations(params)
            if recommendations:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=recommendations,
                    params=params,
                )
            else:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data=f"Could not find any recommendations with filters: {params}",
                    params=params,
                )
        except Exception as e:
            msg = f"There was an error while fetching top recommendations for {params}. {str(e)}"
            logging.exception(msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"Robusta: Fetch KRR Recommendations ({str(params)})"


class FetchConfigurationChangesMetadata(Tool):
    """
    Unified tool for fetching configuration changes from both Kubernetes clusters
    and external sources (e.g., LaunchDarkly, feature flags).
    """

    _dal: Optional[SupabaseDal]

    def __init__(self, dal: Optional[SupabaseDal]):
        super().__init__(
            name="fetch_configuration_changes_metadata",
            description=(
                "Fetch configuration changes metadata in a given time range. "
                "Returns changes from Kubernetes clusters (deployments, configmaps, secrets, etc.) "
                "and external sources (e.g., LaunchDarkly feature flag changes). "
                "CLUSTER SCOPE: By default, queries the CURRENT cluster only and includes external changes. "
                "Set all_clusters=true to search across ALL clusters. "
                "Set include_external=false to exclude external (non-Kubernetes) changes. "
                "Can be filtered by namespace or specific workload name. "
                "Use fetch_finding_by_id to get detailed information about a specific change."
            ),
            parameters={
                START_TIME: ToolParameter(
                    description="The starting time boundary for the search period. String in RFC3339 format.",
                    type="string",
                    required=True,
                ),
                END_TIME: ToolParameter(
                    description="The ending time boundary for the search period. String in RFC3339 format.",
                    type="string",
                    required=True,
                ),
                "limit": ToolParameter(
                    description=f"Maximum number of rows to return. Default is {DEFAULT_LIMIT_CHANGE_ROWS} and the maximum is {MAX_LIMIT_CHANGE_ROWS}.",
                    type="integer",
                    required=False,
                ),
                "namespace": ToolParameter(
                    description="Filter by Kubernetes namespace (exact match). Only applies to cluster changes, not external changes.",
                    type="string",
                    required=False,
                ),
                "workload": ToolParameter(
                    description=(
                        "Filter by Kubernetes resource name (e.g., Pod, Deployment, Job). "
                        "Must be the full name. For Pods, include the exact generated suffix. "
                        "Only applies to cluster changes, not external changes."
                    ),
                    type="string",
                    required=False,
                ),
                "include_external": ToolParameter(
                    description=(
                        "If true (default), include external configuration changes not associated with any Kubernetes cluster "
                        "(e.g., LaunchDarkly feature flag changes, external system configurations). "
                        "Set to false to only see Kubernetes cluster changes."
                    ),
                    type="boolean",
                    required=False,
                ),
                "all_clusters": ToolParameter(
                    description=(
                        "If true, search across ALL Kubernetes clusters in the account instead of just the current cluster. "
                        "Default is false (current cluster only). Use this when investigating cross-cluster issues "
                        "or when the user asks about changes across their entire infrastructure."
                    ),
                    type="boolean",
                    required=False,
                ),
                "clusters": ToolParameter(
                    description=(
                        "Optional list of specific cluster names to query. If provided, overrides all_clusters. "
                        "Use this to query a specific subset of clusters. Example: ['prod-us-east', 'prod-us-west']. "
                        "Leave empty to use default behavior (current cluster or all clusters based on all_clusters flag)."
                    ),
                    type="array",
                    required=False,
                ),
            },
        )
        self._dal = dal

    def _fetch_issues(
        self,
        params: Dict,
        finding_type: FindingType = FindingType.CONFIGURATION_CHANGE,
    ) -> Optional[List[Dict]]:
        if self._dal and self._dal.enabled:
            # Determine cluster scope
            clusters: Optional[List[str]] = None
            if params.get("clusters"):
                clusters = params["clusters"]
            elif params.get("all_clusters"):
                clusters = ["*"]
            # else: None means current cluster only

            # Default include_external to True
            include_external = params.get("include_external")
            if include_external is None:
                include_external = True

            return self._dal.get_issues_metadata(
                start_datetime=params["start_datetime"],
                end_datetime=params["end_datetime"],
                limit=min(
                    params.get("limit") or DEFAULT_LIMIT_CHANGE_ROWS,
                    MAX_LIMIT_CHANGE_ROWS,
                ),
                ns=params.get("namespace"),
                workload=params.get("workload"),
                clusters=clusters,
                include_external=include_external,
                finding_type=finding_type,
            )
        return None

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            changes = self._fetch_issues(params)
            if changes:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=changes,
                    params=params,
                )
            else:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data=f"{self.name} found no data. {params}",
                    params=params,
                )
        except Exception as e:
            msg = f"There was an internal error while fetching changes for {params}. {str(e)}"
            logging.exception(msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data=msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"Robusta: Search Change History {params}"


class FetchResourceIssuesMetadata(Tool):
    """
    Fetch issues and alerts metadata with multi-cluster support.
    """

    _dal: Optional[SupabaseDal]

    def __init__(self, dal: Optional[SupabaseDal]):
        super().__init__(
            name="fetch_resource_issues_metadata",
            description=(
                "Fetch issues and alert metadata in a given time range. "
                "Can be filtered by namespace and/or specific Kubernetes resource. "
                "CLUSTER SCOPE: By default, queries the CURRENT cluster only. "
                "Set all_clusters=true to search across ALL clusters for issues affecting similar workloads. "
                "Use fetch_finding_by_id to get detailed information about a specific issue or alert."
            ),
            parameters={
                START_TIME: ToolParameter(
                    description="The starting time boundary for the search period. String in RFC3339 format.",
                    type="string",
                    required=True,
                ),
                END_TIME: ToolParameter(
                    description="The ending time boundary for the search period. String in RFC3339 format.",
                    type="string",
                    required=True,
                ),
                "limit": ToolParameter(
                    description=f"Maximum number of rows to return. Default is {DEFAULT_LIMIT_CHANGE_ROWS} and the maximum is {MAX_LIMIT_CHANGE_ROWS}.",
                    type="integer",
                    required=False,
                ),
                "namespace": ToolParameter(
                    description="Filter by Kubernetes namespace (exact match).",
                    type="string",
                    required=False,
                ),
                "workload": ToolParameter(
                    description=(
                        "Filter by Kubernetes resource name (e.g., Pod, Deployment, Job). "
                        "Must be the full name. For Pods, include the exact generated suffix."
                    ),
                    type="string",
                    required=False,
                ),
                "all_clusters": ToolParameter(
                    description=(
                        "If true, search across ALL Kubernetes clusters in the account instead of just the current cluster. "
                        "Default is false (current cluster only). Use this when investigating cross-cluster issues "
                        "or when the user asks about issues across their entire infrastructure."
                    ),
                    type="boolean",
                    required=False,
                ),
                "clusters": ToolParameter(
                    description=(
                        "Optional list of specific cluster names to query. If provided, overrides all_clusters. "
                        "Use this to query a specific subset of clusters. Example: ['prod-us-east', 'prod-us-west']. "
                        "Leave empty to use default behavior (current cluster or all clusters based on all_clusters flag)."
                    ),
                    type="array",
                    required=False,
                ),
            },
        )
        self._dal = dal

    def _fetch_issues(self, params: Dict) -> Optional[List[Dict]]:
        if self._dal and self._dal.enabled:
            # Determine cluster scope
            clusters: Optional[List[str]] = None
            if params.get("clusters"):
                clusters = params["clusters"]
            elif params.get("all_clusters"):
                clusters = ["*"]
            # else: None means current cluster only

            return self._dal.get_issues_metadata(
                start_datetime=params["start_datetime"],
                end_datetime=params["end_datetime"],
                limit=min(
                    params.get("limit") or DEFAULT_LIMIT_CHANGE_ROWS,
                    MAX_LIMIT_CHANGE_ROWS,
                ),
                ns=params.get("namespace"),
                workload=params.get("workload"),
                clusters=clusters,
                include_external=False,  # Issues don't have external sources
                finding_type=FindingType.ISSUE,
            )
        return None

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            issues = self._fetch_issues(params)
            if issues:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=issues,
                    params=params,
                )
            else:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data=f"{self.name} found no data. {params}",
                    params=params,
                )
        except Exception as e:
            msg = f"There was an internal error while fetching issues for {params}. {str(e)}"
            logging.exception(msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data=msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"Robusta: Fetch Resource Issues {params}"


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

        tools = [
            FetchRobustaFinding(dal),
            FetchConfigurationChangesMetadata(dal),
            FetchResourceRecommendation(dal),
            FetchResourceIssuesMetadata(dal),
        ]

        super().__init__(
            icon_url="https://cdn.prod.website-files.com/633e9bac8f71dfb7a8e4c9a6/646be7710db810b14133bdb5_logo.svg",
            description="Fetches alerts metadata and change history",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/robusta/",
            name="robusta",
            prerequisites=[dal_prereq],
            tools=tools,
            tags=[
                ToolsetTag.CORE,
            ],
            is_default=True,
        )
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "robusta_instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

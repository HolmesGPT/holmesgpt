import logging
import os
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, cast

from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.runbooks import (
    DEFAULT_RUNBOOK_SEARCH_PATH,
    get_runbook_by_path,
    load_runbook_catalog,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class RunbookFetcher(Tool):
    toolset: "RunbookToolset"
    available_runbooks: List[str] = []
    additional_search_paths: Optional[List[str]] = None
    _dal: Optional[SupabaseDal] = None

    def __init__(
        self,
        toolset: "RunbookToolset",
        additional_search_paths: Optional[List[str]] = None,
        dal: Optional[SupabaseDal] = None,
        custom_catalog_paths: Optional[List[Union[str, Path]]] = None,
    ):
        catalog = load_runbook_catalog(
            dal=dal, custom_catalog_paths=custom_catalog_paths
        )
        available_runbooks = []
        if catalog:
            available_runbooks = catalog.list_available_runbooks()

        if additional_search_paths:
            for search_path in additional_search_paths:
                if not os.path.isdir(search_path):
                    continue

                for file in os.listdir(search_path):
                    if file.endswith(".md") and file not in available_runbooks:
                        available_runbooks.append(f"{file}")

        runbook_list = ", ".join([f'"{rb}"' for rb in available_runbooks])

        super().__init__(
            name="fetch_runbook",
            description="Get runbook content by runbook link. Use this to get troubleshooting steps for incidents",
            parameters={
                "runbook_id": ToolParameter(
                    description=f"The runbook_id: either a UUID or a .md filename. Must be one of: {runbook_list}",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,  # type: ignore[call-arg]
            available_runbooks=available_runbooks,  # type: ignore[call-arg]
            additional_search_paths=additional_search_paths,  # type: ignore[call-arg]
        )
        self._dal = dal

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        runbook_id: str = params.get("runbook_id", "")
        is_md_file: bool = True if runbook_id.endswith(".md") else False

        # Validate link is not empty
        if not runbook_id or not runbook_id.strip():
            err_msg = (
                "Runbook link cannot be empty. Please provide a valid runbook path."
            )
            logging.error(err_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=err_msg,
                params=params,
            )

        if is_md_file:
            return self._get_md_runbook(runbook_id, params)
        else:
            return self._get_robusta_runbook(runbook_id, params)

    def _get_robusta_runbook(self, link: str, params: dict) -> StructuredToolResult:
        if self._dal and self._dal.enabled:
            try:
                runbook_content = self._dal.get_runbook_content(link)
                if runbook_content:
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.SUCCESS,
                        data=runbook_content.pretty(),
                        params=params,
                    )
                else:
                    err_msg = f"Runbook with UUID '{link}' not found in remote storage."
                    logging.error(err_msg)
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error=err_msg,
                        params=params,
                    )
            except Exception as e:
                err_msg = f"Failed to fetch runbook with UUID '{link}': {str(e)}"
                logging.error(err_msg)
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=err_msg,
                    params=params,
                )
        else:
            err_msg = "Runbook link appears to be a UUID, but no remote data access layer (dal) is enabled."
            logging.error(err_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=err_msg,
                params=params,
            )

    def _get_md_runbook(self, link: str, params: dict) -> StructuredToolResult:
        search_paths = [DEFAULT_RUNBOOK_SEARCH_PATH]
        if self.additional_search_paths:
            search_paths.extend(self.additional_search_paths)
        # Validate link is in the available runbooks list OR is a valid path within allowed directories
        if link not in self.available_runbooks:
            # Check if the link would resolve to a valid path within allowed directories
            # This prevents path traversal attacks like ../../secret.md
            is_valid_path = False
            for search_path in search_paths:
                candidate_path = os.path.join(search_path, link)
                # Canonicalize both paths to resolve any .. or . components
                real_search_path = os.path.realpath(search_path)
                real_candidate_path = os.path.realpath(candidate_path)

                # Check if the resolved path is within the allowed directory
                if (
                    real_candidate_path.startswith(real_search_path + os.sep)
                    or real_candidate_path == real_search_path
                ):
                    if os.path.isfile(real_candidate_path):
                        is_valid_path = True
                        break

            if not is_valid_path:
                err_msg = f"Invalid runbook link '{link}'. Must be one of: {', '.join(self.available_runbooks) if self.available_runbooks else 'No runbooks available'}"
                logging.error(err_msg)
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=err_msg,
                    params=params,
                )

        runbook_path = get_runbook_by_path(link, search_paths)
        if runbook_path is None:
            err_msg = (
                f"Runbook '{link}' not found in any of the search paths: {search_paths}"
            )
            logging.error(err_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=err_msg,
                params=params,
            )
        try:
            with open(runbook_path, "r") as file:
                content = file.read()
                wrapped_content = textwrap.dedent(f"""\
                    <runbook>
{textwrap.indent(content, " " * 20)}
                    </runbook>
                    Note: the above are DIRECTIONS not ACTUAL RESULTS. You now need to follow the steps outlined in the runbook yourself USING TOOLS.
                    Anything that looks like an actual result in the above <runbook> is just an EXAMPLE.
                    Now follow those steps and report back what you find.
                    You must follow them by CALLING TOOLS YOURSELF.
                    If you are missing tools, follow your general instructions on how to enable them as present in your system prompt.

                    Assuming the above runbook is relevant, you MUST start your response (after calling tools to investigate) with:
                    "I found a runbook named [runbook name/description] and used it to troubleshoot:"

                    Then list each step with ✅ for completed steps and ❌ for steps you couldn't complete.

                    <example>
                        I found a runbook named **Troubleshooting Erlang Issues** and used it to troubleshoot:

                        1. ✅ *Check BEAM VM memory usage* - 87% allocated (3.2GB used of 4GB limit)
                        2. ✅ *Review GC logs* - 15 full GC cycles in last 30 minutes, avg pause time 2.3s
                        3. ✅ *Verify Erlang application logs* - `** exception error: out of memory in process <0.139.0> called by gen_server:handle_msg/6`
                        4. ❌ *Could not analyze process mailbox sizes* - Observer tool not enabled in container. Enable remote shell or observer_cli for process introspection.
                        5. ✅ *Check pod memory limits* - container limit 4Gi, requests 2Gi
                        6. ✅ *Verify BEAM startup arguments* - `+S 4:4 +P 1048576`, no memory instrumentation flags enabled
                        7. ❌ *Could not retrieve APM traces* - Datadog traces toolset is disabled. You can enable it by following https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/
                        8. ❌ *Could not query Erlang metrics* - Prometheus integration is not connected. Enable it via https://holmesgpt.dev/data-sources/builtin-toolsets/prometheus/
                        9. ✅ *Examine recent deployments* - app version 2.1.3 deployed 4 hours ago, coincides with memory spike
                        10. ❌ *Could not check Stripe API status* - No toolset for Stripe integration exists. To monitor Stripe or similar third-party APIs, add a [custom toolset](https://holmesgpt.dev/data-sources/custom-toolsets/) or use a [remote MCP server](https://holmesgpt.dev/data-sources/remote-mcp-servers/)

                        **Root cause:** Memory leak in `gen_server` logic introduced in v2.1.3. BEAM VM hitting memory limit, causing out-of-memory crashes.

                        **Fix:** Roll back to v2.1.2 or increase memory limit to 6GB as a temporary workaround.
                    </example>
                """)
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=wrapped_content,
                    params=params,
                )
        except Exception as e:
            err_msg = f"Failed to read runbook {runbook_path}: {str(e)}"
            logging.error(err_msg)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=err_msg,
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        path: str = params.get("runbook_id", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Fetch Runbook {path}"


class RunbookToolset(Toolset):
    def __init__(
        self,
        dal: Optional[SupabaseDal],
        additional_search_paths: Optional[List[str]] = None,
    ):
        # Store additional search paths in config for RunbookFetcher to access
        config = {}
        if additional_search_paths:
            config["additional_search_paths"] = additional_search_paths

        # Compute custom catalog paths from additional search paths
        custom_catalog_paths = None
        if additional_search_paths:
            custom_catalog_paths = [
                os.path.join(search_path, "catalog.json")
                for search_path in additional_search_paths
                if os.path.isfile(os.path.join(search_path, "catalog.json"))
            ]

        super().__init__(
            name="runbook",
            description="Fetch runbooks",
            icon_url="https://platform.robusta.dev/demos/runbook.svg",
            tools=[
                RunbookFetcher(
                    self,
                    additional_search_paths,
                    dal,
                    cast(Optional[List[Union[str, Path]]], custom_catalog_paths),
                ),
            ],
            docs_url="https://holmesgpt.dev/data-sources/",
            tags=[
                ToolsetTag.CORE,
            ],
            is_default=True,
            config=config,
            enabled=True,
        )

    def get_example_config(self) -> Dict[str, Any]:
        return {}

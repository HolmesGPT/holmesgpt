import os
import logging


import requests  # type: ignore
from cachetools import TTLCache  # type: ignore
from holmes.core.tools import (
    CallablePrerequisite,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    ToolsetTag,
)
import json
from requests import RequestException

from holmes.plugins.toolsets.opensearch.opensearch_utils import (
    BaseOpenSearchToolset,
    add_auth_header,
    get_search_url,
)
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.plugins.toolsets.utils import get_param_or_raise, toolset_name_for_one_liner

TRACES_FIELDS_CACHE_KEY = "cached_traces_fields"


class GetTracesFields(Tool):
    def __init__(self, toolset: BaseOpenSearchToolset):
        super().__init__(
            name="get_traces_fields",
            description="Get all the fields in the traces documents",
            parameters={},
        )
        self._toolset = toolset
        self._cache = None

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            if not self._cache and self._toolset.opensearch_config.fields_ttl_seconds:
                self._cache = TTLCache(
                    maxsize=5, ttl=self._toolset.opensearch_config.fields_ttl_seconds
                )

            if self._cache:
                cached_response = self._cache.get(TRACES_FIELDS_CACHE_KEY, None)
                if cached_response:
                    logging.debug("traces fields returned from cache")
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.SUCCESS,
                        data=cached_response,
                        params=params,
                    )

            body = {
                "size": 1,
                "_source": False,
                "script_fields": {
                    "all_fields": {
                        "script": {
                            "lang": "painless",
                            "source": "Map fields = new HashMap(); List stack = new ArrayList(); stack.add(['prefix': '', 'obj': params['_source']]); while (!stack.isEmpty()) { Map item = stack.remove(stack.size() - 1); String prefix = item.get('prefix'); Map obj = item.get('obj'); for (entry in obj.entrySet()) { String fullPath = prefix.isEmpty() ? entry.getKey() : prefix + '.' + entry.getKey(); fields.put(fullPath, true); if (entry.getValue() instanceof Map) { stack.add(['prefix': fullPath, 'obj': entry.getValue()]); } } } return fields.keySet();",
                        }
                    }
                },
            }
            headers = {"Content-Type": "application/json"}
            headers.update(
                add_auth_header(self._toolset.opensearch_config.opensearch_auth_header)
            )
            logs_response = requests.get(
                url=get_search_url(self._toolset.opensearch_config),
                timeout=180,
                verify=True,
                data=json.dumps(body),
                headers=headers,
            )
            logs_response.raise_for_status()
            response = json.dumps(logs_response.json())
            if self._cache:
                self._cache[TRACES_FIELDS_CACHE_KEY] = response
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response,
                params=params,
            )
        except requests.Timeout:
            logging.warning(
                "Timeout while fetching opensearch traces fields", exc_info=True
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Request timed out while fetching opensearch traces fields",
                params=params,
            )
        except RequestException as e:
            logging.warning("Failed to fetch opensearch traces fields", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Network error while opensearch traces fields: {str(e)}",
                params=params,
            )
        except Exception as e:
            logging.warning("Failed to process opensearch traces fields", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List Trace Fields"


class TracesSearchQuery(Tool):
    def __init__(self, toolset: "OpenSearchTracesToolset"):
        super().__init__(
            name="traces_in_range_search",
            description="Get traces in a specified time range for an opensearch query",
            parameters={
                "query": ToolParameter(
                    description="An OpenSearch search query. It should be a stringified json, matching opensearch search syntax. "
                    "The query must contain a 'range' on the startTimeMillis field. From a given startTimeMillis, until a given startTimeMillis. This is time in milliseconds",
                    type="string",
                ),
            },
        )
        self._toolset = toolset
        self._cache = None

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        err_msg = ""
        try:
            body = json.loads(get_param_or_raise(params, "query"))
            full_query = body
            full_query["size"] = int(
                os.environ.get("OPENSEARCH_TRACES_SEARCH_SIZE", "5000")
            )
            logging.debug(f"opensearch traces search query: {full_query}")
            headers = {"Content-Type": "application/json"}
            headers.update(
                add_auth_header(self._toolset.opensearch_config.opensearch_auth_header)
            )

            logs_response = requests.get(
                url=get_search_url(self._toolset.opensearch_config),
                timeout=180,
                verify=True,
                data=json.dumps(full_query),
                headers=headers,
            )
            if logs_response.status_code > 300:
                err_msg = logs_response.text

            logs_response.raise_for_status()
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=json.dumps(logs_response.json()),
                params=params,
            )
        except requests.Timeout:
            logging.warning(
                "Timeout while fetching opensearch traces search", exc_info=True
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Request timed out while fetching opensearch traces search {err_msg}",
                params=params,
            )
        except RequestException as e:
            logging.warning("Failed to fetch opensearch traces search", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Network error while opensearch traces search {err_msg} : {str(e)}",
                params=params,
            )
        except Exception as e:
            logging.warning(
                "Failed to process opensearch traces search ", exc_info=True
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error {err_msg}: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        query = params.get("query", "")
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: Search Traces ({query})"
        )


class OpenSearchTracesToolset(BaseOpenSearchToolset):
    def __init__(self):
        super().__init__(
            name="opensearch/traces",
            description="OpenSearch integration to fetch traces",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/opensearch-status/",
            icon_url="https://opensearch.org/assets/brand/PNG/Mark/opensearch_mark_default.png",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                GetTracesFields(toolset=self),
                TracesSearchQuery(toolset=self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
        )
        template_file_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "opensearch_traces_instructions.jinja2"
            )
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

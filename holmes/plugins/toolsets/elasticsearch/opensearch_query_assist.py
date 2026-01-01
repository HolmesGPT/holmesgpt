import logging
import os
from typing import Any, Dict

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolParameter,
    Toolset,
    ToolsetTag,
    ToolInvokeContext,
    ToolsetEnvironmentPrerequisite,
)


class PplQueryAssistTool(Tool):
    def __init__(self, toolset: "OpenSearchQueryAssistToolset"):
        super().__init__(
            name="opensearch_ppl_query_assist",
            description="Generate valid OpenSearch Piped Processing Language (PPL) queries to suggest to users for execution",
            parameters={
                "query": ToolParameter(
                    description="Valid OpenSearch Piped Processing Language (PPL) query to suggest to users for execution",
                    type="string",
                    required=True,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            query = params.get("query", "")
            response_data = {"query": query}
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response_data,
                params=params,
            )

        except Exception as e:
            logging.exception(f"error using {self.name} tool")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to generate PPL query: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        query = params.get("query", "")
        return f"OpenSearchQueryToolset: Query ({query})"


class OpenSearchQueryAssistToolset(Toolset):
    """OpenSearch query assist with PPL queries"""

    def __init__(self):
        super().__init__(
            name="opensearch/query_assist",
            description="OpenSearch query assist with PPL queries.",
            experimental=True,
            icon_url="https://opensearch.org/assets/brand/PNG/Mark/opensearch_mark_default.png",
            tools=[PplQueryAssistTool(self)],
            tags=[ToolsetTag.CORE],
            prerequisites=[ToolsetEnvironmentPrerequisite(env=["OPENSEARCH_URL"])],
        )

    def get_example_config(self) -> Dict[str, Any]:
        return {"opensearch_url": "http://localhost:9200"}

    def _reload_instructions(self):
        template_file_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "opensearch_query_assist_instructions.jinja2"
            )
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

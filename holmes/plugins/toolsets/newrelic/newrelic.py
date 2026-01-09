import base64
import json
import logging
import os
from typing import Any, Dict, Optional

from pydantic import BaseModel

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.newrelic.new_relic_api import NewRelicAPI
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


def _build_newrelic_query_url(
    account_id: str,
    nrql_query: str,
    is_eu_datacenter: bool = False,
) -> Optional[str]:
    """Build a New Relic query URL for the NRQL query builder.

    Note: URL links to queries are not officially supported by New Relic, so we are using
    a workaround to open their overlay to the query builder with the query pre-filled.
    This uses the dashboard launcher with an overlay parameter to open the query builder nerdlet.

    """
    try:
        base_url = (
            "https://one.eu.newrelic.com"
            if is_eu_datacenter
            else "https://one.newrelic.com"
        )

        account_id_int = int(account_id) if isinstance(account_id, str) else account_id

        overlay = {
            "nerdletId": "data-exploration.query-builder",
            "initialActiveInterface": "nrqlEditor",
            "initialQueries": [
                {
                    "accountId": account_id_int,
                    "nrql": nrql_query,
                }
            ],
        }

        overlay_json = json.dumps(overlay, separators=(",", ":"))
        overlay_base64 = base64.b64encode(overlay_json.encode("utf-8")).decode("utf-8")

        pane = {
            "nerdletId": "dashboards.list",
            "entityDomain": "VIZ",
            "entityType": "DASHBOARD",
        }
        pane_json = json.dumps(pane, separators=(",", ":"))
        pane_base64 = base64.b64encode(pane_json.encode("utf-8")).decode("utf-8")

        url = (
            f"{base_url}/launcher/dashboards.launcher"
            f"?pane={pane_base64}"
            f"&overlay={overlay_base64}"
        )

        return url
    except Exception:
        return None


class ExecuteNRQLQuery(Tool):
    def __init__(self, toolset: "NewRelicToolset"):
        super().__init__(
            name="newrelic_execute_nrql_query",
            description="Get Traces, APM, Spans, Logs and more by executing a NRQL query in New Relic. "
            "Returns the result of the NRQL function. "
            "⚠️ CRITICAL: NRQL silently returns empty results for invalid queries instead of errors. "
            "If you get empty results, your query likely has issues such as: "
            "1) Wrong attribute names (use SELECT keyset() first to verify), "
            "2) Type mismatches (string vs numeric fields), "
            "3) Wrong event type. "
            "Always verify attribute names and types before querying.",
            parameters={
                "query": ToolParameter(
                    description="""The NRQL query string to execute.

MANDATORY: Before querying any event type, ALWAYS run `SELECT keyset() FROM <EventType> SINCE <timeframe>` to discover available attributes. Never use attributes without confirming they exist first. Make sure to remember which fields are stringKeys, numericKeys or booleanKeys as this will be important in subsequent queries.

Example: Before querying Transactions, run: `SELECT keyset() FROM Transaction SINCE 24 hours ago`

### ⚠️ Critical Rule: NRQL `FACET` Usa ge

When using **FACET** in NRQL:
- Any **non-constant value** in the `SELECT` clause **must be aggregated**.
- The attribute you **FACET** on must **not appear in `SELECT`** unless it's wrapped in an aggregation.

#### ✅ Correct
```nrql
-- Aggregated metric + facet
SELECT count(*) FROM Transaction FACET transactionType

-- Multiple aggregations with facet
SELECT count(*), average(duration) FROM Transaction FACET transactionType
```

#### ❌ Incorrect
```nrql
-- Not allowed: raw attribute in SELECT
SELECT count(*), transactionType FROM Transaction FACET transactionType
```
""",
                    type="string",
                    required=True,
                ),
                "description": ToolParameter(
                    description="A breif 6 word human understandable description of the query you are running.",
                    type="string",
                    required=True,
                ),
                "query_type": ToolParameter(
                    description="Either 'Metrics', 'Logs', 'Traces', 'Discover Attributes' or 'Other'.",
                    type="string",
                    required=True,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self._toolset.nr_api_key or not self._toolset.nr_account_id:
            raise ValueError("NewRelic API key or account ID is not configured")

        api = NewRelicAPI(
            api_key=self._toolset.nr_api_key,
            account_id=self._toolset.nr_account_id,
            is_eu_datacenter=self._toolset.is_eu_datacenter,
        )

        query = params["query"]
        result = api.execute_nrql_query(query)

        result_with_key = {
            "query": query,
            "data": result,
            "is_eu": self._toolset.is_eu_datacenter,
        }

        # Build New Relic query URL
        explore_url = _build_newrelic_query_url(
            account_id=self._toolset.nr_account_id,
            nrql_query=query,
            is_eu_datacenter=self._toolset.is_eu_datacenter,
        )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=result_with_key,
            params=params,
            url=explore_url,
        )

    def get_parameterized_one_liner(self, params) -> str:
        description = params.get("description", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Execute NRQL ({description})"


class NewrelicConfig(BaseModel):
    nr_api_key: Optional[str] = None
    nr_account_id: Optional[str] = None
    is_eu_datacenter: Optional[bool] = False


class NewRelicToolset(Toolset):
    nr_api_key: Optional[str] = None
    nr_account_id: Optional[str] = None
    is_eu_datacenter: bool = False

    def __init__(self):
        super().__init__(
            name="newrelic",
            description="Toolset for interacting with New Relic to fetch logs, traces, and execute freeform NRQL queries",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/newrelic/",
            icon_url="https://companieslogo.com/img/orig/NEWR-de5fcb2e.png?t=1720244493",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],  # type: ignore
            tools=[
                ExecuteNRQLQuery(self),
            ],
            tags=[ToolsetTag.CORE],
        )
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "newrelic.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

    def prerequisites_callable(
        self, config: dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        if not config:
            return False, "No configuration provided"

        try:
            nr_config = NewrelicConfig(**config)
            self.nr_account_id = nr_config.nr_account_id
            self.nr_api_key = nr_config.nr_api_key
            self.is_eu_datacenter = nr_config.is_eu_datacenter or False

            if not self.nr_account_id or not self.nr_api_key:
                return False, "New Relic account ID or API key is missing"

            return True, None
        except Exception as e:
            logging.exception("Failed to set up New Relic toolset")
            return False, str(e)

    def get_example_config(self) -> Dict[str, Any]:
        return {
            "nr_api_key": "NRAK-XXXXXXXXXXXXXXXXXXXXXXXXXX",
            "nr_account_id": "1234567",
            "is_eu_datacenter": False,
        }

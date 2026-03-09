import base64
import json
import logging
import os
from typing import Any, Optional

from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite,
    ClassVar,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
    Type,
)
from holmes.plugins.toolsets.newrelic.new_relic_api import NewRelicAPI
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig


def _build_newrelic_query_url(
    base_url: str,
    account_id: str,
    nrql_query: str,
) -> Optional[str]:
    """Build a New Relic query URL for the NRQL query builder.

    Note: URL links to queries are not officially supported by New Relic, so we are using
    a workaround to open their overlay to the query builder with the query pre-filled.
    This uses the dashboard launcher with an overlay parameter to open the query builder nerdlet.

    """
    try:
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
        parameters = {
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
                description="A brief 6 word human understandable description of the query you are running.",
                type="string",
                required=True,
            ),
            "query_type": ToolParameter(
                description="Either 'Metrics', 'Logs', 'Traces', 'Discover Attributes' or 'Other'.",
                type="string",
                required=True,
            ),
        }

        # Add account_id parameter only in multi-account mode
        if toolset.enable_multi_account:
            parameters["override_account_id"] = ToolParameter(
                description=(
                    f"A New Relic account ID is a numeric identifier, typically a 6-8 digit integer (e.g., 1234567). It contains only digits, has no prefixes or separators, and uniquely identifies a New Relic account. default: {toolset.account_id}"
                ),
                type="integer",
                required=True,
            )

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
            parameters=parameters,
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if self._toolset.enable_multi_account:
            effective_account_id = (
                params.get("override_account_id") or self._toolset.account_id
            )
            effective_account_id = str(effective_account_id)
        else:
            effective_account_id = self._toolset.account_id

        if not effective_account_id:
            raise ValueError("NewRelic account ID is not configured")

        api = self._toolset.create_api_client(effective_account_id)

        query = params["query"]
        result = api.execute_nrql_query(query)

        result_with_key = {
            "query": query,
            "data": result,
            "is_eu": self._toolset.is_eu_datacenter,
        }

        # Build New Relic query URL
        explore_url = _build_newrelic_query_url(
            base_url=self._toolset.base_url,
            account_id=effective_account_id,
            nrql_query=query,
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


class ListOrganizationAccounts(Tool):
    def __init__(self, toolset: "NewRelicToolset"):
        super().__init__(
            name="newrelic_list_organization_accounts",
            description=(
                "List all account names and IDs accessible in the New Relic organization. "
                "Use this tool to:\n"
                "1. Find the account ID when given an account name\n"
                "2. Map account names to IDs for running NRQL queries\n"
                "Returns a list of accounts with 'id' and 'name' fields."
            ),
            parameters={},
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        api = self._toolset.create_api_client(
            override_account_id="0"
        )  # organization query does not need account_id

        accounts = api.get_organization_accounts()

        result_with_key = {
            "accounts": accounts,
            "total_count": len(accounts),
            "is_eu": self._toolset.is_eu_datacenter,
        }

        # Build New Relic accounts URL
        accounts_url = (
            f"{self._toolset.base_url}/admin-portal/organizations/organization-detail"
        )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=result_with_key,
            params=params,
            url=accounts_url,
        )

    def get_parameterized_one_liner(self, params) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List organization accounts"


class NewrelicConfig(ToolsetConfig):
    _deprecated_mappings: ClassVar[dict[str, Optional[str]]] = {
        "nr_api_key": "api_key",
        "nr_account_id": "account_id",
    }

    api_key: str = Field(
        title="API Key",
        description="New Relic API key for authentication",
        examples=["NRAK-XXXXXXXXXXXXXXXXXXXXXXXXXX"],
    )
    account_id: str = Field(
        title="Account ID",
        description="New Relic account ID",
        examples=["1234567"],
    )
    is_eu_datacenter: Optional[bool] = Field(
        default=False,
        title="EU Datacenter",
        description="Whether to use EU datacenter (api.eu.newrelic.com) instead of US",
    )
    enable_multi_account: Optional[bool] = Field(
        default=False,
        title="Multi-Account Mode",
        description="Enable multi-account support for querying across accounts",
    )


class NewRelicToolset(Toolset):
    config_classes: ClassVar[list[Type[NewrelicConfig]]] = [NewrelicConfig]

    api_key: Optional[str] = None
    account_id: Optional[str] = None
    is_eu_datacenter: bool = False
    enable_multi_account: bool = False

    @property
    def base_url(self) -> str:
        """Get the New Relic base URL based on datacenter region."""
        return (
            "https://one.eu.newrelic.com"
            if self.is_eu_datacenter
            else "https://one.newrelic.com"
        )

    def create_api_client(
        self, override_account_id: Optional[str] = None
    ) -> NewRelicAPI:
        """Create a NewRelicAPI client instance.

        Args:
            override_account_id: Account ID to use. If None, uses the default from config.
                       Set to "0" for organization-level queries.

        Returns:
            Configured NewRelicAPI instance

        Raises:
            ValueError: If API key is not configured
        """
        if not self.api_key:
            raise ValueError("NewRelic API key is not configured")

        effective_account_id = (
            override_account_id if override_account_id is not None else self.account_id
        )

        if not effective_account_id:
            raise ValueError("NewRelic Account id is not configured")

        return NewRelicAPI(
            api_key=self.api_key,
            account_id=effective_account_id,
            is_eu_datacenter=self.is_eu_datacenter,
        )

    def __init__(self):
        super().__init__(
            name="newrelic",
            description="Toolset for interacting with New Relic to fetch logs, traces, and execute freeform NRQL queries",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/newrelic/",
            icon_url="https://companieslogo.com/img/orig/NEWR-de5fcb2e.png?t=1720244493",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],  # type: ignore
            tools=[],
            tags=[ToolsetTag.CORE],
        )

    def prerequisites_callable(
        self, config: dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        if not config:
            return False, "No configuration provided"

        try:
            nr_config = NewrelicConfig(**config)
            self.account_id = nr_config.account_id
            self.api_key = nr_config.api_key
            self.is_eu_datacenter = nr_config.is_eu_datacenter or False
            self.enable_multi_account = nr_config.enable_multi_account or False

            # Tool uses enable_multi_account flag.
            self.tools = [ExecuteNRQLQuery(self)]
            if self.enable_multi_account:
                self.tools.append(ListOrganizationAccounts(self))
            template_file_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "newrelic.jinja2")
            )
            self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

            return True, None
        except Exception as e:
            logging.exception("Failed to set up New Relic toolset")
            return False, str(e)

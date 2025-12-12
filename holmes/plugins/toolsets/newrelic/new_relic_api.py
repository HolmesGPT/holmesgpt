"""NewRelic API wrapper for executing NRQL queries via GraphQL."""

import logging
from typing import Any, Dict

import requests  # type: ignore


logger = logging.getLogger(__name__)


class NewRelicAPI:
    """Python wrapper for NewRelic GraphQL API.

    This class provides a clean interface to execute NRQL queries via the NewRelic GraphQL API,
    supporting both US and EU datacenters.
    """

    def __init__(self, api_key: str, account_id: str, is_eu_datacenter: bool = False):
        """Initialize the NewRelic API wrapper.

        Args:
            api_key: NewRelic API key
            account_id: NewRelic account ID
            is_eu_datacenter: If True, use EU datacenter URL. Defaults to False (US).
        """
        self.api_key = api_key
        # Validate account_id is numeric to prevent injection
        try:
            self.account_id = int(account_id)
        except ValueError:
            raise ValueError(f"Invalid account_id: must be numeric, got '{account_id}'")
        self.is_eu_datacenter = is_eu_datacenter

    def _get_api_url(self) -> str:
        """Get the appropriate API URL based on datacenter location.

        Returns:
            str: The GraphQL API endpoint URL
        """
        if self.is_eu_datacenter:
            return "https://api.eu.newrelic.com/graphql"
        return "https://api.newrelic.com/graphql"

    def _make_request(
        self, graphql_query: Dict[str, Any], timeout: int = 30
    ) -> Dict[str, Any]:
        """Make HTTP POST request to NewRelic GraphQL API.

        Args:
            graphql_query: The GraphQL query as a dictionary
            timeout: Request timeout in seconds

        Returns:
            JSON response from the API

        Raises:
            requests.exceptions.HTTPError: If the request fails
            Exception: If GraphQL returns errors
        """
        url = self._get_api_url()
        headers = {
            "Content-Type": "application/json",
            "Api-Key": self.api_key,
        }

        response = requests.post(
            url,
            headers=headers,
            json=graphql_query,
            timeout=timeout,
        )
        response.raise_for_status()

        # Parse JSON response
        data = response.json()

        # Check for GraphQL errors even on 200 responses
        if "errors" in data and data["errors"]:
            error_msg = data["errors"][0].get("message", "Unknown GraphQL error")
            raise Exception(f"NewRelic GraphQL error: {error_msg}")

        return data

    def execute_nrql_query(self, nrql_query: str) -> list:
        """Execute an NRQL query via the NewRelic GraphQL API.

        Args:
            nrql_query: The NRQL query string to execute

        Returns:
            list: The query results from NewRelic (extracted from the nested response)

        Raises:
            requests.exceptions.HTTPError: If the API request fails
            Exception: If GraphQL returns errors
        """
        # Build the GraphQL query using variables to prevent injection
        # Note: New Relic's GraphQL API requires the account ID to be inline, but we can use variables for the NRQL query
        graphql_query = {
            "query": f"""
            query ExecuteNRQL($nrqlQuery: Nrql!) {{
                actor {{
                    account(id: {self.account_id}) {{
                        nrql(query: $nrqlQuery) {{
                            results
                        }}
                    }}
                }}
            }}
            """,
            "variables": {"nrqlQuery": nrql_query},
        }

        logger.info(f"Executing NRQL query: {nrql_query}")
        response = self._make_request(graphql_query)

        # Extract just the results array from the nested response
        try:
            results = response["data"]["actor"]["account"]["nrql"]["results"]
            return results
        except (KeyError, TypeError) as e:
            raise Exception(
                f"Failed to extract results from NewRelic response: {e}"
            ) from e

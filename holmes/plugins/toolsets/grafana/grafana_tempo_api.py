"""Grafana Tempo API wrapper for querying traces and metrics."""

import logging
from typing import Any, Dict, Optional, Union
from urllib.parse import quote

import backoff
import requests  # type: ignore

from holmes.plugins.toolsets.grafana.common import (
    GrafanaTempoConfig,
    build_headers,
    get_base_url,
)


logger = logging.getLogger(__name__)


class TempoAPIError(Exception):
    """Custom exception for Tempo API errors with detailed response information."""

    def __init__(self, status_code: int, response_text: str, url: str):
        self.status_code = status_code
        self.response_text = response_text
        self.url = url

        # Try to extract error message from JSON response
        try:
            import json

            error_data = json.loads(response_text)
            # Tempo may return errors in different formats
            error_message = (
                error_data.get("error")
                or error_data.get("message")
                or error_data.get("errorType")
                or response_text
            )
        except (json.JSONDecodeError, TypeError):
            error_message = response_text

        super().__init__(f"Tempo API error {status_code}: {error_message}")


class GrafanaTempoAPI:
    """Python wrapper for Grafana Tempo REST API.

    This class provides a clean interface to all Tempo API endpoints.
    """

    def __init__(self, config: GrafanaTempoConfig):
        """Initialize the Tempo API wrapper.

        Args:
            config: GrafanaTempoConfig instance with connection details
        """
        self.config = config
        self.base_url = get_base_url(config)
        self.headers = build_headers(config.api_key, config.headers)

    def _make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        path_params: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        retries: int = 3,
    ) -> Dict[str, Any]:
        """Make HTTP request to Tempo API with retry logic.

        Args:
            endpoint: API endpoint path (e.g., "/api/echo")
            params: Query parameters
            path_params: Parameters to substitute in the endpoint path
            timeout: Request timeout in seconds
            retries: Number of retry attempts

        Returns:
            JSON response from the API

        Raises:
            Exception: If the request fails after all retries
        """
        # Format endpoint with path parameters
        if path_params:
            for key, value in path_params.items():
                endpoint = endpoint.replace(f"{{{key}}}", quote(str(value), safe=""))

        url = f"{self.base_url}{endpoint}"

        @backoff.on_exception(
            backoff.expo,
            requests.exceptions.RequestException,
            max_tries=retries,
            giveup=lambda e: isinstance(e, requests.exceptions.HTTPError)
            and getattr(e, "response", None) is not None
            and e.response.status_code < 500,
        )
        def make_request():
            # GET request with query parameters
            response = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()

        try:
            return make_request()
        except requests.exceptions.HTTPError as e:
            # Extract detailed error message from response
            response = e.response
            if response is not None:
                logger.error(
                    f"HTTP error {response.status_code} for {url}: {response.text}"
                )
                raise TempoAPIError(
                    status_code=response.status_code,
                    response_text=response.text,
                    url=url,
                )
            else:
                logger.error(f"Request failed for {url}: {e}")
                raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for {url}: {e}")
            raise

    def query_echo_endpoint(self) -> bool:
        """Query the echo endpoint to check Tempo status.

        API Endpoint: GET /api/echo
        HTTP Method: GET

        Returns:
            bool: True if endpoint returns 200 status code, False otherwise
        """
        url = f"{self.base_url}/api/echo"

        try:
            response = requests.get(
                url,
                headers=self.headers,
                timeout=30,
            )

            # Just check status code, don't try to parse JSON
            return response.status_code == 200

        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for {url}: {e}")
            return False

    def query_trace_by_id_v2(
        self,
        trace_id: str,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Query a trace by its ID.

        API Endpoint: GET /api/v2/traces/{trace_id}
        HTTP Method: GET

        Args:
            trace_id: The trace ID to retrieve
            start: Optional start time in Unix epoch seconds
            end: Optional end time in Unix epoch seconds

        Returns:
            dict: OpenTelemetry format trace data
        """
        params = {}
        if start is not None:
            params["start"] = str(start)
        if end is not None:
            params["end"] = str(end)

        return self._make_request(
            "/api/v2/traces/{trace_id}",
            params=params,
            path_params={"trace_id": trace_id},
        )

    def _search_traces_common(
        self,
        search_params: Dict[str, Any],
        limit: Optional[int] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        spss: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Common search implementation for both tag and TraceQL searches.

        Args:
            search_params: The search-specific parameters (tags or q)
            limit: Optional max number of traces to return
            start: Optional start time in Unix epoch seconds
            end: Optional end time in Unix epoch seconds
            spss: Optional spans per span set

        Returns:
            dict: Search results with trace metadata
        """
        params = search_params.copy()

        if limit is not None:
            params["limit"] = str(limit)
        if start is not None:
            params["start"] = str(start)
        if end is not None:
            params["end"] = str(end)
        if spss is not None:
            params["spss"] = str(spss)

        return self._make_request("/api/search", params=params)

    def search_traces_by_tags(
        self,
        tags: str,
        min_duration: Optional[str] = None,
        max_duration: Optional[str] = None,
        limit: Optional[int] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        spss: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search for traces using tag-based search.

        API Endpoint: GET /api/search
        HTTP Method: GET

        Args:
            tags: logfmt-encoded span/process attributes (required)
            min_duration: Optional minimum trace duration (e.g., "5s")
            max_duration: Optional maximum trace duration
            limit: Optional max number of traces to return
            start: Optional start time in Unix epoch seconds
            end: Optional end time in Unix epoch seconds
            spss: Optional spans per span set

        Returns:
            dict: Search results with trace metadata
        """
        search_params = {"tags": tags}

        # minDuration and maxDuration are only supported with tag-based search
        if min_duration is not None:
            search_params["minDuration"] = min_duration
        if max_duration is not None:
            search_params["maxDuration"] = max_duration

        return self._search_traces_common(
            search_params=search_params,
            limit=limit,
            start=start,
            end=end,
            spss=spss,
        )

    def search_traces_by_query(
        self,
        q: str,
        limit: Optional[int] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        spss: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search for traces using TraceQL query.

        API Endpoint: GET /api/search
        HTTP Method: GET

        Note: minDuration and maxDuration are not supported with TraceQL queries.
        Use the TraceQL query syntax to filter by duration instead.

        Args:
            q: TraceQL query (required)
            limit: Optional max number of traces to return
            start: Optional start time in Unix epoch seconds
            end: Optional end time in Unix epoch seconds
            spss: Optional spans per span set

        Returns:
            dict: Search results with trace metadata
        """
        return self._search_traces_common(
            search_params={"q": q},
            limit=limit,
            start=start,
            end=end,
            spss=spss,
        )

    def search_tag_names_v2(
        self,
        scope: Optional[str] = None,
        q: Optional[str] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        limit: Optional[int] = None,
        max_stale_values: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search for available tag names.

        API Endpoint: GET /api/v2/search/tags
        HTTP Method: GET

        Args:
            scope: Optional scope filter ("resource", "span", or "intrinsic")
            q: Optional TraceQL query to filter tags
            start: Optional start time in Unix epoch seconds
            end: Optional end time in Unix epoch seconds
            limit: Optional max number of tag names
            max_stale_values: Optional max stale values parameter

        Returns:
            dict: Available tag names organized by scope
        """
        params = {}
        if scope is not None:
            params["scope"] = scope
        if q is not None:
            params["q"] = q
        if start is not None:
            params["start"] = str(start)
        if end is not None:
            params["end"] = str(end)
        if limit is not None:
            params["limit"] = str(limit)
        if max_stale_values is not None:
            params["maxStaleValues"] = str(max_stale_values)

        return self._make_request("/api/v2/search/tags", params=params)

    def search_tag_values_v2(
        self,
        tag: str,
        q: Optional[str] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        limit: Optional[int] = None,
        max_stale_values: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Search for values of a specific tag with optional TraceQL filtering.

        API Endpoint: GET /api/v2/search/tag/{tag}/values
        HTTP Method: GET

        Args:
            tag: The tag name to get values for (required)
            q: Optional TraceQL query to filter tag values (e.g., '{resource.cluster="us-east-1"}')
            start: Optional start time in Unix epoch seconds
            end: Optional end time in Unix epoch seconds
            limit: Optional max number of values
            max_stale_values: Optional max stale values parameter

        Returns:
            dict: List of discovered values for the tag
        """
        params = {}
        if q is not None:
            params["q"] = q
        if start is not None:
            params["start"] = str(start)
        if end is not None:
            params["end"] = str(end)
        if limit is not None:
            params["limit"] = str(limit)
        if max_stale_values is not None:
            params["maxStaleValues"] = str(max_stale_values)

        return self._make_request(
            "/api/v2/search/tag/{tag}/values",
            params=params,
            path_params={"tag": tag},
        )

    def query_metrics_instant(
        self,
        q: str,
        start: Optional[Union[int, str]] = None,
        end: Optional[Union[int, str]] = None,
        since: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query TraceQL metrics for an instant value.

        Computes a single value across the entire time range.

        API Endpoint: GET /api/metrics/query
        HTTP Method: GET

        Args:
            q: TraceQL metrics query (required)
            start: Optional start time (Unix seconds/nanoseconds/RFC3339)
            end: Optional end time (Unix seconds/nanoseconds/RFC3339)
            since: Optional duration string (e.g., "1h")

        Returns:
            dict: Single computed metric value
        """
        params = {"q": q}
        if start is not None:
            params["start"] = str(start)
        if end is not None:
            params["end"] = str(end)
        if since is not None:
            params["since"] = since

        return self._make_request("/api/metrics/query", params=params)

    def query_metrics_range(
        self,
        q: str,
        step: Optional[str] = None,
        start: Optional[Union[int, str]] = None,
        end: Optional[Union[int, str]] = None,
        since: Optional[str] = None,
        exemplars: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Query TraceQL metrics for a time series range.

        Returns metrics computed at regular intervals over the time range.

        API Endpoint: GET /api/metrics/query_range
        HTTP Method: GET

        Args:
            q: TraceQL metrics query (required)
            step: Optional time series granularity (e.g., "1m", "5m")
            start: Optional start time (Unix seconds/nanoseconds/RFC3339)
            end: Optional end time (Unix seconds/nanoseconds/RFC3339)
            since: Optional duration string (e.g., "3h")
            exemplars: Optional maximum number of exemplars to return

        Returns:
            dict: Time series of metric values
        """
        params = {"q": q}
        if step is not None:
            params["step"] = step
        if start is not None:
            params["start"] = str(start)
        if end is not None:
            params["end"] = str(end)
        if since is not None:
            params["since"] = since
        if exemplars is not None:
            params["exemplars"] = str(exemplars)

        return self._make_request("/api/metrics/query_range", params=params)

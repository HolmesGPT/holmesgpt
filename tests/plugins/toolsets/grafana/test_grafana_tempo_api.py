"""Tests for the Grafana Tempo API wrapper."""

import os
from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import HTTPError, RequestException  # type: ignore

from holmes.plugins.toolsets.grafana.common import GrafanaTempoConfig
from holmes.plugins.toolsets.grafana.grafana_tempo_api import GrafanaTempoAPI

# Test constants
TEST_SERVICE_NAME = "checkout-service"


class TestGrafanaTempoAPI:
    """Test the GrafanaTempoAPI wrapper class."""

    @pytest.fixture
    def config(self):
        """Create a test configuration."""
        return GrafanaTempoConfig(
            url="https://grafana.example.com",
            api_key="test-api-key",
            grafana_datasource_uid="tempo-uid",
        )

    @pytest.fixture
    def api(self, config):
        """Create API instance."""
        return GrafanaTempoAPI(config)

    def test_initialization(self, config):
        """Test API initialization with config."""
        api = GrafanaTempoAPI(config)
        assert api.config == config
        assert (
            api.base_url
            == "https://grafana.example.com/api/datasources/proxy/uid/tempo-uid"
        )
        assert "Authorization" in api.headers
        assert api.headers["Authorization"] == "Bearer test-api-key"

    @patch("requests.get")
    def test_query_echo_endpoint(self, mock_get, api):
        """Test query_echo_endpoint method."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = api.query_echo_endpoint()

        assert result is True
        mock_get.assert_called_once_with(
            f"{api.base_url}/api/echo",
            headers=api.headers,
            timeout=30,
        )

    @patch("requests.get")
    def test_query_echo_endpoint_non_200(self, mock_get, api):
        """Test query_echo_endpoint with non-200 status code."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = api.query_echo_endpoint()

        assert result is False

    @patch("requests.get")
    def test_query_trace_by_id_v2(self, mock_get, api):
        """Test query_trace_by_id_v2 method."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"traceID": "123abc"}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = api.query_trace_by_id_v2("123abc", start=1000, end=2000)

        assert result == {"traceID": "123abc"}
        mock_get.assert_called_once_with(
            f"{api.base_url}/api/v2/traces/123abc",
            headers=api.headers,
            params={"start": "1000", "end": "2000"},
            timeout=30,
        )

    @patch("requests.get")
    def test_query_trace_by_id_v2_no_time_params(self, mock_get, api):
        """Test query_trace_by_id_v2 without time parameters."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"traceID": "123abc"}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = api.query_trace_by_id_v2("123abc")

        assert result == {"traceID": "123abc"}
        mock_get.assert_called_once_with(
            f"{api.base_url}/api/v2/traces/123abc",
            headers=api.headers,
            params={},
            timeout=30,
        )

    @patch("requests.get")
    def test_search_traces_by_tags(self, mock_get, api):
        """Test search_traces_by_tags method with all parameters."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"traces": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = api.search_traces_by_tags(
            tags=f"resource.service.name={TEST_SERVICE_NAME}",
            min_duration="5s",
            max_duration="30s",
            limit=100,
            start=1000,
            end=2000,
            spss=5,
        )

        assert result == {"traces": []}
        expected_params = {
            "tags": f"resource.service.name={TEST_SERVICE_NAME}",
            "minDuration": "5s",
            "maxDuration": "30s",
            "limit": "100",
            "start": "1000",
            "end": "2000",
            "spss": "5",
        }
        mock_get.assert_called_once_with(
            f"{api.base_url}/api/search",
            headers=api.headers,
            params=expected_params,
            timeout=30,
        )

    @patch("requests.get")
    def test_search_traces_by_query(self, mock_get, api):
        """Test search_traces_by_query method."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"traces": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = api.search_traces_by_query(
            q=f'{{resource.service.name="{TEST_SERVICE_NAME}"}}',
            limit=100,
            start=1000,
            end=2000,
            spss=5,
        )

        assert result == {"traces": []}
        expected_params = {
            "q": f'{{resource.service.name="{TEST_SERVICE_NAME}"}}',
            "limit": "100",
            "start": "1000",
            "end": "2000",
            "spss": "5",
        }
        mock_get.assert_called_once_with(
            f"{api.base_url}/api/search",
            headers=api.headers,
            params=expected_params,
            timeout=30,
        )

    @patch("requests.get")
    def test_search_tag_names_v2(self, mock_get, api):
        """Test search_tag_names_v2 method."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"scopes": {"resource": ["service.name"]}}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = api.search_tag_names_v2(
            scope="resource",
            q=f'{{resource.service.name="{TEST_SERVICE_NAME}"}}',
            start=1000,
            end=2000,
            limit=50,
            max_stale_values=100,
        )

        assert result == {"scopes": {"resource": ["service.name"]}}
        expected_params = {
            "scope": "resource",
            "q": f'{{resource.service.name="{TEST_SERVICE_NAME}"}}',
            "start": "1000",
            "end": "2000",
            "limit": "50",
            "maxStaleValues": "100",
        }
        mock_get.assert_called_once_with(
            f"{api.base_url}/api/v2/search/tags",
            headers=api.headers,
            params=expected_params,
            timeout=30,
        )

    @patch("requests.get")
    def test_search_tag_values_v2(self, mock_get, api):
        """Test search_tag_values_v2 method."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tagValues": [TEST_SERVICE_NAME, "web", "db"]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = api.search_tag_values_v2(
            tag="resource.service.name",
            q='{resource.cluster="us-east-1"}',
            start=1000,
            end=2000,
            limit=50,
            max_stale_values=100,
        )

        assert result == {"tagValues": [TEST_SERVICE_NAME, "web", "db"]}
        expected_params = {
            "q": '{resource.cluster="us-east-1"}',
            "start": "1000",
            "end": "2000",
            "limit": "50",
            "maxStaleValues": "100",
        }
        mock_get.assert_called_once_with(
            f"{api.base_url}/api/v2/search/tag/resource.service.name/values",
            headers=api.headers,
            params=expected_params,
            timeout=30,
        )

    @patch("requests.get")
    def test_query_metrics_instant(self, mock_get, api):
        """Test query_metrics_instant method."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"metrics": {"value": 123}}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = api.query_metrics_instant(
            q="{status=error} | count_over_time() by (resource.service.name)",
            start=1000,
            end=2000,
            since="1h",
        )

        assert result == {"metrics": {"value": 123}}
        expected_params = {
            "q": "{status=error} | count_over_time() by (resource.service.name)",
            "start": "1000",
            "end": "2000",
            "since": "1h",
        }
        mock_get.assert_called_once_with(
            f"{api.base_url}/api/metrics/query",
            headers=api.headers,
            params=expected_params,
            timeout=30,
        )

    @patch("requests.get")
    def test_query_metrics_range(self, mock_get, api):
        """Test query_metrics_range method."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "series": [{"timestamps": [1000, 2000], "values": [10, 20]}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = api.query_metrics_range(
            q='{resource.service.name="myservice"} | rate()',
            step="1m",
            start=1000,
            end=2000,
            since="3h",
            exemplars=100,
        )

        assert result == {"series": [{"timestamps": [1000, 2000], "values": [10, 20]}]}
        expected_params = {
            "q": '{resource.service.name="myservice"} | rate()',
            "step": "1m",
            "start": "1000",
            "end": "2000",
            "since": "3h",
            "exemplars": "100",
        }
        mock_get.assert_called_once_with(
            f"{api.base_url}/api/metrics/query_range",
            headers=api.headers,
            params=expected_params,
            timeout=30,
        )

    @patch("requests.get")
    def test_query_metrics_instant_required_only(self, mock_get, api):
        """Test query_metrics_instant with only required parameter."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"metrics": {"value": 123}}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = api.query_metrics_instant(
            q=f'{{resource.service.name="{TEST_SERVICE_NAME}"}} | count()'
        )

        assert result == {"metrics": {"value": 123}}
        expected_params = {
            "q": f'{{resource.service.name="{TEST_SERVICE_NAME}"}} | count()'
        }
        mock_get.assert_called_once_with(
            f"{api.base_url}/api/metrics/query",
            headers=api.headers,
            params=expected_params,
            timeout=30,
        )

    @patch("requests.get")
    def test_error_handling(self, mock_get, api):
        """Test error handling for failed requests."""
        mock_get.side_effect = RequestException("Connection error")

        # query_echo_endpoint now returns False on error
        result = api.query_echo_endpoint()
        assert result is False

    @patch("requests.get")
    def test_http_error_handling(self, mock_get, api):
        """Test handling of HTTP errors."""
        mock_response = MagicMock()
        # Create an HTTPError with a response attribute
        http_error = HTTPError("404 Not Found")
        http_error.response = MagicMock()
        http_error.response.status_code = 404
        http_error.response.text = "Not found"
        mock_response.raise_for_status.side_effect = http_error
        mock_get.return_value = mock_response

        from holmes.plugins.toolsets.grafana.grafana_tempo_api import TempoAPIError

        with pytest.raises(TempoAPIError) as exc_info:
            api.query_trace_by_id_v2("nonexistent")

        assert "404" in str(exc_info.value)
        assert exc_info.value.status_code == 404

    @patch("requests.get")
    def test_special_characters_in_path_params(self, mock_get, api):
        """Test handling of special characters in path parameters."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"tagValues": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # Tag with special characters that need encoding
        api.search_tag_values_v2(tag="service/name:test")

        # Check that the URL was properly encoded
        expected_url = f"{api.base_url}/api/v2/search/tag/service%2Fname%3Atest/values"
        mock_get.assert_called_once_with(
            expected_url,
            headers=api.headers,
            params={},
            timeout=30,
        )


@pytest.mark.skipif(
    not os.getenv("GRAFANA_URL"), reason="GRAFANA_URL environment variable not set"
)
class TestGrafanaTempoAPIIntegration:
    """Integration tests for Grafana Tempo API (requires real Tempo instance)."""

    @pytest.fixture
    def config(self):
        """Create configuration for integration tests from environment variables."""
        return GrafanaTempoConfig(
            url=os.getenv("GRAFANA_URL", ""),
            api_key=os.getenv("GRAFANA_API_KEY", ""),
            grafana_datasource_uid=os.getenv("GRAFANA_TEMPO_DATASOURCE_UID", ""),
        )

    def test_echo_endpoint_integration(self, config):
        """Test echo endpoint against real Tempo."""
        api = GrafanaTempoAPI(config)
        result = api.query_echo_endpoint()
        assert result is True

    def test_search_traces_by_tags_integration(self, config):
        """Test tag-based trace search against real Tempo."""
        api = GrafanaTempoAPI(config)
        result = api.search_traces_by_tags(
            tags=f"resource.service.name={TEST_SERVICE_NAME}", limit=10
        )
        assert "traces" in result

    def test_search_traces_by_query_integration(self, config):
        """Test TraceQL trace search against real Tempo."""
        api = GrafanaTempoAPI(config)
        result = api.search_traces_by_query(
            q=f'{{resource.service.name="{TEST_SERVICE_NAME}"}}', limit=10
        )
        assert "traces" in result

    def test_search_tag_names_integration(self, config):
        """Test tag name search against real Tempo."""
        api = GrafanaTempoAPI(config)
        result = api.search_tag_names_v2()
        assert "scopes" in result or "tagNames" in result

    def test_query_trace_by_id_v2_integration(self, config):
        """Test trace retrieval by ID against real Tempo."""
        api = GrafanaTempoAPI(config)
        # First, find a trace
        search_result = api.search_traces_by_tags(
            tags=f"resource.service.name={TEST_SERVICE_NAME}", limit=1
        )
        if search_result.get("traces"):
            trace_id = search_result["traces"][0]["traceID"]
            result = api.query_trace_by_id_v2(trace_id)
            assert "batches" in result or "resourceSpans" in result

    def test_search_tag_values_v2_integration(self, config):
        """Test tag value search against real Tempo."""
        api = GrafanaTempoAPI(config)
        result = api.search_tag_values_v2(tag="resource.service.name")
        assert "tagValues" in result

    def test_query_metrics_instant_integration(self, config):
        """Test instant metrics query against real Tempo."""
        api = GrafanaTempoAPI(config)
        result = api.query_metrics_instant(
            q='{span.http.status_code="error"} | count_over_time()'
        )
        assert "metrics" in result or "data" in result

    def test_query_metrics_range_integration(self, config):
        """Test range metrics query against real Tempo."""
        api = GrafanaTempoAPI(config)
        result = api.query_metrics_range(
            q=f'{{resource.service.name="{TEST_SERVICE_NAME}"}} | rate()',
            step="5m",
            since="1h",
        )
        assert "series" in result or "data" in result

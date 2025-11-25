import pytest
from unittest.mock import Mock, patch
import requests  # type: ignore

from holmes.plugins.toolsets.grafana.common import GrafanaConfig
from holmes.plugins.toolsets.grafana.grafana_api import grafana_health_check


@pytest.fixture
def mock_requests_get():
    """Fixture to mock requests.get"""
    with patch("holmes.plugins.toolsets.grafana.grafana_api.requests.get") as mock:
        yield mock


class TestGrafanaHealthCheck:
    """Test cases for grafana_health_check function"""

    def test_first_url_succeeds(self, mock_requests_get):
        """Test that function returns True when first URL succeeds"""
        config = GrafanaConfig(
            url="http://grafana:3000",
            grafana_datasource_uid="loki-uid",
        )

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_requests_get.return_value = mock_response

        success, error = grafana_health_check(config)

        assert success is True
        assert error == ""
        assert mock_requests_get.call_count == 1

    def test_second_url_succeeds_after_first_fails(self, mock_requests_get):
        """Test that function tries second URL when first fails"""
        config = GrafanaConfig(
            url="http://grafana:3000",
        )

        mock_requests_get.side_effect = [
            requests.exceptions.ConnectionError("Connection failed"),
            requests.exceptions.ConnectionError("Connection failed"),
            Mock(raise_for_status=Mock()),
        ]

        success, error = grafana_health_check(config)
        assert success is True
        assert error == ""
        assert mock_requests_get.call_count > 2

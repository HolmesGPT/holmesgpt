from typing import Dict, Optional

from holmes.plugins.toolsets.grafana.toolset_grafana import GrafanaDashboardConfig
from holmes.utils.pydantic_utils import build_config_example
import pytest

from holmes.plugins.toolsets.grafana.common import GrafanaConfig, GrafanaTempoConfig, build_headers


@pytest.mark.parametrize(
    "api_key, additional_headers, expected_headers",
    [
        (
            None,
            None,
            {"Accept": "application/json", "Content-Type": "application/json"},
        ),
        (
            "test_api_key_123",
            None,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer test_api_key_123",
            },
        ),
        (
            None,
            {"X-Request-ID": "req-abc"},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Request-ID": "req-abc",
            },
        ),
        (
            "test_api_key_456",
            {"X-Custom-Header": "custom-value"},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer test_api_key_456",
                "X-Custom-Header": "custom-value",
            },
        ),
        (
            None,
            {"Accept": "application/xml"},
            {"Accept": "application/xml", "Content-Type": "application/json"},
        ),
        (
            "test_api_key_789",
            {"Authorization": "Basic dXNlcjpwYXNz"},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Basic dXNlcjpwYXNz",
            },
        ),
        (
            "test_api_key_101",
            {},
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer test_api_key_101",
            },
        ),
        (None, {}, {"Accept": "application/json", "Content-Type": "application/json"}),
    ],
)
def test_build_headers(
    api_key: Optional[str],
    additional_headers: Optional[Dict[str, str]],
    expected_headers: Dict[str, str],
):
    """Tests the build_headers function with various inputs."""
    result_headers = build_headers(api_key, additional_headers)
    assert result_headers == expected_headers


def test_build_config_example_grafana_config():
    example = build_config_example(GrafanaConfig)

    assert example["url"] == "YOUR GRAFANA URL"
    assert example["api_key"] == "YOUR API KEY"
    assert example["headers"] == {"Authorization": "Bearer YOUR_API_KEY"}
    assert example["grafana_datasource_uid"] == "loki"
    assert example["external_url"] == "your_external_url"
    assert example["verify_ssl"] is True


def test_build_config_example_grafana_dashboard_config():
    example = build_config_example(GrafanaDashboardConfig)

    # Dashboard config is currently the base GrafanaConfig.
    assert example["url"] == "YOUR GRAFANA URL"
    assert example["api_key"] == "YOUR API KEY"
    assert example["headers"] == {"Authorization": "Bearer YOUR_API_KEY"}
    assert example["grafana_datasource_uid"] == "loki"
    assert example["external_url"] == "your_external_url"
    assert example["verify_ssl"] is True


def test_build_config_example_grafana_tempo_config():
    example = build_config_example(GrafanaTempoConfig)

    assert example["url"] == "YOUR GRAFANA URL"
    assert example["grafana_datasource_uid"] == "loki"

    assert example["labels"]["pod"] == "k8s.pod.name"
    assert example["labels"]["namespace"] == "k8s.namespace.name"
    assert example["labels"]["deployment"] == "k8s.deployment.name"
    assert example["labels"]["node"] == "k8s.node.name"
    assert example["labels"]["service"] == "service.name"


import pytest

from holmes.utils.pydantic_utils import build_config_example
from holmes.plugins.toolsets.prometheus.prometheus import PrometheusConfig


def test_build_config_example_prometheus_config():
    example = build_config_example(PrometheusConfig)

    assert (
        example["prometheus_url"]
        == "http://prometheus-server.monitoring.svc.cluster.local:9090"
    )

    assert example["discover_metrics_from_last_hours"] == 1
    assert example["query_timeout_seconds_default"] == 20
    assert example["query_timeout_seconds_hard_max"] == 180
    assert example["metadata_timeout_seconds_default"] == 20
    assert example["metadata_timeout_seconds_hard_max"] == 60

    assert example["tool_calls_return_data"] is True
    assert example["headers"] == {"Authorization": "Basic <base64_encoded_credentials>"}
    assert example["rules_cache_duration_seconds"] == 1800
    assert example["additional_labels"] == {"cluster": "prod", "namespace": "default"}
    assert example["verify_ssl"] is True
    assert example["query_response_size_limit_pct"] == 10


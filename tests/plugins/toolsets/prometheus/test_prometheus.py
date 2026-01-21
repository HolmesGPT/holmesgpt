from prometrix.connect.custom_connect import PrometheusConfig
from holmes.utils.pydantic_utils import build_config_example
import pytest

from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.prometheus.prometheus import PrometheusToolset
from tests.plugins.toolsets.grafana.conftest import check_service_running

skip_reason = check_service_running("Grafana", 9000)
if skip_reason:
    pytestmark = pytest.mark.skip(reason=skip_reason)


# Use docker compose with https://github.com/grafana/mimir/blob/main/docs/sources/mimir/get-started/play-with-grafana-mimir/index.md
def test_mimir_datasource_toolset_health_check():
    toolset = PrometheusToolset()
    toolset.config = {
        "prometheus_url": "http://localhost:9000/api/datasources/proxy/uid/PAE45454D0EDB9216",
    }
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED


def test_mimir_datasource_toolset_bad_uid_health_check():
    toolset = PrometheusToolset()
    toolset.config = {
        "prometheus_url": "http://localhost:9000/api/datasources/proxy/uid/PAE45454D0EDB9216111",
    }
    toolset.check_prerequisites()

    assert (
        "Failed to connect to Prometheus at http://localhost:9000/api/datasources/proxy/uid/PAE45454D0EDB9216111/api/v1/query?query=up: HTTP 404"
        in toolset.error
    )
    assert toolset.status == ToolsetStatusEnum.FAILED


def test_mimir_direct_toolset_health_check():
    toolset = PrometheusToolset()
    toolset.config = {
        "prometheus_url": "http://localhost:9009/prometheus",
        "headers": {"X-Scope-OrgID": "DEMO"},
    }
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED


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


import pytest

from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.grafana.loki.toolset_grafana_loki import (
    GrafanaLokiToolset,
)
from holmes.plugins.toolsets.grafana.toolset_grafana import GrafanaToolset
from tests.plugins.toolsets.grafana.conftest import check_service_running

# Skip all tests in this module if Grafana and loki are not running. use loki/docker-compose.yaml
skip_reason = check_service_running("Grafana", 3000)
if skip_reason:
    pytestmark = pytest.mark.skip(reason=skip_reason)


def test_grafana_toolset_direct_health_check():
    toolset = GrafanaToolset()
    toolset.config = {"api_url": "http://localhost:3000/"}
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED


def test_grafana_toolset_error_health_check():
    toolset = GrafanaToolset()
    toolset.config = {"api_url": "http://localhost:2000/"}
    toolset.check_prerequisites()

    assert (
        "Failed to connect to Grafana HTTPConnectionPool(host='localhost', port=2000): Max retries exceeded with url: /api/dashboards/tags"
        in toolset.error
    )
    assert toolset.status == ToolsetStatusEnum.FAILED


def test_loki_toolset_direct_health_check():
    toolset = GrafanaLokiToolset()
    toolset.config = {"api_url": "http://localhost:3100/"}
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED


def test_loki_datasource_toolset_health_check():
    toolset = GrafanaLokiToolset()
    toolset.config = {
        "api_url": "http://localhost:3000/",
        "grafana_datasource_uid": "loki-test-uid",
    }
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED


def test_loki_datasource_toolset_error_health_check():
    toolset = GrafanaLokiToolset()
    toolset.config = {
        "api_url": "http://localhost:3000/",
        "grafana_datasource_uid": "wrong-uid",
    }
    toolset.check_prerequisites()

    assert (
        "Unable to connect to Loki.\nFailed to query Loki logs: 404 Client Error: Not Found for url: http://localhost:3000//api/datasources/proxy/uid/wrong-uid/loki/api/v1/query_range"
        in toolset.error
    )
    assert toolset.status == ToolsetStatusEnum.FAILED

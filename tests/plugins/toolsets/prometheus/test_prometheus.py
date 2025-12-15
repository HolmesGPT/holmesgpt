from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.prometheus.prometheus import PrometheusToolset
from tests.plugins.toolsets.grafana.conftest import check_grafana_running
import pytest

skip_reason = check_grafana_running(port=9000)
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


def test_mimir_direct_toolset_health_check():
    toolset = PrometheusToolset()
    toolset.config = {
        "prometheus_url": "http://localhost:9009/prometheus",
        "headers": {"X-Scope-OrgID": "DEMO"},
    }
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED

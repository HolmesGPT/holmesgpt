import pytest

from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.grafana.loki.toolset_grafana_loki import (
    GrafanaLokiToolset,
)
from tests.plugins.toolsets.grafana.conftest import check_grafana_running

# Skip all tests in this module if Loki is not running
skip_reason = check_grafana_running()
if skip_reason:
    pytestmark = pytest.mark.skip(reason=skip_reason)


def test_loki_toolset_direct_health_check():
    toolset = GrafanaLokiToolset()
    toolset.config = {"url": "http://localhost:3100/"}
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED


def test_loki_datasource_toolset_health_check():
    toolset = GrafanaLokiToolset()
    toolset.config = {
        "url": "http://localhost:3000/",
        "grafana_datasource_uid": "loki-test-uid",
    }
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED

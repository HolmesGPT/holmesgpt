import pytest
import responses

from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.grafana.loki.toolset_grafana_loki import (
    GrafanaLokiToolset,
)
from holmes.plugins.toolsets.grafana.loki_api import execute_loki_query
from holmes.plugins.toolsets.grafana.toolset_grafana import GrafanaToolset
from tests.plugins.toolsets.grafana.conftest import check_service_running

# Skip integration tests that require Grafana/Loki running on localhost:3000.
# Use loki/docker-compose.yaml to bring them up.
_grafana_skip_reason = check_service_running("Grafana", 3000)
needs_grafana = pytest.mark.skipif(
    _grafana_skip_reason is not None,
    reason=_grafana_skip_reason or "",
)


@needs_grafana
def test_grafana_toolset_direct_health_check():
    toolset = GrafanaToolset()
    toolset.config = {"api_url": "http://localhost:3000/"}
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED


@needs_grafana
def test_grafana_toolset_error_health_check():
    toolset = GrafanaToolset()
    toolset.config = {"api_url": "http://localhost:2000/"}
    toolset.check_prerequisites()

    assert (
        "Failed to connect to Grafana HTTPConnectionPool(host='localhost', port=2000): Max retries exceeded with url: /api/dashboards/tags"
        in toolset.error
    )
    assert toolset.status == ToolsetStatusEnum.FAILED


@needs_grafana
def test_loki_toolset_direct_health_check():
    toolset = GrafanaLokiToolset()
    toolset.config = {"api_url": "http://localhost:3100/"}
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED


@needs_grafana
def test_loki_datasource_toolset_health_check():
    toolset = GrafanaLokiToolset()
    toolset.config = {
        "api_url": "http://localhost:3000/",
        "grafana_datasource_uid": "loki-test-uid",
    }
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED


@needs_grafana
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


def test_execute_loki_query_includes_raw_body_on_malformed_json():
    """When Loki returns malformed JSON, the raised exception should include
    both the JSON parser error and the raw response body so an operator (and
    the LLM) can see what actually came back over the wire."""
    base_url = "http://loki.example.com"
    raw_body = '{"data": {"result": [oops not json'

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            f"{base_url}/loki/api/v1/query_range",
            body=raw_body,
            status=200,
            content_type="application/json",
        )

        with pytest.raises(Exception) as exc_info:
            execute_loki_query(
                base_url=base_url,
                api_key=None,
                headers=None,
                query='{job="foo"}',
                start=0,
                end=1,
                limit=10,
            )

    message = str(exc_info.value)
    assert "Failed to process Loki response" in message
    assert raw_body in message
    assert "content-type=application/json" in message
    assert f"{len(raw_body)} chars" in message

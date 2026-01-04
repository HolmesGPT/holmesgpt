import json
import os

import pytest
import requests  # type: ignore
import responses

from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.grafana.toolset_grafana_tempo import (
    GrafanaTempoToolset,
)
from holmes.plugins.toolsets.grafana.trace_parser import process_trace
from tests.plugins.toolsets.grafana.conftest import check_service_running

# use docker compose setup from https://github.com/grafana/tempo/blob/main/example/docker-compose/local/readme.md to run local grafana and tempo.
skip_reason = check_service_running("Grafana", 3000)
if skip_reason:
    pytestmark = pytest.mark.skip(reason=skip_reason)


def test_process_trace_json():
    input_trace_data_file_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "fixtures",
            "test_tempo_api",
            "trace_data.input.json",
        )
    )
    expected_trace_data_file_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "fixtures",
            "test_tempo_api",
            "trace_data.expected.txt",
        )
    )

    labels = [
        "service.name",
        "service.version",
        "k8s.deployment.name",
        "k8s.node.name",
        "k8s.pod.name",
        "k8s.namespace.name",
    ]
    trace_data = json.loads(open(input_trace_data_file_path).read())
    expected_result = open(expected_trace_data_file_path).read()
    result = process_trace(trace_data, labels)
    print(result)
    assert result is not None
    assert result.strip() == expected_result.strip()


def test_tempo_toolset_direct_health_check():
    toolset = GrafanaTempoToolset()
    toolset.config = {"url": "http://localhost:3200/"}
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED


def test_tempo_datasource_toolset_health_check():
    toolset = GrafanaTempoToolset()
    toolset.config = {
        "url": "http://localhost:3000/",
        "grafana_datasource_uid": "tempo-streaming-enabled",
    }
    toolset.check_prerequisites()

    assert toolset.error is None
    assert toolset.status == ToolsetStatusEnum.ENABLED


def test_tempo_datasource_toolset_wrong_url_health_check():
    toolset = GrafanaTempoToolset()
    toolset.config = {
        "url": "http://localhost:2000/",
        "grafana_datasource_uid": "tempo-streaming-enabled",
    }
    toolset.check_prerequisites()

    assert (
        "Unable to connect to Tempo.\nHTTPConnectionPool(host='localhost', port=2000): Max retries exceeded with url: /api/datasources/proxy/uid/tempo-streaming-enabled/api/search"
        in toolset.error
    )
    assert toolset.status == ToolsetStatusEnum.FAILED


def test_tempo_datasource_toolset_health_check_exceptions():
    """Test that health check handles request exceptions properly with backoff retries."""
    toolset = GrafanaTempoToolset()
    toolset.config = {
        "url": "http://localhost:3000/",
        "grafana_datasource_uid": "tempo-streaming-enabled",
    }

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            'http://localhost:3000//api/datasources/proxy/uid/tempo-streaming-enabled/api/search?q={ .service.name = "test-endpoint" }&limit=1',
            body=requests.exceptions.ConnectionError("Connection refused"),
            status=400,
        )

        toolset.check_prerequisites()

        assert len(rsps.calls) == 3, "Expected 3 retries due to backoff"
        assert toolset.status == ToolsetStatusEnum.FAILED
        assert "Connection refused" in toolset.error

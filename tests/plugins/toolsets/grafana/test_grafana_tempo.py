import json
import os
from unittest.mock import patch

import pytest
import requests  # type: ignore

from holmes.plugins.toolsets.grafana.trace_parser import process_trace
from tests.plugins.toolsets.grafana.conftest import check_service_running

from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.grafana.toolset_grafana_tempo import (
    GrafanaTempoToolset,
)

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


def test_tempo_datasource_toolset_health_check_exceptions():
    """Test that health check handles request exceptions properly with backoff retries."""
    toolset = GrafanaTempoToolset()
    toolset.config = {
        "url": "http://localhost:3000/",
        "grafana_datasource_uid": "tempo-streaming-enabled",
    }

    call_count = 0

    def mock_get_raises(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise requests.exceptions.ConnectionError("Connection refused")

    with patch("requests.get", side_effect=mock_get_raises):
        toolset.check_prerequisites()

    assert call_count == 3, f"Expected 3 retries due to backoff, got {call_count}"
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert "Connection refused" in toolset.error

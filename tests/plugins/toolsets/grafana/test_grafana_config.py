"""Tests for configurable timeout and retries in GrafanaConfig and subclasses."""

from unittest.mock import patch

import pytest
import requests
import responses
from pydantic import ValidationError
from responses import matchers

from holmes.plugins.toolsets.grafana.common import GrafanaConfig, GrafanaTempoConfig
from holmes.plugins.toolsets.grafana.grafana_tempo_api import GrafanaTempoAPI
from holmes.plugins.toolsets.grafana.loki_api import execute_loki_query
from holmes.plugins.toolsets.grafana.toolset_grafana import GrafanaDashboardConfig


class TestGrafanaConfigTimeoutRetries:
    """Tests for timeout/retries on the base GrafanaConfig class."""

    def test_base_config_default_timeout_and_retries(self):
        config = GrafanaConfig(api_url="https://example.com")
        assert config.timeout_seconds == 30
        assert config.max_retries == 3

    def test_base_config_custom_timeout_and_retries(self):
        config = GrafanaConfig(
            api_url="https://example.com",
            timeout_seconds=60,
            max_retries=5,
        )
        assert config.timeout_seconds == 60
        assert config.max_retries == 5

    def test_tempo_config_inherits_timeout_and_retries(self):
        config = GrafanaTempoConfig(api_url="https://example.com", timeout_seconds=45)
        assert config.timeout_seconds == 45
        assert config.max_retries == 3

    def test_dashboard_config_inherits_timeout_and_retries(self):
        config = GrafanaDashboardConfig(api_url="https://example.com", max_retries=7)
        # Dashboard config overrides timeout_seconds default to 60s since rendering can be slow.
        assert config.timeout_seconds == 60
        assert config.max_retries == 7

    def test_invalid_timeout_rejected(self):
        with pytest.raises(ValidationError):
            GrafanaConfig(api_url="https://example.com", timeout_seconds=0)
        with pytest.raises(ValidationError):
            GrafanaConfig(api_url="https://example.com", timeout_seconds=-1)

    def test_invalid_max_retries_rejected(self):
        with pytest.raises(ValidationError):
            GrafanaConfig(api_url="https://example.com", max_retries=0)
        with pytest.raises(ValidationError):
            GrafanaConfig(api_url="https://example.com", max_retries=-1)


class TestTempoConfigurableTimeoutRetries:
    """Tests for timeout/retries propagation through GrafanaTempoAPI."""

    @patch("time.sleep", return_value=None)
    def test_custom_retry_count_changes_behavior(self, mock_sleep):
        config = GrafanaTempoConfig(
            api_url="http://localhost:3000",
            grafana_datasource_uid="tempo-uid",
            max_retries=5,
        )
        api = GrafanaTempoAPI(config)

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "http://localhost:3000/api/datasources/proxy/uid/tempo-uid/api/search",
                body=requests.exceptions.ConnectionError("Connection refused"),
            )

            with pytest.raises(requests.exceptions.ConnectionError):
                api.search_traces_by_query(q="{}")

            assert len(rsps.calls) == 5

    @patch("time.sleep", return_value=None)
    def test_default_retry_count_is_three(self, mock_sleep):
        config = GrafanaTempoConfig(
            api_url="http://localhost:3000",
            grafana_datasource_uid="tempo-uid",
        )
        api = GrafanaTempoAPI(config)

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "http://localhost:3000/api/datasources/proxy/uid/tempo-uid/api/search",
                body=requests.exceptions.ConnectionError("Connection refused"),
            )

            with pytest.raises(requests.exceptions.ConnectionError):
                api.search_traces_by_query(q="{}")

            assert len(rsps.calls) == 3

    def test_make_request_with_custom_config_returns_data(self):
        config = GrafanaTempoConfig(
            api_url="https://example.com",
            api_key="key",
            grafana_datasource_uid="uid",
            timeout_seconds=90,
            max_retries=5,
        )
        api = GrafanaTempoAPI(config)

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://example.com/api/datasources/proxy/uid/uid/api/search",
                json={"traces": [{"traceID": "abc123"}]},
                match=[matchers.request_kwargs_matcher({"timeout": 90})],
            )

            result = api.search_traces_by_query(q="{}")

            assert len(rsps.calls) == 1
            assert result == {"traces": [{"traceID": "abc123"}]}

    def test_echo_endpoint_with_custom_config(self):
        config = GrafanaTempoConfig(
            api_url="https://example.com",
            api_key="key",
            grafana_datasource_uid="uid",
            timeout_seconds=120,
        )
        api = GrafanaTempoAPI(config)

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://example.com/api/datasources/proxy/uid/uid/api/echo",
                status=200,
                match=[matchers.request_kwargs_matcher({"timeout": 120})],
            )

            result = api.query_echo_endpoint()

            assert result is True
            assert len(rsps.calls) == 1


class TestLokiConfigurableTimeoutRetries:
    """Tests for timeout/retries propagation through execute_loki_query."""

    @patch("time.sleep", return_value=None)
    def test_loki_custom_retry_count_changes_behavior(self, mock_sleep):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "http://localhost:3100/loki/api/v1/query_range",
                body=requests.exceptions.ConnectionError("Connection refused"),
            )

            with pytest.raises(Exception, match="Failed to query Loki logs"):
                execute_loki_query(
                    base_url="http://localhost:3100",
                    api_key=None,
                    headers=None,
                    query='{job="test"}',
                    start=0,
                    end=1000,
                    limit=10,
                    max_retries=5,
                )

            assert len(rsps.calls) == 5

    @patch("time.sleep", return_value=None)
    def test_loki_default_retry_count_is_three(self, mock_sleep):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "http://localhost:3100/loki/api/v1/query_range",
                body=requests.exceptions.ConnectionError("Connection refused"),
            )

            with pytest.raises(Exception, match="Failed to query Loki logs"):
                execute_loki_query(
                    base_url="http://localhost:3100",
                    api_key=None,
                    headers=None,
                    query='{job="test"}',
                    start=0,
                    end=1000,
                    limit=10,
                )

            assert len(rsps.calls) == 3

    def test_loki_query_with_custom_config_returns_data(self):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "http://localhost:3100/loki/api/v1/query_range",
                json={
                    "data": {
                        "result": [{"stream": {}, "values": [["1234", "log line"]]}]
                    }
                },
                match=[matchers.request_kwargs_matcher({"timeout": 90})],
            )

            result = execute_loki_query(
                base_url="http://localhost:3100",
                api_key="key",
                headers=None,
                query='{job="test"}',
                start=0,
                end=1000,
                limit=10,
                timeout=90,
                max_retries=5,
            )

            assert len(rsps.calls) == 1
            assert len(result) == 1
            assert result[0]["log"] == "log line"

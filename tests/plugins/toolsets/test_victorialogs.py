"""Tests for the VictoriaLogs toolset."""

import json
import os
from unittest.mock import MagicMock

import pytest
import responses

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.victorialogs.victorialogs import (
    VictoriaLogsConfig,
    VictoriaLogsToolset,
)


API_URL = "http://localhost:9428"


@pytest.fixture
def toolset():
    ts = VictoriaLogsToolset()
    ts.config = VictoriaLogsConfig(api_url=API_URL)
    return ts


def _tool(toolset, name):
    return next(t for t in toolset.tools if t.name == name)


class TestVictoriaLogsConfig:
    def test_minimal_config(self):
        config = VictoriaLogsConfig(api_url=API_URL)
        assert config.api_url == API_URL
        assert config.username is None
        assert config.password is None
        assert config.bearer_token is None
        assert config.verify_ssl is True
        assert config.timeout_seconds == 30

    def test_basic_auth_config(self):
        config = VictoriaLogsConfig(
            api_url=API_URL, username="user", password="pass"
        )
        assert config.username == "user"
        assert config.password == "pass"

    def test_bearer_token_config(self):
        config = VictoriaLogsConfig(api_url=API_URL, bearer_token="abc")
        assert config.bearer_token == "abc"

    def test_extra_headers(self):
        config = VictoriaLogsConfig(
            api_url=API_URL, headers={"AccountID": "0", "ProjectID": "0"}
        )
        assert config.headers == {"AccountID": "0", "ProjectID": "0"}

    def test_bearer_and_basic_auth_rejected(self):
        with pytest.raises(ValueError, match="not both"):
            VictoriaLogsConfig(
                api_url=API_URL,
                bearer_token="abc",
                username="user",
                password="pass",
            )

    def test_username_without_password_rejected(self):
        with pytest.raises(ValueError, match="password is required"):
            VictoriaLogsConfig(api_url=API_URL, username="user")

    def test_password_without_username_rejected(self):
        with pytest.raises(ValueError, match="username is required"):
            VictoriaLogsConfig(api_url=API_URL, password="pass")


class TestVictoriaLogsToolsetInit:
    def test_toolset_has_expected_tools(self, toolset):
        names = {t.name for t in toolset.tools}
        assert names == {
            "victorialogs_query",
            "victorialogs_streams",
            "victorialogs_field_names",
            "victorialogs_field_values",
            "victorialogs_hits",
        }

    def test_instructions_loaded(self, toolset):
        assert toolset.llm_instructions
        assert "LogsQL" in toolset.llm_instructions

    def test_prerequisites_missing_config(self, toolset):
        ok, msg = toolset.prerequisites_callable({})
        assert ok is False
        assert "missing" in msg.lower()

    def test_prerequisites_invalid_config(self, toolset):
        ok, msg = toolset.prerequisites_callable({"foo": "bar"})
        assert ok is False
        assert "Failed to validate" in msg


class TestHealthCheck:
    def test_health_check_success(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/health",
                body="OK",
                status=200,
            )
            ok, msg = toolset.prerequisites_callable({"api_url": API_URL})
            assert ok is True
            assert API_URL in msg

    def test_health_check_http_error(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/health",
                body="auth required",
                status=401,
            )
            ok, msg = toolset.prerequisites_callable({"api_url": API_URL})
            assert ok is False
            assert "401" in msg

    def test_health_check_connection_error(self, toolset):
        with responses.RequestsMock() as rsps:
            # No matching response means ConnectionError
            ok, msg = toolset.prerequisites_callable(
                {"api_url": "http://nonexistent.invalid:9428"}
            )
            assert ok is False
            assert "Failed to connect" in msg or "connect" in msg.lower()


class TestVictoriaLogsQuery:
    def test_query_returns_logs(self, toolset):
        sample = (
            '{"_msg":"User completed purchase order 12345","_time":"2026-04-27T10:00:00Z","level":"info","service":"checkout"}\n'
            '{"_msg":"Payment gateway timeout","_time":"2026-04-27T10:01:00Z","level":"error","service":"checkout"}\n'
        )
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/query",
                body=sample,
                status=200,
                content_type="application/stream+json",
            )
            tool = _tool(toolset, "victorialogs_query")
            r = tool._invoke(
                {
                    "query": "service:checkout",
                    "start": "2026-04-27T09:00:00Z",
                    "end": "2026-04-27T11:00:00Z",
                    "limit": 50,
                },
                MagicMock(),
            )
            assert r.status == StructuredToolResultStatus.SUCCESS
            assert isinstance(r.data, list)
            assert len(r.data) == 2
            assert r.data[0]["_msg"] == "User completed purchase order 12345"
            assert r.data[0]["level"] == "info"

            # Ensure form data was POSTed correctly
            assert len(rsps.calls) == 1
            body = rsps.calls[0].request.body
            assert "query=service" in body
            assert "limit=50" in body

    def test_query_no_results(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/query",
                body="",
                status=200,
            )
            tool = _tool(toolset, "victorialogs_query")
            r = tool._invoke({"query": "nope"}, MagicMock())
            assert r.status == StructuredToolResultStatus.NO_DATA
            assert "No logs returned" in r.data

    def test_query_400_error_includes_request_and_response(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/query",
                body="cannot parse `query` arg: unsupported operation",
                status=400,
            )
            tool = _tool(toolset, "victorialogs_query")
            r = tool._invoke({"query": "{ broken"}, MagicMock())
            assert r.status == StructuredToolResultStatus.ERROR
            assert "HTTP 400" in r.error
            assert "{ broken" in r.error
            assert "cannot parse" in r.error

    def test_query_default_query(self, toolset):
        """If query is empty, default to '*' (defensive default)."""
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/query",
                body="",
                status=200,
            )
            tool = _tool(toolset, "victorialogs_query")
            r = tool._invoke({"query": ""}, MagicMock())
            # status NO_DATA because body is empty
            assert r.status == StructuredToolResultStatus.NO_DATA
            body = rsps.calls[0].request.body
            assert "query=%2A" in body  # '*' URL-encoded

    def test_query_unconfigured(self):
        ts = VictoriaLogsToolset()
        ts.config = None
        tool = _tool(ts, "victorialogs_query")
        r = tool._invoke({"query": "*"}, MagicMock())
        assert r.status == StructuredToolResultStatus.ERROR


class TestVictoriaLogsStreams:
    def test_streams_success(self, toolset):
        payload = {
            "values": [
                {"value": '{namespace="app-1",service="checkout"}', "hits": 3},
                {"value": '{namespace="app-1",service="inventory"}', "hits": 2},
            ]
        }
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/streams",
                json=payload,
                status=200,
            )
            tool = _tool(toolset, "victorialogs_streams")
            r = tool._invoke({"query": "*"}, MagicMock())
            assert r.status == StructuredToolResultStatus.SUCCESS
            assert len(r.data) == 2
            assert r.data[0]["hits"] == 3

    def test_streams_no_data(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/streams",
                json={"values": []},
                status=200,
            )
            tool = _tool(toolset, "victorialogs_streams")
            r = tool._invoke({"query": "*"}, MagicMock())
            assert r.status == StructuredToolResultStatus.NO_DATA


class TestVictoriaLogsFieldNames:
    def test_field_names(self, toolset):
        payload = {
            "values": [
                {"value": "_msg", "hits": 5},
                {"value": "level", "hits": 5},
                {"value": "service", "hits": 5},
            ]
        }
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/field_names",
                json=payload,
                status=200,
            )
            tool = _tool(toolset, "victorialogs_field_names")
            r = tool._invoke({"query": "*"}, MagicMock())
            assert r.status == StructuredToolResultStatus.SUCCESS
            assert {entry["value"] for entry in r.data} == {"_msg", "level", "service"}


class TestVictoriaLogsFieldValues:
    def test_field_values(self, toolset):
        payload = {
            "values": [
                {"value": "error", "hits": 2},
                {"value": "info", "hits": 2},
                {"value": "warn", "hits": 1},
            ]
        }
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/field_values",
                json=payload,
                status=200,
            )
            tool = _tool(toolset, "victorialogs_field_values")
            r = tool._invoke({"field": "level"}, MagicMock())
            assert r.status == StructuredToolResultStatus.SUCCESS
            assert {entry["value"] for entry in r.data} == {"error", "info", "warn"}

    def test_field_values_missing_field_param(self, toolset):
        tool = _tool(toolset, "victorialogs_field_values")
        r = tool._invoke({}, MagicMock())
        assert r.status == StructuredToolResultStatus.ERROR
        assert "field" in r.error.lower()


class TestVictoriaLogsHits:
    def test_hits(self, toolset):
        payload = {
            "hits": [
                {
                    "fields": {},
                    "timestamps": ["2026-04-27T09:00:00Z", "2026-04-27T10:00:00Z"],
                    "values": [0, 2],
                    "total": 2,
                }
            ]
        }
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/hits",
                json=payload,
                status=200,
            )
            tool = _tool(toolset, "victorialogs_hits")
            r = tool._invoke({"query": "level:error", "step": "1h"}, MagicMock())
            assert r.status == StructuredToolResultStatus.SUCCESS
            assert r.data["hits"][0]["total"] == 2
            body = rsps.calls[0].request.body
            assert "step=1h" in body

    def test_hits_missing_query(self, toolset):
        tool = _tool(toolset, "victorialogs_hits")
        r = tool._invoke({}, MagicMock())
        assert r.status == StructuredToolResultStatus.ERROR


class TestAuthHeaders:
    def test_basic_auth_sent(self, toolset):
        toolset.config = VictoriaLogsConfig(
            api_url=API_URL, username="u", password="p"
        )
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/query",
                body="",
                status=200,
            )
            tool = _tool(toolset, "victorialogs_query")
            tool._invoke({"query": "*"}, MagicMock())
            auth_header = rsps.calls[0].request.headers.get("Authorization", "")
            assert auth_header.startswith("Basic ")

    def test_bearer_token_sent(self, toolset):
        toolset.config = VictoriaLogsConfig(
            api_url=API_URL, bearer_token="my-token"
        )
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/query",
                body="",
                status=200,
            )
            tool = _tool(toolset, "victorialogs_query")
            tool._invoke({"query": "*"}, MagicMock())
            assert (
                rsps.calls[0].request.headers["Authorization"] == "Bearer my-token"
            )

    def test_extra_headers_sent(self, toolset):
        toolset.config = VictoriaLogsConfig(
            api_url=API_URL, headers={"AccountID": "0", "ProjectID": "1"}
        )
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/query",
                body="",
                status=200,
            )
            tool = _tool(toolset, "victorialogs_query")
            tool._invoke({"query": "*"}, MagicMock())
            assert rsps.calls[0].request.headers["AccountID"] == "0"
            assert rsps.calls[0].request.headers["ProjectID"] == "1"


class TestExploreUrl:
    def test_explore_url_built(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/select/logsql/query",
                body="",
                status=200,
            )
            tool = _tool(toolset, "victorialogs_query")
            r = tool._invoke(
                {
                    "query": "level:error",
                    "start": "2026-04-27T09:00:00Z",
                    "end": "2026-04-27T11:00:00Z",
                },
                MagicMock(),
            )
            assert r.url is not None
            assert "/select/vmui/#/?" in r.url
            assert "query=level%3Aerror" in r.url


# ---------------------------------------------------------------------------
# Live tests (only run when VICTORIALOGS_URL is set, e.g. against the local sandbox).
# These exercise a real VictoriaLogs HTTP API.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("VICTORIALOGS_URL"),
    reason="VICTORIALOGS_URL env var not set",
)
class TestLiveVictoriaLogs:
    """Live tests against a running VictoriaLogs server.

    Set VICTORIALOGS_URL=http://localhost:9428 to run these tests.
    """

    @pytest.fixture
    def live_toolset(self):
        from datetime import datetime, timedelta, timezone
        import time

        ts = VictoriaLogsToolset()
        url = os.environ["VICTORIALOGS_URL"]

        # Insert a deterministic test record at the current time.
        import requests as _requests

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _requests.post(
            f"{url}/insert/jsonline?_stream_fields=service,namespace&_msg_field=message&_time_field=time",
            data=json.dumps(
                {
                    "time": now,
                    "service": "holmes-test",
                    "namespace": "holmes-test",
                    "level": "info",
                    "message": "VICTORIALOGS_TEST_MARKER_HOLMES_42",
                }
            ),
            headers={"Content-Type": "application/stream+json"},
            timeout=10,
        )
        # Give VictoriaLogs a moment to flush the ingestion buffer
        time.sleep(2)

        ok, msg = ts.prerequisites_callable({"api_url": url})
        assert ok, msg
        # Save the time range used for live queries
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=10)
        ts._test_start = start.strftime("%Y-%m-%dT%H:%M:%SZ")  # type: ignore[attr-defined]
        ts._test_end = (end + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")  # type: ignore[attr-defined]
        return ts

    def test_health_check_live(self, live_toolset):
        # Already validated by fixture, but make it explicit.
        ok, _ = live_toolset.prerequisites_callable(
            {"api_url": os.environ["VICTORIALOGS_URL"]}
        )
        assert ok

    def test_query_live(self, live_toolset):
        tool = _tool(live_toolset, "victorialogs_query")
        r = tool._invoke(
            {
                "query": "VICTORIALOGS_TEST_MARKER_HOLMES_42",
                "start": live_toolset._test_start,
                "end": live_toolset._test_end,
            },
            MagicMock(),
        )
        assert r.status == StructuredToolResultStatus.SUCCESS, r.error or r.data
        assert any(
            "VICTORIALOGS_TEST_MARKER_HOLMES_42" in entry.get("_msg", "")
            for entry in r.data
        )

    def test_streams_live(self, live_toolset):
        tool = _tool(live_toolset, "victorialogs_streams")
        r = tool._invoke(
            {
                "query": "{namespace=\"holmes-test\"}",
                "start": live_toolset._test_start,
                "end": live_toolset._test_end,
            },
            MagicMock(),
        )
        assert r.status == StructuredToolResultStatus.SUCCESS, r.error or r.data
        assert len(r.data) >= 1

    def test_field_values_live(self, live_toolset):
        tool = _tool(live_toolset, "victorialogs_field_values")
        r = tool._invoke(
            {
                "field": "level",
                "query": "{namespace=\"holmes-test\"}",
                "start": live_toolset._test_start,
                "end": live_toolset._test_end,
            },
            MagicMock(),
        )
        assert r.status == StructuredToolResultStatus.SUCCESS, r.error or r.data
        assert any(entry.get("value") == "info" for entry in r.data)

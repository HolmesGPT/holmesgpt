"""Internal request kinds on the RemoteToolCalls channel.

The platform UI (via relay) writes rows with metadata.kind set; the worker
must dispatch them before the caller-version guard and the tool lookup, and
leave rows without a kind on the normal tool path.
"""

import base64
import gzip
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.conversations_worker.tool_call_worker import ToolCallWorker
from holmes.core.llm import LLM
from holmes.core.models import OAuthCallbackRequest, OAuthCallbackResponse
from holmes.core.oauth_config import OAuthConfigLookupError, OAuthTokenExchangeError
from holmes.core.self_logs import HolmesLogsRequest
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.version import get_version

_WORKER = "holmes.core.conversations_worker.tool_call_worker"

_SECRET_CODE = "auth-code-SECRET-1234"
_SECRET_VERIFIER = "verifier-SECRET-5678"
_SECRET_CLIENT_SECRET = "client-secret-SECRET-9012"


def _worker():
    config = MagicMock()
    return ToolCallWorker(dal=MagicMock(), config=config, holmes_id="h-test")


def _oauth_row(user_id="user-from-row", **payload_overrides):
    payload = {
        "toolset_name": "github_mcp",
        "code": _SECRET_CODE,
        "code_verifier": _SECRET_VERIFIER,
        "redirect_uri": "https://platform.example/oauth/callback",
        "client_id": "dcr-client",
        "client_secret": _SECRET_CLIENT_SECRET,
        "resource": "https://mcp.example",
    }
    payload.update(payload_overrides)
    return {
        "id": "row-oauth",
        "source_cluster": "ui",
        "user_id": user_id,
        "tool_request": {"kind": "oauth_callback", "payload": payload},
        # No source_version: the UI cannot supply the caller version.
        "metadata": {"kind": "oauth_callback"},
    }


def _data(resp):
    if resp.get("compressed"):
        return json.loads(gzip.decompress(base64.b64decode(resp["data_gz_b64"])))
    return json.loads(resp["data"])


# ---- oauth_callback ----


def test_oauth_callback_dispatches_with_user_id_from_row():
    worker = _worker()
    with patch(
        f"{_WORKER}.handle_oauth_callback",
        return_value=OAuthCallbackResponse(success=True),
    ) as handler:
        resp = worker._execute(_oauth_row())

    assert resp["status"] == StructuredToolResultStatus.SUCCESS.value
    assert resp["error"] is None
    assert _data(resp) == {"success": True, "error": None}

    handler.assert_called_once()
    request, config, dal = handler.call_args[0]
    assert isinstance(request, OAuthCallbackRequest)
    assert request.user_id == "user-from-row"
    assert request.toolset_name == "github_mcp"
    assert request.code == _SECRET_CODE
    assert request.code_verifier == _SECRET_VERIFIER
    assert request.client_secret == _SECRET_CLIENT_SECRET
    assert request.resource == "https://mcp.example"
    assert config is worker.config and dal is worker.dal


def test_oauth_callback_ignores_user_id_in_payload():
    worker = _worker()
    with patch(
        f"{_WORKER}.handle_oauth_callback",
        return_value=OAuthCallbackResponse(success=True),
    ) as handler:
        row = _oauth_row(user_id="real-user")
        row["tool_request"]["payload"]["user_id"] = "attacker"
        worker._execute(row)

    assert handler.call_args[0][0].user_id == "real-user"


def test_oauth_callback_without_row_user_id_is_rejected():
    worker = _worker()
    with patch(f"{_WORKER}.handle_oauth_callback") as handler:
        resp = worker._execute(_oauth_row(user_id=None))
    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "user_id" in resp["error"]
    handler.assert_not_called()


def test_oauth_callback_skips_version_guard_and_tool_lookup():
    worker = _worker()
    row = _oauth_row()
    row["metadata"]["source_version"] = "0.0.0-some-other-version"
    with patch(
        f"{_WORKER}.handle_oauth_callback",
        return_value=OAuthCallbackResponse(success=True),
    ):
        resp = worker._execute(row)
    assert resp["status"] == StructuredToolResultStatus.SUCCESS.value
    worker.config.create_tool_executor.assert_not_called()


def test_oauth_callback_invalid_payload_does_not_leak_secrets():
    worker = _worker()
    row = _oauth_row()
    del row["tool_request"]["payload"]["redirect_uri"]
    row["tool_request"]["payload"]["code"] = {"not": _SECRET_CODE}
    with patch(f"{_WORKER}.handle_oauth_callback") as handler:
        resp = worker._execute(row)
    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "redirect_uri" in resp["error"] and "code" in resp["error"]
    assert _SECRET_CODE not in resp["error"]
    assert _SECRET_VERIFIER not in resp["error"]
    handler.assert_not_called()


@pytest.mark.parametrize(
    "exc, expected",
    [
        (OAuthConfigLookupError("Toolset 'github_mcp' not found"), "not found"),
        (OAuthTokenExchangeError(400, "invalid_grant"), "HTTP 400"),
        (RuntimeError("boom"), "boom"),
    ],
)
def test_oauth_callback_errors_become_error_responses(exc, expected):
    worker = _worker()
    with patch(f"{_WORKER}.handle_oauth_callback", side_effect=exc):
        resp = worker._execute(_oauth_row())
    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert expected in resp["error"]
    assert resp["data"] is None


def test_oauth_callback_never_logs_secrets(caplog):
    worker = _worker()
    caplog.set_level(logging.DEBUG)
    with patch(
        f"{_WORKER}.handle_oauth_callback",
        side_effect=OAuthTokenExchangeError(400, "invalid_grant"),
    ):
        worker._execute(_oauth_row())
    with patch(
        f"{_WORKER}.handle_oauth_callback",
        return_value=OAuthCallbackResponse(success=True),
    ):
        worker._execute(_oauth_row())
    for secret in (_SECRET_CODE, _SECRET_VERIFIER, _SECRET_CLIENT_SECRET):
        assert secret not in caplog.text


def test_oauth_callback_unsuccessful_response_is_passed_through():
    worker = _worker()
    with patch(
        f"{_WORKER}.handle_oauth_callback",
        return_value=OAuthCallbackResponse(success=False, error="denied"),
    ):
        resp = worker._execute(_oauth_row())
    assert resp["status"] == StructuredToolResultStatus.SUCCESS.value
    assert _data(resp) == {"success": False, "error": "denied"}


# ---- holmes_logs ----


def _logs_row(payload=None):
    return {
        "id": "row-logs",
        "source_cluster": "ui",
        "user_id": "u1",
        "tool_request": {"kind": "holmes_logs", "payload": payload or {}},
        "metadata": {"kind": "holmes_logs"},
    }


def test_holmes_logs_dispatch_parses_and_clamps_payload():
    worker = _worker()
    data = {"source": "memory", "namespace": None, "pods": [], "logs": [], "error": None}
    with patch(f"{_WORKER}.get_holmes_logs", return_value=data) as get_logs:
        resp = worker._execute(
            _logs_row({"tail_lines": 999999, "previous": True, "pod_name": "holmes-1"})
        )
    assert resp["status"] == StructuredToolResultStatus.SUCCESS.value
    assert _data(resp) == data
    request = get_logs.call_args[0][0]
    assert isinstance(request, HolmesLogsRequest)
    assert request.tail_lines == 5000
    assert request.previous is True
    assert request.pod_name == "holmes-1"
    assert request.include_logs is True
    worker.config.create_tool_executor.assert_not_called()


def test_holmes_logs_defaults_when_payload_missing():
    worker = _worker()
    row = _logs_row()
    row["tool_request"].pop("payload")
    with patch(
        f"{_WORKER}.get_holmes_logs",
        return_value={"source": "memory", "namespace": None, "pods": [], "logs": [], "error": None},
    ) as get_logs:
        resp = worker._execute(row)
    assert resp["status"] == StructuredToolResultStatus.SUCCESS.value
    request = get_logs.call_args[0][0]
    assert (request.tail_lines, request.previous, request.pod_name, request.include_logs) == (
        500,
        False,
        None,
        True,
    )


def test_holmes_logs_large_result_is_gzipped():
    worker = _worker()
    data = {
        "source": "kubernetes",
        "namespace": "robusta",
        "pods": [],
        "logs": [{"pod": "p", "container": "c", "previous": False, "text": "line\n" * 60_000, "truncated": False}],
        "error": None,
    }
    with patch(f"{_WORKER}.get_holmes_logs", return_value=data):
        resp = worker._execute(_logs_row())
    assert resp["compressed"] is True and resp["data"] is None
    assert _data(resp) == data


def test_holmes_logs_invalid_payload_is_error():
    worker = _worker()
    with patch(f"{_WORKER}.get_holmes_logs") as get_logs:
        resp = worker._execute(_logs_row({"tail_lines": "lots"}))
    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "tail_lines" in resp["error"]
    get_logs.assert_not_called()


# ---- dispatch edges ----


def test_unknown_kind_is_error():
    worker = _worker()
    row = _logs_row()
    row["metadata"]["kind"] = "drop_tables"
    resp = worker._execute(row)
    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "unknown internal request kind 'drop_tables'" in resp["error"]
    worker.config.create_tool_executor.assert_not_called()


def test_non_object_payload_is_error():
    worker = _worker()
    row = _logs_row()
    row["tool_request"]["payload"] = ["not", "a", "dict"]
    resp = worker._execute(row)
    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "payload must be an object" in resp["error"]


def test_kind_only_in_tool_request_is_not_internal():
    """Dispatch keys on metadata.kind; a tool row stays on the tool path."""
    worker = _worker()
    row = {
        "id": "row-x",
        "user_id": None,
        "tool_request": {"kind": "oauth_callback", "payload": {}},
        "metadata": {"source_version": "0.0.0-other"},
    }
    with patch(f"{_WORKER}.handle_oauth_callback") as handler:
        resp = worker._execute(row)
    assert "version mismatch" in resp["error"]
    handler.assert_not_called()


def _tool_worker():
    tool = MagicMock(spec=["name", "invoke"])
    tool.name = "probe"
    tool.invoke.return_value = StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS, data="probe ran"
    )
    toolset = MagicMock(spec=["name", "is_core", "expose_remotely"])
    toolset.name = "fake_ts"
    toolset.is_core = False
    toolset.expose_remotely = True
    executor = MagicMock()
    executor.tools_by_name = {"probe": tool}
    executor._tool_to_toolset = {"probe": toolset}
    config = MagicMock()
    config.create_tool_executor.return_value = executor
    config._get_llm.return_value = MagicMock(spec=LLM)
    return ToolCallWorker(dal=MagicMock(), config=config, holmes_id="h"), tool


def _tool_row(metadata):
    return {
        "id": "row-tool",
        "user_id": None,
        "tool_request": {
            "tool_name": "probe",
            "tool_params": {},
            "tool_call_id": "c1",
            "max_token_count": 1000,
        },
        "metadata": metadata,
    }


@pytest.mark.parametrize(
    "metadata",
    [
        {"source_version": get_version()},
        {"source_version": get_version(), "kind": None},
    ],
)
def test_tool_rows_without_kind_run_as_before(metadata):
    worker, tool = _tool_worker()
    with patch(f"{_WORKER}.handle_oauth_callback") as oauth, patch(
        f"{_WORKER}.get_holmes_logs"
    ) as logs:
        resp = worker._execute(_tool_row(metadata))
    assert resp["status"] == StructuredToolResultStatus.SUCCESS.value
    assert resp["data"] == "probe ran"
    tool.invoke.assert_called_once()
    oauth.assert_not_called()
    logs.assert_not_called()


def test_tool_rows_without_kind_keep_version_guard():
    worker, tool = _tool_worker()
    resp = worker._execute(_tool_row({"source_version": "0.0.0-other"}))
    assert "version mismatch" in resp["error"]
    tool.invoke.assert_not_called()


def test_execute_safe_posts_internal_result():
    worker = _worker()
    worker.dal.post_remote_tool_call_result.return_value = True
    with patch(
        f"{_WORKER}.handle_oauth_callback",
        return_value=OAuthCallbackResponse(success=True),
    ):
        worker._active_count = 1
        worker._execute_safe(_oauth_row())
    kwargs = worker.dal.post_remote_tool_call_result.call_args.kwargs
    assert kwargs["tool_call_id"] == "row-oauth"
    assert kwargs["status"] == "completed"
    assert kwargs["tool_response"]["status"] == "success"

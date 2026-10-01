from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
import requests
import responses

from holmes.core.tool_errors import classify_tool_error
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus


@pytest.mark.parametrize("error,kind", [
    (TimeoutError("timeout"), "timeout"),
    (requests.Timeout("timeout"), "timeout"),
    (httpx.ReadTimeout("timeout"), "timeout"),
    (ConnectionError("connection failed"), "connection"),
    (requests.ConnectionError("connection failed"), "connection"),
    (PermissionError("not permitted"), "permission"),
    (requests.exceptions.SSLError("invalid certificate"), "unknown"),
    (ValueError("HTTP 503 timeout; retry this call"), "unknown"),
])
def test_error_classification_uses_exception_types_not_message_text(error, kind):
    assert classify_tool_error(error).kind == kind


@pytest.mark.parametrize("code,kind", [
    (400, "invalid_arguments"), (401, "permission"), (403, "permission"),
    (404, "invalid_arguments"), (408, "timeout"), (429, "rate_limit"),
    (500, "unavailable"), (503, "unavailable"), (504, "unavailable"), (501, "unknown"),
])
def test_http_failures_keep_typed_status_and_retry_delay(code, kind):
    with responses.RequestsMock() as http:
        http.add(responses.GET, "https://source.example/query", status=code, headers={"Retry-After": "7"})
        with pytest.raises(requests.HTTPError) as failure:
            requests.get("https://source.example/query", timeout=1).raise_for_status()
        details = classify_tool_error(failure.value)
        assert details.kind == kind
        assert details.retry_after_seconds == 7
    response = httpx.Response(code, request=httpx.Request("GET", "https://source.example/query"))
    with pytest.raises(httpx.HTTPStatusError) as failure:
        response.raise_for_status()
    assert classify_tool_error(failure.value).kind == kind


def test_http_retry_after_date_is_preserved():
    retry_at = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60), usegmt=True)
    response = httpx.Response(429, headers={"Retry-After": retry_at},
                              request=httpx.Request("GET", "https://source.example/query"))
    details = classify_tool_error(httpx.HTTPStatusError("rate limited", request=response.request, response=response))
    assert 50 <= details.retry_after_seconds <= 60


@pytest.mark.parametrize("header", ["not a date", "nan", "inf", "-1"])
def test_invalid_retry_after_is_not_a_sleep_duration(header):
    response = httpx.Response(503, headers={"Retry-After": header},
                              request=httpx.Request("GET", "https://source.example/query"))
    details = classify_tool_error(httpx.HTTPStatusError("unavailable", request=response.request, response=response))
    assert details.kind == "unavailable"
    assert details.retry_after_seconds is None


def test_grouped_transport_errors_are_conservative_and_wire_format_is_unchanged():
    class GroupedError(Exception):
        def __init__(self, errors):
            self.exceptions = errors

    assert classify_tool_error(GroupedError([TimeoutError(), httpx.ReadTimeout("timeout")])).kind == "timeout"
    assert classify_tool_error(GroupedError([TimeoutError(), PermissionError()])).kind == "unknown"
    assert classify_tool_error(GroupedError([GroupedError([TimeoutError()])])).kind == "timeout"
    result = StructuredToolResult(status=StructuredToolResultStatus.ERROR,
                                  error="timeout", error_kind="timeout", retry_after_seconds=2)
    assert "error_kind" not in result.model_dump()
    assert "retry_after_seconds" not in result.model_dump()
    assert result.error_kind == "timeout"

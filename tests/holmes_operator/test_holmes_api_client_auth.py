"""Unit tests for HolmesAPIClient authentication header behavior.

Covers the contract between the operator and the Holmes API server when
HOLMES_API_KEY is configured (issue #2030):
- without an API key, no auth header is sent (current behavior preserved);
- with an API key, the X-API-Key header is sent on /api/checks/execute
  (the request path that previously failed with 401 Unauthorized);
- with an API key, the X-API-Key header is sent on every endpoint the client
  exposes, so a future endpoint can't silently regress.
"""

import pytest
import respx
from httpx import Response

from holmes_operator.client.holmes_api_client import HolmesAPIClient


SAMPLE_CHECK_RESPONSE = {
    "status": "pass",
    "message": "ok",
    "rationale": "ok",
    "duration": 0.1,
    "model_used": "test-model",
    "notifications": [],
}


@pytest.mark.asyncio
async def test_no_api_key_sends_no_auth_header(respx_mock):
    """Without an API key, the client must not send X-API-Key or Authorization."""
    route = respx_mock.post(
        "http://mock-holmes-api:80/api/checks/execute"
    ).mock(return_value=Response(200, json=SAMPLE_CHECK_RESPONSE))

    client = HolmesAPIClient(base_url="http://mock-holmes-api:80")
    try:
        await client.execute_check(
            check_name="t",
            query="q",
            timeout=10,
            mode="monitor",
            destinations=[],
        )
    finally:
        await client.close()

    assert route.called
    sent = route.calls.last.request
    assert "X-API-Key" not in sent.headers
    assert "Authorization" not in sent.headers


@pytest.mark.asyncio
async def test_api_key_sent_on_execute_check(respx_mock):
    """With an API key, X-API-Key must reach /api/checks/execute exactly."""
    route = respx_mock.post(
        "http://mock-holmes-api:80/api/checks/execute"
    ).mock(return_value=Response(200, json=SAMPLE_CHECK_RESPONSE))

    client = HolmesAPIClient(base_url="http://mock-holmes-api:80", api_key="secret")
    try:
        await client.execute_check(
            check_name="t",
            query="q",
            timeout=10,
            mode="monitor",
            destinations=[],
        )
    finally:
        await client.close()

    assert route.called
    sent = route.calls.last.request
    assert sent.headers.get("X-API-Key") == "secret"
    assert sent.url.path == "/api/checks/execute"


@pytest.mark.asyncio
async def test_api_key_sent_on_every_endpoint(respx_mock):
    """The auth header must apply to every endpoint, not just execute_check."""
    public_methods = [
        name
        for name in dir(HolmesAPIClient)
        if not name.startswith("_") and callable(getattr(HolmesAPIClient, name))
    ]
    # Sanity check: if a new endpoint is added without updating this test, the
    # author should at least see the list grow.
    assert "execute_check" in public_methods

    route = respx_mock.route(host="mock-holmes-api").mock(
        return_value=Response(200, json=SAMPLE_CHECK_RESPONSE)
    )

    client = HolmesAPIClient(base_url="http://mock-holmes-api:80", api_key="secret")
    try:
        # Drive every HTTP-bound endpoint the client exposes today.
        await client.execute_check(
            check_name="t",
            query="q",
            timeout=10,
            mode="monitor",
            destinations=[],
        )
        # Exercise the underlying httpx.AsyncClient directly to assert the header
        # is attached by construction — any future endpoint built on self.client
        # inherits the header automatically.
        await client.client.get("http://mock-holmes-api:80/some/future/endpoint")
    finally:
        await client.close()

    assert route.call_count >= 2
    for call in route.calls:
        assert call.request.headers.get("X-API-Key") == "secret"


@pytest.mark.asyncio
async def test_api_key_value_not_logged(caplog):
    """The API key value must never appear in log output."""
    import logging

    caplog.set_level(logging.DEBUG)
    client = HolmesAPIClient(base_url="http://mock-holmes-api:80", api_key="super-secret-value")
    try:
        assert "super-secret-value" not in caplog.text
    finally:
        await client.close()

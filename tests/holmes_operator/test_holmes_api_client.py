"""Tests for the Holmes API client.

The Holmes API server requires authentication whenever it runs with
HOLMES_API_KEY set, so the operator must send the key when it has one.
"""

import pytest
from httpx import Response

from holmes_operator.client.holmes_api_client import HolmesAPIClient

BASE_URL = "http://mock-holmes-api:80"
EXECUTE_URL = f"{BASE_URL}/api/checks/execute"

CHECK_RESPONSE = {
    "status": "pass",
    "message": "All systems operational",
    "rationale": "Nothing to report",
    "duration": 1.0,
}


@pytest.fixture
async def make_client():
    """Build clients and close them when the test finishes."""
    clients = []

    def _make(**kwargs):
        client = HolmesAPIClient(base_url=BASE_URL, timeout=30, **kwargs)
        clients.append(client)
        return client

    yield _make

    for client in clients:
        await client.close()


async def _execute_check(client):
    return await client.execute_check(
        check_name="test-check",
        query="Is the cluster healthy?",
        timeout=30,
        mode="monitor",
        destinations=[],
    )


async def test_api_key_is_sent_as_x_api_key_header(make_client, respx_mock):
    route = respx_mock.post(EXECUTE_URL).mock(
        return_value=Response(200, json=CHECK_RESPONSE)
    )

    await _execute_check(make_client(api_key="secret-key"))

    assert route.called
    assert route.calls.last.request.headers["X-API-Key"] == "secret-key"


async def test_no_api_key_header_when_key_is_not_configured(make_client, respx_mock):
    route = respx_mock.post(EXECUTE_URL).mock(
        return_value=Response(200, json=CHECK_RESPONSE)
    )

    await _execute_check(make_client())

    assert route.called
    assert "X-API-Key" not in route.calls.last.request.headers

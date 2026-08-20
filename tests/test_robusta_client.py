"""fetch_robusta_models retry behavior (ROB-795): a transient relay/gateway
failure during the single startup fetch must not permanently degrade the
agent to the legacy single-model fallback."""

import pytest
import requests
import responses
from tenacity import wait_none

import holmes.clients.robusta_client as robusta_client
from holmes.clients.robusta_client import FETCH_MODELS_ATTEMPTS, fetch_robusta_models
from holmes.common.env_vars import ROBUSTA_API_ENDPOINT

MODELS_URL = f"{ROBUSTA_API_ENDPOINT}/api/llm/models/v2"
MODELS_PAYLOAD = {
    "Robusta/gpt-5": {"model": "azure/gpt-5", "holmes_args": {}, "is_default": True}
}


@pytest.fixture(autouse=True)
def instant_retries(monkeypatch):
    monkeypatch.setattr(
        robusta_client._request_robusta_models.retry, "wait", wait_none()
    )


@pytest.fixture
def mocked_responses():
    with responses.RequestsMock() as rsps:
        yield rsps


def test_returns_models_on_first_success(mocked_responses):
    mocked_responses.post(MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert set(result.models) == {"Robusta/gpt-5"}
    assert result.models["Robusta/gpt-5"].is_default
    assert len(mocked_responses.calls) == 1


def test_recovers_from_transient_gateway_errors(mocked_responses):
    mocked_responses.post(MODELS_URL, status=502)
    mocked_responses.post(MODELS_URL, status=502)
    mocked_responses.post(MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert set(result.models) == {"Robusta/gpt-5"}
    assert len(mocked_responses.calls) == 3


def test_gives_up_after_max_attempts(mocked_responses):
    for _ in range(FETCH_MODELS_ATTEMPTS):
        mocked_responses.post(MODELS_URL, status=502)

    result = fetch_robusta_models("account-id", "token")

    assert result is None
    assert len(mocked_responses.calls) == FETCH_MODELS_ATTEMPTS


def test_does_not_retry_client_errors(mocked_responses):
    mocked_responses.post(MODELS_URL, status=401)

    result = fetch_robusta_models("account-id", "token")

    assert result is None
    assert len(mocked_responses.calls) == 1


def test_retries_connection_errors(mocked_responses):
    mocked_responses.post(MODELS_URL, body=requests.exceptions.ConnectionError())
    mocked_responses.post(MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert len(mocked_responses.calls) == 2


def test_retries_timeouts(mocked_responses):
    mocked_responses.post(MODELS_URL, body=requests.exceptions.Timeout())
    mocked_responses.post(MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert len(mocked_responses.calls) == 2

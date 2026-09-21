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

MODELS_URL = f"{ROBUSTA_API_ENDPOINT}/api/llm/models/v3"
MODELS_PAYLOAD = {
    "models": {
        "Robusta/gpt-5": {"model": "azure/gpt-5", "holmes_args": {}, "is_default": True}
    },
    "default_model": "Robusta/gpt-5",
    "fallback_model": None,
    "platform_default_model": "Robusta/gpt-5",
    "robusta_ai_disabled": False,
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
    mocked_responses.add(responses.POST, MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert set(result.models) == {"Robusta/gpt-5"}
    assert result.models["Robusta/gpt-5"].is_default
    assert len(mocked_responses.calls) == 1


def test_recovers_from_transient_gateway_errors(mocked_responses):
    mocked_responses.add(responses.POST, MODELS_URL, status=502)
    mocked_responses.add(responses.POST, MODELS_URL, status=502)
    mocked_responses.add(responses.POST, MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert set(result.models) == {"Robusta/gpt-5"}
    assert len(mocked_responses.calls) == 3


def test_gives_up_after_max_attempts(mocked_responses):
    for _ in range(FETCH_MODELS_ATTEMPTS):
        mocked_responses.add(responses.POST, MODELS_URL, status=502)

    result = fetch_robusta_models("account-id", "token")

    assert result is None
    assert len(mocked_responses.calls) == FETCH_MODELS_ATTEMPTS


@pytest.mark.parametrize("status", [401, 404])
def test_does_not_retry_client_errors(mocked_responses, status):
    """404 is a platform that does not serve v3: the registry then loads its
    legacy single-model entry, the same as after any failed fetch."""
    mocked_responses.add(responses.POST, MODELS_URL, status=status)

    result = fetch_robusta_models("account-id", "token")

    assert result is None
    assert len(mocked_responses.calls) == 1


def test_retries_connection_errors(mocked_responses):
    mocked_responses.add(
        responses.POST, MODELS_URL, body=requests.exceptions.ConnectionError()
    )
    mocked_responses.add(responses.POST, MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert len(mocked_responses.calls) == 2


def test_retries_timeouts(mocked_responses):
    mocked_responses.add(responses.POST, MODELS_URL, body=requests.exceptions.Timeout())
    mocked_responses.add(responses.POST, MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert len(mocked_responses.calls) == 2


def test_retries_rate_limiting(mocked_responses):
    mocked_responses.add(responses.POST, MODELS_URL, status=429)
    mocked_responses.add(responses.POST, MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert len(mocked_responses.calls) == 2


def test_parses_an_opted_out_account(mocked_responses):
    """An account with the Robusta AI opt-out set gets an empty catalog and no
    defaults - the flag is what tells the agent this is deliberate rather than
    a relay hiccup."""
    mocked_responses.add(
        responses.POST,
        MODELS_URL,
        json={
            "models": {},
            "default_model": None,
            "fallback_model": None,
            "platform_default_model": None,
            "robusta_ai_disabled": True,
        },
        status=200,
    )

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert result.models == {}
    assert result.robusta_ai_disabled


def test_parses_an_enabled_account(mocked_responses):
    mocked_responses.add(responses.POST, MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert not result.robusta_ai_disabled
    assert result.models["Robusta/gpt-5"].is_default


def test_ignores_the_envelope_fields_holmes_does_not_read(mocked_responses):
    """The v3 payload above already carries relay's own bookkeeping fields;
    they are dropped rather than modelled."""
    mocked_responses.add(responses.POST, MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert not hasattr(result, "default_model")
    assert not hasattr(result, "platform_default_model")


def test_ignores_unknown_response_fields(mocked_responses):
    mocked_responses.add(
        responses.POST,
        MODELS_URL,
        json={**MODELS_PAYLOAD, "something_new": 1},
        status=200,
    )

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert set(result.models) == {"Robusta/gpt-5"}

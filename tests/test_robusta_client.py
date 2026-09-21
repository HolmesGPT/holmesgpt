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
MODELS_V2_URL = f"{ROBUSTA_API_ENDPOINT}/api/llm/models/v2"
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
    monkeypatch.setattr(robusta_client._post_models.retry, "wait", wait_none())


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


def test_retries_rate_limiting(mocked_responses):
    mocked_responses.post(MODELS_URL, status=429)
    mocked_responses.post(MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert len(mocked_responses.calls) == 2


def test_parses_an_opted_out_account(mocked_responses):
    """An account with the Robusta AI opt-out set gets an empty catalog and no
    defaults - the flag is what tells the agent this is deliberate rather than
    a relay hiccup."""
    mocked_responses.post(
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
    mocked_responses.post(MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert not result.robusta_ai_disabled
    assert result.models["Robusta/gpt-5"].is_default


def test_ignores_the_envelope_fields_holmes_does_not_read(mocked_responses):
    """The v3 payload above already carries relay's own bookkeeping fields;
    they are dropped rather than modelled."""
    mocked_responses.post(MODELS_URL, json=MODELS_PAYLOAD, status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert not hasattr(result, "default_model")
    assert not hasattr(result, "platform_default_model")


def test_ignores_unknown_response_fields(mocked_responses):
    mocked_responses.post(
        MODELS_URL, json={**MODELS_PAYLOAD, "something_new": 1}, status=200
    )

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert set(result.models) == {"Robusta/gpt-5"}


def test_falls_back_to_v2_when_the_platform_has_no_v3(mocked_responses):
    """A platform that predates v3 has no opt-out to report, so its bare
    catalog is read as an enabled account."""
    mocked_responses.post(MODELS_URL, status=404)
    mocked_responses.post(MODELS_V2_URL, json=MODELS_PAYLOAD["models"], status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert set(result.models) == {"Robusta/gpt-5"}
    assert result.models["Robusta/gpt-5"].is_default
    assert not result.robusta_ai_disabled
    assert len(mocked_responses.calls) == 2
    assert mocked_responses.calls[0].request.url == MODELS_URL
    assert mocked_responses.calls[1].request.url == MODELS_V2_URL


def test_returns_none_when_neither_version_is_served(mocked_responses):
    mocked_responses.post(MODELS_URL, status=404)
    mocked_responses.post(MODELS_V2_URL, status=404)

    result = fetch_robusta_models("account-id", "token")

    assert result is None
    assert len(mocked_responses.calls) == 2


def test_a_v3_client_error_is_not_read_as_a_missing_endpoint(mocked_responses):
    """Only 404 means the platform has no v3. A 403 on v3 is v3 refusing this
    request, and reading v2 instead would hide whatever it refused."""
    mocked_responses.post(MODELS_URL, status=403)

    result = fetch_robusta_models("account-id", "token")

    assert result is None
    assert len(mocked_responses.calls) == 1


def test_a_v3_server_error_is_retried_rather_than_falling_back(mocked_responses):
    """A 500 is a blip on a platform that does serve v3; dropping to v2 there
    would silently hide the opt-out."""
    for _ in range(FETCH_MODELS_ATTEMPTS):
        mocked_responses.post(MODELS_URL, status=500)

    result = fetch_robusta_models("account-id", "token")

    assert result is None
    assert len(mocked_responses.calls) == FETCH_MODELS_ATTEMPTS
    assert all(call.request.url == MODELS_URL for call in mocked_responses.calls)


def test_v2_is_retried_on_its_own_after_the_fallback(mocked_responses):
    mocked_responses.post(MODELS_URL, status=404)
    mocked_responses.post(MODELS_V2_URL, status=502)
    mocked_responses.post(MODELS_V2_URL, json=MODELS_PAYLOAD["models"], status=200)

    result = fetch_robusta_models("account-id", "token")

    assert result is not None
    assert set(result.models) == {"Robusta/gpt-5"}
    assert len(mocked_responses.calls) == 3

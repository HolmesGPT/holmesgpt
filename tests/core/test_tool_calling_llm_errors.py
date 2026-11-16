"""What the user sees when relay refuses an LLM call (ROB-1389).

Relay answers a call on a Robusta-hosted model with 403 when the account
disabled Robusta-hosted models, and with 401 when the session token went
stale; the body of both carries the sentence the user has to act on. litellm
maps those to its own exception classes and renders the message as
`litellm.<Class>: <Class>: OpenAIException - <body>`, which buries it - so the
errors here are built through litellm's real mapping rather than by hand. What
holmes re-raises is its own `RelayRefusal`, carrying relay's sentence and the
status an HTTP consumer answers with.
"""

from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from litellm.exceptions import AuthenticationError, BadRequestError
from litellm.litellm_core_utils.exception_mapping_utils import exception_type

from holmes.core.llm import LLM, ContextWindowUsage
from holmes.core.llm_usage import RequestStats
from holmes.core.tool_calling_llm import RelayRefusal, ToolCallingLLM
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.truncation.input_context_window_limiter import (
    ContextWindowLimiterOutput,
)

LIMIT_PATCH = "holmes.core.tool_calling_llm.compact_if_necessary"

DISABLED = (
    "Robusta-hosted models are disabled for this account. Configure a model on "
    "the cluster, or enable Robusta-hosted models in Settings > LLM Models."
)
STALE_TOKEN = "Your session has expired. Reconnect the cluster to the platform."

TOKEN_COUNT = ContextWindowUsage(
    total_tokens=100,
    system_tokens=0,
    tools_to_call_tokens=0,
    tools_tokens=0,
    user_tokens=0,
    assistant_tokens=0,
    other_tokens=0,
)


def _passthrough_limiter(messages, **_kwargs):
    return ContextWindowLimiterOutput(
        metadata={},
        messages=list(messages),
        events=[],
        max_context_size=128000,
        maximum_output_token=4096,
        tokens=TOKEN_COUNT,
        conversation_history_compacted=False,
        compaction_usage=RequestStats(),
    )


def _from_response(status_code: int, body: dict, model: str) -> Exception:
    """The exception holmes actually sees for a body relay returned: built by
    the OpenAI client exactly as it builds it from a real response, then run
    through litellm's own exception mapping."""
    response = httpx.Response(
        status_code,
        request=httpx.Request("POST", f"https://api.robusta.dev/llm/{model}"),
        json=body,
    )
    original = openai.OpenAI(api_key="dummy")._make_status_error_from_response(response)
    try:
        exception_type(
            model=model, original_exception=original, custom_llm_provider="openai"
        )
    except Exception as mapped:
        return mapped
    raise AssertionError("litellm's exception mapping did not raise")


def _mapped_error(
    status_code: int,
    message: str,
    code: str,
    model: str,
    error_type: str = "permission_denied",
) -> Exception:
    return _from_response(
        status_code,
        {"error": {"message": message, "type": error_type, "code": code}},
        model,
    )


@pytest.fixture
def mock_llm():
    llm = MagicMock(spec=LLM)
    llm.count_tokens.return_value = TOKEN_COUNT
    llm.get_context_window_size.return_value = 128000
    llm.get_maximum_output_token.return_value = 4096
    llm.get_max_token_count_for_single_tool.return_value = 10000
    llm.model = "Robusta/gpt-5"
    llm.is_robusta_model = True
    return llm


@pytest.fixture
def make_ai(mock_llm):
    tool_executor = MagicMock(spec=ToolExecutor)
    tool_executor.get_all_tools_openai_format.return_value = []
    tool_executor.ensure_toolset_initialized.return_value = None
    tool_executor.oauth_connector = MagicMock()
    tool_executor.oauth_connector.get_toolset.return_value = None
    toolset = MagicMock()
    toolset.name = "kubectl"
    tool_executor.toolsets = [toolset]
    tool_executor.enabled_toolsets = [toolset]

    def _make():
        return ToolCallingLLM(
            tool_executor=tool_executor,
            max_steps=3,
            llm=mock_llm,
            tool_results_dir=None,
        )

    return _make


def _ask(ai):
    return ai.call([{"role": "user", "content": "what is wrong?"}])


def test_litellm_maps_refusals_by_body_type_not_by_status():
    """The premise the handler rests on: litellm picks the class from the
    body's error type, so neither the class nor the clause order can decide
    what a refusal is - only the status can. A 403 is not an
    AuthenticationError, and a 401 typed `invalid_request_error` arrives as a
    BadRequestError, the same class the Azure 400 case is keyed on."""
    denied = _mapped_error(403, DISABLED, "robusta_ai_disabled", "Robusta/gpt-5")
    assert not isinstance(denied, AuthenticationError)
    assert getattr(denied, "status_code", None) == 403

    unauthorized = _mapped_error(
        401,
        STALE_TOKEN,
        "invalid_session_token",
        "Robusta/gpt-5",
        error_type="invalid_request_error",
    )
    assert isinstance(unauthorized, BadRequestError)
    assert getattr(unauthorized, "status_code", None) == 401


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_disabled_account_refusal_reaches_the_user(_mock_limit, make_ai, mock_llm):
    mock_llm.completion.side_effect = _mapped_error(
        403, DISABLED, "robusta_ai_disabled", "Robusta/gpt-5"
    )

    with pytest.raises(RelayRefusal) as excinfo:
        _ask(make_ai())

    assert excinfo.value.message == DISABLED
    assert str(excinfo.value) == DISABLED
    assert excinfo.value.status_code == 403


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_stale_token_refusal_stays_a_401(_mock_limit, make_ai, mock_llm):
    mock_llm.completion.side_effect = _mapped_error(
        401, STALE_TOKEN, "invalid_api_key", "Robusta/gpt-5"
    )

    with pytest.raises(RelayRefusal) as excinfo:
        _ask(make_ai())

    assert excinfo.value.message == STALE_TOKEN
    assert excinfo.value.status_code == 401


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_a_retried_refusal_keeps_only_relay_text(_mock_limit, make_ai, mock_llm):
    """litellm appends its retry count to str(e); the user gets relay's
    sentence either way."""
    error = _mapped_error(403, DISABLED, "robusta_ai_disabled", "Robusta/gpt-5")
    error.num_retries = 3
    mock_llm.completion.side_effect = error

    with pytest.raises(RelayRefusal) as excinfo:
        _ask(make_ai())

    assert str(excinfo.value) == DISABLED


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_a_fastapi_shaped_body_is_read_too(_mock_limit, make_ai, mock_llm):
    """A `{"detail": ...}` body, the shape FastAPI-based services answer with,
    carries its text in `detail` rather than in OpenAI's `error.message`. This one is the provider
    error as the OpenAI client raises it - litellm's mapping keeps neither the
    body nor the response for a `detail`-shaped 403, so a refusal that has to
    survive the mapping must use the OpenAI error shape."""
    body = {"detail": DISABLED}
    response = httpx.Response(
        403,
        request=httpx.Request("POST", "https://api.robusta.dev/llm/Robusta%2Fgpt-5"),
        json=body,
    )
    mock_llm.completion.side_effect = openai.PermissionDeniedError(
        "Error code: 403", response=response, body=body
    )

    with pytest.raises(RelayRefusal) as excinfo:
        _ask(make_ai())

    assert excinfo.value.message == DISABLED


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_a_refusal_without_a_json_body_falls_back_to_the_error_text(
    _mock_limit, make_ai, mock_llm
):
    """A 403 answered by something in front of relay (an HTML error page) has
    no body to read; the user still gets the status and the error's text,
    without litellm's decoration."""
    response = httpx.Response(
        403,
        request=httpx.Request("POST", "https://api.robusta.dev/llm/Robusta%2Fgpt-5"),
        text="<html>Forbidden</html>",
    )
    mock_llm.completion.side_effect = openai.PermissionDeniedError(
        "PermissionDeniedError: OpenAIException - Forbidden",
        response=response,
        body=None,
    )

    with pytest.raises(RelayRefusal) as excinfo:
        _ask(make_ai())

    assert excinfo.value.message == "Forbidden"
    assert excinfo.value.status_code == 403


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_a_non_azure_bad_request_on_a_robusta_model_is_raised_as_is(
    _mock_limit, make_ai, mock_llm
):
    """A 400 is not a refusal: it stays the provider's error, as before."""
    error = _from_response(
        400,
        {
            "error": {
                "message": "max_tokens is too large",
                "type": "invalid_request_error",
                "code": None,
            }
        },
        "Robusta/gpt-5",
    )
    mock_llm.completion.side_effect = error

    with pytest.raises(BadRequestError) as excinfo:
        _ask(make_ai())

    assert excinfo.value is error


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_non_robusta_models_keep_the_original_error(_mock_limit, make_ai, mock_llm):
    """A user's own key being wrong is the provider's error, and the user needs
    to see it as such - litellm's rendering and all."""
    mock_llm.is_robusta_model = False
    mock_llm.model = "azure/gpt-4o"
    error = _mapped_error(
        401, "Incorrect API key provided", "invalid_api_key", "azure/gpt-4o"
    )
    mock_llm.completion.side_effect = error

    with pytest.raises(AuthenticationError) as excinfo:
        _ask(make_ai())

    assert excinfo.value is error
    assert "litellm." in excinfo.value.message


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_a_bad_request_shaped_401_is_still_a_refusal(_mock_limit, make_ai, mock_llm):
    """litellm maps a 401 whose body says `invalid_request_error` to
    BadRequestError - the class the Azure case is keyed on - so recognising the
    refusal has to happen on the status, before any class-keyed handling."""
    mock_llm.completion.side_effect = _mapped_error(
        401,
        DISABLED,
        "robusta_ai_disabled",
        "Robusta/gpt-5",
        error_type="invalid_request_error",
    )

    with pytest.raises(RelayRefusal) as excinfo:
        _ask(make_ai())

    assert excinfo.value.message == DISABLED
    assert excinfo.value.status_code == 401


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_relays_own_401_body_shape_is_read(_mock_limit, make_ai, mock_llm):
    """Relay's own error responses are `{"msg": ..., "error_code": ...}`; the
    user must read the sentence, not the dict."""
    mock_llm.completion.side_effect = _from_response(
        401, {"msg": "Unauthorized", "error_code": 5001}, "Robusta/gpt-5"
    )

    with pytest.raises(RelayRefusal) as excinfo:
        _ask(make_ai())

    assert excinfo.value.message == "Unauthorized"
    assert excinfo.value.status_code == 401
    assert "error_code" not in str(excinfo.value)


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_the_refusal_carries_the_clean_message_everywhere(
    _mock_limit, make_ai, mock_llm
):
    """Anything that renders the exception - str(), repr(), args - has to show
    relay's sentence, not litellm's decoration of it."""
    mock_llm.completion.side_effect = _mapped_error(
        403, DISABLED, "robusta_ai_disabled", "Robusta/gpt-5"
    )

    with pytest.raises(RelayRefusal) as excinfo:
        _ask(make_ai())

    error = excinfo.value
    assert error.args == (DISABLED,)
    assert str(error) == DISABLED
    assert repr(error) == f"RelayRefusal({DISABLED!r})"
    assert "litellm." not in repr(error)


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_the_azure_bad_request_message_still_wins_on_a_400(
    _mock_limit, make_ai, mock_llm
):
    """The refusal check runs first, so the 400-keyed Azure case must still be
    reached - on a Robusta-hosted model too."""
    mock_llm.completion.side_effect = _from_response(
        400,
        {
            "error": {
                "message": "Unrecognized request arguments supplied: tool_choice, tools",
                "type": "invalid_request_error",
                "code": None,
            }
        },
        "Robusta/gpt-5",
    )

    with pytest.raises(Exception) as excinfo:
        _ask(make_ai())

    assert "Model version 1106 and higher required" in str(excinfo.value)

"""What the user sees when relay refuses an LLM call (ROB-1389).

Relay answers a call on a Robusta-hosted model with 403 when the account
disabled Robusta-hosted models, and with 401 when the session token went
stale; the body of both carries the sentence the user has to act on. litellm
maps those to its own exception classes and renders the message as
`litellm.<Class>: <Class>: OpenAIException - <body>`, which buries it - so the
errors here are built through litellm's real mapping rather than by hand.
"""

from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from litellm.exceptions import AuthenticationError, PermissionDeniedError
from litellm.litellm_core_utils.exception_mapping_utils import exception_type

from holmes.core.llm import LLM, ContextWindowUsage
from holmes.core.llm_usage import RequestStats
from holmes.core.tool_calling_llm import ToolCallingLLM
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


def _mapped_error(status_code: int, message: str, code: str, model: str) -> Exception:
    """The exception holmes actually sees: the provider error relay's proxy
    returns, run through litellm's own exception mapping."""
    body = {"error": {"message": message, "type": "invalid_request_error", "code": code}}
    response = httpx.Response(
        status_code,
        request=httpx.Request("POST", f"https://api.robusta.dev/llm/{model}"),
        json=body,
    )
    error_class = {
        401: openai.AuthenticationError,
        403: openai.PermissionDeniedError,
    }[status_code]
    original = error_class(
        f"Error code: {status_code}", response=response, body=body["error"]
    )
    try:
        exception_type(
            model=model, original_exception=original, custom_llm_provider="openai"
        )
    except Exception as mapped:
        return mapped
    raise AssertionError("litellm's exception mapping did not raise")


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


def test_litellm_maps_a_403_to_an_api_error_not_an_auth_error():
    """The premise the handler rests on: only 401 becomes AuthenticationError,
    so keying on the class alone would miss the disabled-account case."""
    error = _mapped_error(403, DISABLED, "robusta_ai_disabled", "Robusta/gpt-5")

    assert not isinstance(error, AuthenticationError)
    assert getattr(error, "status_code", None) == 403
    assert "litellm." in error.message


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_disabled_account_refusal_reaches_the_user(_mock_limit, make_ai, mock_llm):
    mock_llm.completion.side_effect = _mapped_error(
        403, DISABLED, "robusta_ai_disabled", "Robusta/gpt-5"
    )

    with pytest.raises(PermissionDeniedError) as excinfo:
        _ask(make_ai())

    assert excinfo.value.message == DISABLED
    assert str(excinfo.value) == DISABLED
    assert excinfo.value.status_code == 403


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_stale_token_refusal_stays_a_401(_mock_limit, make_ai, mock_llm):
    mock_llm.completion.side_effect = _mapped_error(
        401, STALE_TOKEN, "invalid_session_token", "Robusta/gpt-5"
    )

    with pytest.raises(AuthenticationError) as excinfo:
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

    with pytest.raises(PermissionDeniedError) as excinfo:
        _ask(make_ai())

    assert str(excinfo.value) == DISABLED


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_a_fastapi_shaped_body_is_read_too(_mock_limit, make_ai, mock_llm):
    """Relay is FastAPI, so a refusal it raises itself carries its text in
    `detail` rather than in OpenAI's `error.message`. This one is the provider
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

    with pytest.raises(PermissionDeniedError) as excinfo:
        _ask(make_ai())

    assert excinfo.value.message == DISABLED


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

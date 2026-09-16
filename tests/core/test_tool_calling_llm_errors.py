"""What the user sees when relay refuses an LLM call (ROB-1389).

Relay answers a call on a Robusta-hosted model with 403 and a body explaining
that the account disabled Robusta-hosted models, and what to do instead.
litellm wraps that into an AuthenticationError whose string is mostly its own
prefix, so the remedy has to be dug back out before it reaches the user.
"""

from unittest.mock import MagicMock, patch

import pytest
from litellm.exceptions import AuthenticationError

from holmes.core.llm import LLM, ContextWindowUsage
from holmes.core.llm_usage import RequestStats
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.truncation.input_context_window_limiter import (
    ContextWindowLimiterOutput,
)

LIMIT_PATCH = "holmes.core.tool_calling_llm.compact_if_necessary"

REFUSAL = (
    "Robusta-hosted models are disabled for this account. Configure a model on "
    "the cluster, or enable Robusta-hosted models in Settings > LLM Models."
)

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


def _auth_error(message: str) -> AuthenticationError:
    return AuthenticationError(
        message=message, llm_provider="openai", model="Robusta/gpt-5"
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


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_relay_refusal_reaches_the_user(_mock_limit, make_ai, mock_llm):
    mock_llm.completion.side_effect = _auth_error(
        'AuthenticationError: OpenAIException - {"detail": "%s"}' % REFUSAL
    )

    with pytest.raises(Exception) as excinfo:
        make_ai().call([{"role": "user", "content": "what is wrong?"}])

    assert str(excinfo.value) == REFUSAL


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_unparseable_refusal_body_falls_back_to_the_error_text(
    _mock_limit, make_ai, mock_llm
):
    mock_llm.completion.side_effect = _auth_error("Invalid session token")

    with pytest.raises(Exception) as excinfo:
        make_ai().call([{"role": "user", "content": "what is wrong?"}])

    assert "Invalid session token" in str(excinfo.value)


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_non_robusta_models_keep_the_original_error(_mock_limit, make_ai, mock_llm):
    """A user's own key being wrong is an AuthenticationError the user needs to
    see as such, with litellm's provider detail intact."""
    mock_llm.is_robusta_model = False
    mock_llm.model = "azure/gpt-4o"
    mock_llm.completion.side_effect = _auth_error("Incorrect API key provided")

    with pytest.raises(AuthenticationError):
        make_ai().call([{"role": "user", "content": "what is wrong?"}])

from unittest.mock import MagicMock, patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.core.llm import DefaultLLM


def _mock_model_response() -> ModelResponse:
    return ModelResponse(
        id="chatcmpl-test",
        choices=[
            Choices(
                index=0,
                message=Message(role="assistant", content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        model="test-model",
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _make_llm(args: dict) -> DefaultLLM:
    """Build a DefaultLLM bypassing __init__/check_llm so we can control self.args directly."""
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.model = "test-model"
    llm.api_key = None
    llm.api_base = None
    llm.api_version = None
    llm.args = dict(args)
    llm.tracer = None
    llm.name = None
    llm.is_robusta_model = False
    return llm


@pytest.fixture
def mock_completion():
    with patch("holmes.core.llm.litellm.completion") as mock:
        mock.return_value = _mock_model_response()
        yield mock


class TestCompletionTemperatureHandling:
    """Verify temperature is forwarded to litellm.completion correctly.

    Behavior matrix rows (see design doc sam.lockart-master-design-20260429-150837.md):
      1. caller=0.7, args={}           -> forward 0.7
      2. caller=0.0, args={}           -> forward 0.0
      3. caller=None, args={temp: 0.5} -> forward 0.5  (PR #698 semantics)
      4. caller=None, args={}          -> key absent
      5. caller=None, args={temp: None}-> key absent   (primary fix)
      6. caller=0.5, args={temp: None} -> forward 0.5  (hidden bug fix)
      7. caller=0.5, args={temp: 0.7}  -> forward 0.7  (persisted wins, PR #698)
    """

    def test_caller_temperature_is_forwarded(self, mock_completion):
        """Row 1: caller temp, empty args -> forwarded."""
        llm = _make_llm({})
        llm.completion(messages=[{"role": "user", "content": "hi"}], temperature=0.7)
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["temperature"] == 0.7

    def test_caller_temperature_zero_is_forwarded(self, mock_completion):
        """Row 2: falsy-but-valid (0.0) -> forwarded."""
        llm = _make_llm({})
        llm.completion(messages=[{"role": "user", "content": "hi"}], temperature=0.0)
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["temperature"] == 0.0

    def test_persisted_temperature_survives_none_caller(self, mock_completion):
        """Row 3: PR #698 regression guard -- persisted temp survives caller=None."""
        llm = _make_llm({"temperature": 0.5})
        llm.completion(messages=[{"role": "user", "content": "hi"}], temperature=None)
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["temperature"] == 0.5

    def test_no_temperature_anywhere_forwards_no_key(self, mock_completion):
        """Row 4: no temp anywhere -> no key forwarded."""
        llm = _make_llm({})
        llm.completion(messages=[{"role": "user", "content": "hi"}], temperature=None)
        kwargs = mock_completion.call_args.kwargs
        assert "temperature" not in kwargs

    def test_config_null_temperature_is_stripped(self, mock_completion):
        """Row 5: primary fix -- modelList temperature: null must not leak as None."""
        llm = _make_llm({"temperature": None})
        llm.completion(messages=[{"role": "user", "content": "hi"}], temperature=None)
        kwargs = mock_completion.call_args.kwargs
        assert "temperature" not in kwargs

    def test_caller_temperature_overrides_config_null(self, mock_completion):
        """Row 6: hidden bug fix -- caller temp must not be silently dropped by config null."""
        llm = _make_llm({"temperature": None})
        llm.completion(messages=[{"role": "user", "content": "hi"}], temperature=0.5)
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["temperature"] == 0.5

    def test_persisted_temperature_overrides_caller(self, mock_completion):
        """Row 7: persisted temp wins over caller (PR #698 precedence)."""
        llm = _make_llm({"temperature": 0.7})
        llm.completion(messages=[{"role": "user", "content": "hi"}], temperature=0.5)
        kwargs = mock_completion.call_args.kwargs
        assert kwargs["temperature"] == 0.7

from unittest.mock import patch

import litellm
import pytest

from holmes.core.llm import DefaultLLM

UNPREFIXED_MODEL = "Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8"


def _llm(is_robusta_model: bool = False) -> DefaultLLM:
    # Create instance without __init__, which would call check_llm itself
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.is_robusta_model = is_robusta_model
    return llm


@pytest.fixture(autouse=True)
def _no_openai_base_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


class TestCheckLLMMissingProvider:
    """check_llm explains how to fix a model name without a provider prefix (#2425)."""

    def test_openai_compatible_endpoint_suggests_openai_prefix(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:8000/v1")

        with pytest.raises(Exception) as exc_info:
            _llm().check_llm(UNPREFIXED_MODEL, "none", None, None)

        message = str(exc_info.value)
        assert f"'openai/{UNPREFIXED_MODEL}'" in message
        assert "https://holmesgpt.dev/ai-providers/openai-compatible/" in message
        assert isinstance(exc_info.value.__cause__, litellm.BadRequestError)

    def test_openai_base_url_env_also_suggests_openai_prefix(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8000/v1")

        with pytest.raises(Exception, match="openai-compatible"):
            _llm().check_llm(UNPREFIXED_MODEL, "none", None, None)

    def test_api_base_argument_suggests_openai_prefix(self):
        with pytest.raises(Exception) as exc_info:
            _llm().check_llm(UNPREFIXED_MODEL, "none", "http://localhost:8000/v1", None)

        assert f"'openai/{UNPREFIXED_MODEL}'" in str(exc_info.value)

    def test_without_api_base_points_to_provider_docs(self):
        with pytest.raises(Exception) as exc_info:
            _llm().check_llm(UNPREFIXED_MODEL, None, None, None)

        message = str(exc_info.value)
        assert "provider prefix" in message
        assert "https://holmesgpt.dev/ai-providers/" in message
        assert "openai-compatible" not in message

    def test_prefixed_model_passes(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:8000/v1")

        _llm().check_llm(f"openai/{UNPREFIXED_MODEL}", "none", None, None)

    def test_robusta_model_skips_check(self):
        _llm(is_robusta_model=True).check_llm(UNPREFIXED_MODEL, None, None, None)

    def test_other_lookup_errors_are_not_rewritten(self):
        # litellm wraps any provider-lookup failure (e.g. a GitHub Copilot token
        # error) in BadRequestError; those must surface unchanged, not as
        # "add a provider prefix" advice.
        original = litellm.BadRequestError(
            message="GetLLMProvider Exception - token request failed",
            model="github_copilot/gpt-4o",
            llm_provider="",
        )
        with patch("litellm.get_llm_provider", side_effect=original):
            with pytest.raises(litellm.BadRequestError) as exc_info:
                _llm().check_llm("github_copilot/gpt-4o", None, None, None)

        assert exc_info.value is original

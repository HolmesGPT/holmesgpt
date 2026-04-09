import pytest


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch):
    """
    Remove env vars that affect LLM model loading to ensure predictable model counts.

    Why this is needed:
    LLMModelRegistry._should_load_config_model() in holmes/core/llm.py automatically
    adds a 'gpt-4.1' model when OPENAI_API_KEY is set in the environment (lines 508-512):

        has_openai_key = os.environ.get("OPENAI_API_KEY")
        if has_openai_key:
            self.config.model = "gpt-4.1"
            return True

    This causes tests that assert specific model counts to fail because an extra
    model gets added from the environment. For example, a test expecting 2 models
    from a YAML file would see 3 models (2 from YAML + 1 from env var).
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MODEL", raising=False)
"""
Common test utilities for transformer testing.
"""


def ensure_transformers_registered():
    """
    Ensure required transformers are registered in the global registry.

    This utility handles the case where other tests may have cleared or modified
    the global transformer registry, ensuring that required transformers are
    available for YAML parsing and validation tests.

    Returns:
        registry: The transformer registry instance
    """
    # Import the transformers module to trigger automatic registration
    from holmes.core.transformers import LLMSummarizeTransformer, registry

    # Re-register if missing (some tests may clear the global registry)
    if not registry.is_registered("llm_summarize"):
        registry.register(LLMSummarizeTransformer)

    # Verify registration
    assert registry.is_registered(
        "llm_summarize"
    ), "llm_summarize transformer should be registered for tests"

    return registry

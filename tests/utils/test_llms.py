import pytest

from holmes.utils.llms import model_matches_list


@pytest.mark.parametrize(
    "model,model_list,expected",
    [
        # Exact matches
        ("azure/gpt", ["azure/gpt"], True),
        ("openai/gpt-4o", ["openai/gpt-4o"], True),
        ("anthropic/claude", ["anthropic/claude"], True),
        # No match cases
        ("azure/gpt", ["openai/gpt"], False),
        ("openai/gpt-4o", ["azure/gpt-4o"], False),
        ("mistral/mistral-7b", ["openai/gpt", "azure/gpt"], False),
        # Wildcard at the end
        ("azure/gpt-4o", ["azure/gpt-*"], True),
        ("azure/gpt-3.5-turbo", ["azure/gpt-*"], True),
        ("azure/claude", ["azure/gpt-*"], False),
        # Wildcard at the beginning
        ("azure/mistral", ["*/mistral"], True),
        ("openai/mistral", ["*/mistral"], True),
        ("azure/gpt", ["*/mistral"], False),
        # Wildcard in the middle
        ("azure/prod/gpt", ["azure/*/gpt"], True),
        ("azure/dev/gpt", ["azure/*/gpt"], True),
        ("openai/prod/gpt", ["azure/*/gpt"], False),
        # Full wildcard for provider
        ("azure/anything", ["azure/*"], True),
        ("azure/gpt-4o", ["azure/*"], True),
        ("openai/gpt-4o", ["azure/*"], False),
        # Full wildcard for model name
        ("anything/gpt-4o", ["*/gpt-4o"], True),
        ("azure/gpt-4o", ["*/gpt-4o"], True),
        ("azure/gpt-3.5", ["*/gpt-4o"], False),
        # Multiple patterns - should match any
        ("azure/gpt", ["openai/*", "azure/*"], True),
        ("openai/gpt", ["openai/*", "azure/*"], True),
        ("anthropic/claude", ["openai/*", "azure/*"], False),
        # Complex patterns
        ("azure/prod/gpt-4o", ["azure/*/gpt-*"], True),
        ("azure/staging/gpt-3.5-turbo", ["azure/*/gpt-*"], True),
        ("azure/prod/claude", ["azure/*/gpt-*"], False),
        ("openai/gpt-4o", ["*gpt-4o*"], True),
        ("gpt-4o", ["*gpt-4o*"], True),
        # Question mark wildcard (single character)
        ("azure/gpt4", ["azure/gpt?"], True),
        ("azure/gpt5", ["azure/gpt?"], True),
        ("azure/gpt40", ["azure/gpt?"], False),
        # Empty cases
        ("azure/gpt", [], False),
        ("", ["azure/*"], False),
        ("", [""], True),
        # Character class patterns
        ("azure/gpt4", ["azure/gpt[0-9]"], True),
        ("azure/gpt5", ["azure/gpt[0-9]"], True),
        ("azure/gpta", ["azure/gpt[0-9]"], False),
        # Multiple wildcards
        ("azure/prod/gpt-4o-mini", ["*/*/gpt-*"], True),
        ("openai/staging/gpt-3.5", ["*/*/gpt-*"], True),
        ("azure/claude", ["*/*/gpt-*"], False),
        # Case sensitivity (fnmatch is case-sensitive by default)
        ("Azure/GPT", ["azure/gpt"], False),
        ("azure/gpt", ["Azure/GPT"], False),
        ("Azure/GPT", ["Azure/GPT"], True),
        # Special characters that need escaping in patterns
        ("azure/gpt-4.0", ["azure/gpt-4.0"], True),
        ("azure/gpt-4.0", ["azure/gpt-4*"], True),
        ("model/with(parens)", ["model/with(parens)"], True),
        # Edge case: Pattern matching entire string
        ("azure/gpt", ["*"], True),
        ("anything/at/all", ["*"], True),
        ("", ["*"], True),
    ],
)
def test_model_matches_list(model, model_list, expected):
    """Test model_matches_list function with various patterns and models."""
    result = model_matches_list(model, model_list)
    assert (
        result == expected
    ), f"Expected {expected} for model='{model}' with patterns={model_list}"

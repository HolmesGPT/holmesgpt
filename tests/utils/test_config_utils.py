"""
Tests for configuration utility functions.
"""

from holmes.core.transformers import Transformer
from holmes.utils.config_utils import merge_transformers


def test_merge_transformers_both_none():
    """Test that merging None transformers returns None."""
    result = merge_transformers(None, None)
    assert result is None


def test_merge_transformers_base_none():
    """Test that when base is None, override transformers are returned."""
    override = [Transformer(name="llm_summarize", config={"input_threshold": 1000})]
    result = merge_transformers(None, override)
    assert result == override


def test_merge_transformers_override_none():
    """Test that when override is None, base transformers are returned (default behavior)."""
    base = [Transformer(name="llm_summarize", config={"fast_model": "gpt-4o-mini"})]
    result = merge_transformers(base, None)
    assert result == base


def test_merge_transformers_both_empty():
    """Test that merging empty lists returns None."""
    result = merge_transformers([], [])
    assert result is None


def test_merge_transformers_field_level_merge():
    """Test field-level merging with precedence."""
    base = [
        Transformer(
            name="llm_summarize",
            config={"fast_model": "gpt-4o-mini", "input_threshold": 500},
        )
    ]
    override = [
        Transformer(
            name="llm_summarize", config={"input_threshold": 1000, "prompt": "Custom"}
        )
    ]

    result = merge_transformers(base, override)

    assert len(result) == 1
    assert result[0].name == "llm_summarize"
    assert result[0].config == {
        "fast_model": "gpt-4o-mini",  # From base
        "input_threshold": 1000,  # Override wins
        "prompt": "Custom",  # From override
    }


def test_merge_transformers_different_types():
    """Test merging different transformer types."""
    base = [Transformer(name="llm_summarize", config={"fast_model": "gpt-4o-mini"})]
    override = [Transformer(name="custom_transformer", config={"param": "value"})]

    result = merge_transformers(base, override)

    # Should have both transformers
    result_names = {t.name for t in result}
    assert "llm_summarize" in result_names
    assert "custom_transformer" in result_names
    assert len(result) == 2


def test_merge_transformers_multiple_base():
    """Test merging with multiple transformers in base list."""
    base = [
        Transformer(name="llm_summarize", config={"fast_model": "gpt-4o-mini"}),
        Transformer(name="custom_transformer", config={"param": "base_value"}),
    ]
    override = [Transformer(name="llm_summarize", config={"input_threshold": 1000})]

    result = merge_transformers(base, override)

    # Check that llm_summarize was merged and custom_transformer was preserved
    result_dict = {t.name: t.config for t in result}

    assert result_dict["llm_summarize"]["fast_model"] == "gpt-4o-mini"
    assert result_dict["llm_summarize"]["input_threshold"] == 1000
    assert result_dict["custom_transformer"]["param"] == "base_value"


def test_merge_transformers_override_precedence():
    """Test that override transformers take precedence for existing fields."""
    base = [
        Transformer(
            name="llm_summarize",
            config={
                "fast_model": "gpt-4o-mini",
                "input_threshold": 500,
                "prompt": "Base prompt",
            },
        )
    ]
    override = [
        Transformer(
            name="llm_summarize",
            config={"input_threshold": 2000, "prompt": "Override prompt"},
        )
    ]

    result = merge_transformers(base, override)

    assert len(result) == 1
    assert result[0].name == "llm_summarize"
    assert result[0].config == {
        "fast_model": "gpt-4o-mini",  # From base (not overridden)
        "input_threshold": 2000,  # Override wins
        "prompt": "Override prompt",  # Override wins
    }


def test_merge_transformers_complex_scenario():
    """Test complex merging scenario with multiple types and transformers."""
    base = [
        Transformer(
            name="llm_summarize",
            config={"fast_model": "gpt-4o-mini", "input_threshold": 500},
        ),
        Transformer(name="data_filter", config={"max_items": 100}),
    ]
    override = [
        Transformer(
            name="llm_summarize", config={"input_threshold": 1000, "prompt": "Custom"}
        ),
        Transformer(name="result_formatter", config={"format": "json"}),
    ]

    result = merge_transformers(base, override)

    # Convert to dict for easier assertions
    result_dict = {t.name: t.config for t in result}

    # Check llm_summarize was merged properly
    assert result_dict["llm_summarize"]["fast_model"] == "gpt-4o-mini"  # From base
    assert result_dict["llm_summarize"]["input_threshold"] == 1000  # Override
    assert result_dict["llm_summarize"]["prompt"] == "Custom"  # Override

    # Check other types were preserved
    assert result_dict["data_filter"]["max_items"] == 100
    assert result_dict["result_formatter"]["format"] == "json"

    # Should have 3 transformer types
    assert len(result_dict) == 3


def test_merge_transformers_only_merge_when_override_exists_true():
    """Test that with only_merge_when_override_exists=True, None is returned when no override exists."""
    base = [Transformer(name="llm_summarize", config={"fast_model": "gpt-4o-mini"})]
    result = merge_transformers(base, None, only_merge_when_override_exists=True)
    assert result is None


def test_merge_transformers_only_merge_when_override_exists_false():
    """Test that with only_merge_when_override_exists=False, base transformers are returned."""
    base = [Transformer(name="llm_summarize", config={"fast_model": "gpt-4o-mini"})]
    result = merge_transformers(base, None, only_merge_when_override_exists=False)
    assert result == base


def test_merge_transformers_only_merge_when_override_exists_with_override():
    """Test that the parameter doesn't affect behavior when both base and override exist."""
    base = [Transformer(name="llm_summarize", config={"fast_model": "gpt-4o-mini"})]
    override = [Transformer(name="llm_summarize", config={"prompt": "Custom"})]

    # Should work the same regardless of parameter value when both exist
    result_true = merge_transformers(
        base, override, only_merge_when_override_exists=True
    )
    result_false = merge_transformers(
        base, override, only_merge_when_override_exists=False
    )

    assert result_true == result_false
    assert len(result_true) == 1
    assert result_true[0].config == {"fast_model": "gpt-4o-mini", "prompt": "Custom"}

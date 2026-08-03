"""Token counting must use the model's own tokenizer.

litellm keys its bundled tokenizers on bare model ids, so counting under a routed
name falls back to a generic tokenizer that under-counts for families shipping
their own — making reported context usage smaller than what the provider charges.
Counts move with litellm and its tokenizers, so these compare counts against each
other rather than against absolute numbers.
"""

from unittest.mock import patch

from holmes.core.llm import DefaultLLM


BARE = "claude-sonnet-4-5-20250929"
ROUTED = f"anthropic/{BARE}"
UNRELATED = "proxy/unknown-alias"

MESSAGES = [
    {"role": "system", "content": "You are a troubleshooting assistant. " * 40},
    {"role": "user", "content": "Summarise the failures in the report. " * 40},
]


def _make_llm(model: str) -> DefaultLLM:
    with patch.object(DefaultLLM, "check_llm"):
        return DefaultLLM(model=model, api_key="fake-key")


def _count(model: str):
    return _make_llm(model).count_tokens(messages=[dict(m) for m in MESSAGES])


def test_routed_name_counts_like_the_bare_name():
    assert _count(ROUTED).total_tokens == _count(BARE).total_tokens


def test_family_tokenizer_counts_more_than_the_generic_fallback():
    assert _count(BARE).total_tokens > _count(UNRELATED).total_tokens


def test_unresolvable_name_still_counts():
    usage = _count(UNRELATED)
    assert usage.total_tokens > 0
    assert usage.system_tokens > 0


def test_resolution_is_cached_per_instance():
    llm = _make_llm(ROUTED)
    resolved = llm._get_tokenizer_model_name()
    with patch("holmes.core.llm.litellm.get_llm_provider") as lookup:
        assert llm._get_tokenizer_model_name() == resolved
        lookup.assert_not_called()


def test_lookup_failure_falls_back_to_the_configured_name():
    llm = _make_llm(ROUTED)
    with patch("holmes.core.llm.litellm.get_llm_provider", side_effect=Exception):
        assert llm._get_tokenizer_model_name() == ROUTED

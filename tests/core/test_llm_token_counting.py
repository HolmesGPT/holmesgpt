"""Token counting must use the model's own tokenizer.

litellm matches its bundled tokenizers against bare model ids. A name carrying a
routing prefix, a dotted qualifier or a version suffix silently falls back to its
generic tokenizer, which under-counts for families that ship their own — so the
context usage reported and budgeted against comes out below what the provider
charges. Assertions here compare counts against each other rather than against
absolute numbers, which move with litellm and its bundled tokenizers.
"""

from unittest.mock import patch

import pytest

from holmes.core.llm import (
    DefaultLLM,
    _has_family_tokenizer,
    _tokenizer_name_candidates,
)


BARE = "claude-sonnet-4-5-20250929"
PREFIXED = f"anthropic/{BARE}"
NESTED = f"bedrock/us.anthropic.{BARE}-v1:0"
UNRELATED = "proxy/unknown-alias"

MESSAGES = [
    {"role": "system", "content": "You are a troubleshooting assistant. " * 40},
    {"role": "user", "content": "Summarise the failures in the report. " * 40},
]


def _make_llm(model: str) -> DefaultLLM:
    with patch.object(DefaultLLM, "check_llm"):
        return DefaultLLM(model=model, api_key="fake-key")


# ---------- candidate generation ----------


@pytest.mark.parametrize(
    "model, expected",
    [
        (PREFIXED, BARE),
        (NESTED, BARE),
        (f"vertex_ai/{BARE}", BARE),
        (f"openai/{BARE}", BARE),
        (BARE, BARE),
    ],
    ids=["prefixed", "nested_with_version", "vertex", "openai_compatible", "bare"],
)
def test_candidates_include_the_bare_model_id(model: str, expected: str):
    assert expected in _tokenizer_name_candidates(model)


def test_candidates_start_with_the_name_as_configured():
    """The configured name is tried first, so a model litellm knows under its full
    name keeps that tokenizer."""
    assert _tokenizer_name_candidates(PREFIXED)[0] == PREFIXED


def test_candidates_are_deduplicated():
    candidates = _tokenizer_name_candidates(NESTED)
    assert len(candidates) == len(set(candidates))


# ---------- tokenizer resolution ----------


def test_bare_id_has_its_own_tokenizer():
    """Guards the premise of the whole fix: if this ever stops holding, litellm
    changed how it keys tokenizers and the resolution needs revisiting."""
    assert _has_family_tokenizer(BARE)
    assert not _has_family_tokenizer(UNRELATED)


def test_resolution_is_cached_per_instance():
    llm = _make_llm(PREFIXED)
    first = llm._get_tokenizer_model_name()
    with patch("holmes.core.llm._has_family_tokenizer") as probe:
        assert llm._get_tokenizer_model_name() == first
        probe.assert_not_called()


def test_unresolvable_name_falls_back_to_the_configured_name():
    llm = _make_llm(UNRELATED)
    assert llm._get_tokenizer_model_name() == UNRELATED


def test_probe_failure_falls_back_to_the_configured_name():
    """litellm's tokenizer selection is not public API; if it moves or raises,
    counting continues under the configured name."""
    llm = _make_llm(PREFIXED)
    with patch("holmes.core.llm._litellm_select_tokenizer", None):
        assert llm._get_tokenizer_model_name() == PREFIXED


# ---------- count_tokens ----------


def test_prefixed_name_counts_like_the_bare_name():
    prefixed = _make_llm(PREFIXED).count_tokens(messages=[dict(m) for m in MESSAGES])
    bare = _make_llm(BARE).count_tokens(messages=[dict(m) for m in MESSAGES])
    assert prefixed.total_tokens == bare.total_tokens
    assert prefixed.system_tokens == bare.system_tokens
    assert prefixed.user_tokens == bare.user_tokens


def test_nested_provider_and_version_name_counts_like_the_bare_name():
    nested = _make_llm(NESTED).count_tokens(messages=[dict(m) for m in MESSAGES])
    bare = _make_llm(BARE).count_tokens(messages=[dict(m) for m in MESSAGES])
    assert nested.total_tokens == bare.total_tokens


def test_family_tokenizer_counts_more_than_the_generic_fallback():
    """The undercount this fixes: the generic tokenizer reports fewer tokens for
    the same messages than the family's own."""
    family = _make_llm(BARE).count_tokens(messages=[dict(m) for m in MESSAGES])
    generic = _make_llm(UNRELATED).count_tokens(messages=[dict(m) for m in MESSAGES])
    assert family.total_tokens > generic.total_tokens


def test_unknown_model_still_counts():
    usage = _make_llm(UNRELATED).count_tokens(messages=[dict(m) for m in MESSAGES])
    assert usage.total_tokens > 0
    assert usage.system_tokens > 0
    assert usage.user_tokens > 0

"""ExecutorSizing: the one place account sizes are validated and clamped (ROB-1369)."""

import logging

import pytest

from holmes.core.conversations_worker.sizing import THREAD_CEILING, ExecutorSizing


def _sizing(base=5, ceiling=64):
    return ExecutorSizing(base_size=base, thread_ceiling=ceiling)


def test_builtin_defaults_manual_10_auto_2_others_base():
    sizing = _sizing(base=5)
    assert sizing.size_for("manual") == 10
    assert sizing.size_for("auto") == 2
    assert sizing.size_for("nightly") == 5


def test_lookup_order_account_then_builtin_then_base():
    sizing = _sizing(base=5)
    # 1. account setting wins
    assert sizing.size_for("manual", {"manual": 12}) == 12
    assert sizing.size_for("nightly", {"nightly": 3}) == 3
    # 2. built-in default when the account says nothing about the name
    assert sizing.size_for("manual", {"auto": 1}) == 10
    assert sizing.size_for("auto") == 2
    # 3. base size for unknown names
    assert sizing.size_for("other") == 5


@pytest.mark.parametrize("bad", [0, -1, "x", None, True, 2.5, {"n": 1}])
def test_invalid_account_setting_falls_through_with_warning_once(bad, caplog):
    sizing = _sizing()
    with caplog.at_level(logging.WARNING):
        assert sizing.size_for("manual", {"manual": bad}) == 10
        assert sizing.size_for("manual", {"manual": bad}) == 10
    hits = [r for r in caplog.records if "conversation_executors" in r.getMessage()]
    assert len(hits) == 1 and hits[0].levelno == logging.WARNING


def test_account_setting_accepts_numeric_strings():
    assert _sizing().size_for("auto", {"auto": "4"}) == 4


def test_sizes_are_capped_at_the_thread_ceiling():
    assert _sizing(ceiling=8).size_for("manual", {"manual": 5000}) == 8


def test_sizes_are_never_below_one():
    sizing = ExecutorSizing(base_size=0, thread_ceiling=0)
    assert sizing.base_size == 1
    assert sizing.thread_ceiling == 1


def test_malformed_account_setting_shape_is_ignored_with_one_warning(caplog):
    """The DAL returns the jsonb as stored; a non-object value falls through to
    the built-ins and is warned about once."""
    sizing = _sizing()
    with caplog.at_level(logging.WARNING):
        assert sizing.size_for("manual", [1, 2]) == 10
        assert sizing.size_for("auto", "nope") == 2
    hits = [r for r in caplog.records if "malformed" in r.getMessage()]
    assert len(hits) == 1


def test_defaults_match_the_module_constants():
    sizing = ExecutorSizing()
    assert sizing.thread_ceiling == THREAD_CEILING
    assert sizing.size_for("manual") == 10 and sizing.size_for("auto") == 2

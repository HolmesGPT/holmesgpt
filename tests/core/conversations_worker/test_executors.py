"""Unit tests for executor pool sizing and name validation (ROB-1369)."""

import logging

import pytest

from holmes.core.conversations_worker.executors import (
    ConversationExecutor,
    ExecutorSettings,
    is_valid_executor_name,
)


def _settings(env=None, base=5, ceiling=64):
    return ExecutorSettings(
        base_size=base, max_executors=16, thread_ceiling=ceiling, env=env or {}
    )


def test_builtin_defaults_manual_10_auto_2_others_base():
    s = _settings(base=5)
    assert s.size_for("manual") == 10
    assert s.size_for("auto") == 2
    assert s.size_for("nightly") == 5


def test_lookup_order_account_then_env_then_builtin_then_base():
    env = {
        "CONVERSATION_WORKER_MAX_CONCURRENT_MANUAL": "7",
        "CONVERSATION_WORKER_MAX_CONCURRENT_NIGHTLY": "3",
    }
    s = _settings(env=env, base=5)
    # 1. account setting wins over everything
    assert s.size_for("manual", {"manual": 12}) == 12
    # 2. per-name env var beats the built-in default
    assert s.size_for("manual") == 7
    assert s.size_for("manual", {"auto": 1}) == 7
    # 2. per-name env var for a non-built-in name beats the base size
    assert s.size_for("nightly") == 3
    # 3. built-in default when no env var
    assert s.size_for("auto") == 2
    # 4. base size for unknown names
    assert s.size_for("other") == 5


@pytest.mark.parametrize("bad", ["0", "-2", "x", "", "1.5"])
def test_invalid_per_name_env_is_ignored_with_warning_once(bad, caplog):
    s = _settings(env={"CONVERSATION_WORKER_MAX_CONCURRENT_AUTO": bad})
    with caplog.at_level(logging.WARNING):
        assert s.size_for("auto") == 2
        assert s.size_for("auto") == 2
    hits = [
        r
        for r in caplog.records
        if "CONVERSATION_WORKER_MAX_CONCURRENT_AUTO" in r.getMessage()
    ]
    assert len(hits) == 1 and hits[0].levelno == logging.WARNING


@pytest.mark.parametrize("bad", [0, -1, "x", None, True, 2.5, {"n": 1}])
def test_invalid_account_setting_falls_through(bad, caplog):
    s = _settings(env={"CONVERSATION_WORKER_MAX_CONCURRENT_MANUAL": "7"})
    with caplog.at_level(logging.WARNING):
        assert s.size_for("manual", {"manual": bad}) == 7
    assert any("conversation_executors" in r.getMessage() for r in caplog.records)


def test_account_setting_accepts_numeric_strings():
    assert _settings().size_for("auto", {"auto": "4"}) == 4


def test_sizes_are_never_below_one():
    s = ExecutorSettings(base_size=0, max_executors=0, thread_ceiling=0, env={})
    assert s.base_size == 1 and s.max_executors == 1 and s.thread_ceiling == 1


def test_from_env_reads_process_environment(monkeypatch):
    monkeypatch.setenv("CONVERSATION_WORKER_MAX_CONCURRENT_AUTO", "9")
    assert ExecutorSettings.from_env().size_for("auto") == 9


def test_set_max_concurrent_is_live_and_capped():
    ex = ConversationExecutor("manual", 2, thread_ceiling=8)
    ex.notify_event.clear()
    assert ex.set_max_concurrent(5) is True
    assert ex.max_concurrent == 5 and ex.free_slots() == 5
    assert ex.notify_event.is_set()  # new slots → re-claim
    assert ex.set_max_concurrent(5) is False  # unchanged → no wake
    assert ex.set_max_concurrent(100) is True
    assert ex.max_concurrent == 8  # never above the pool's thread ceiling
    assert ex.set_max_concurrent(0) is True
    assert ex.max_concurrent == 1


def test_pool_is_created_with_the_thread_ceiling():
    ex = ConversationExecutor("auto", 2, thread_ceiling=16)
    ex.start(lambda _e: None)
    try:
        assert ex._pool._max_workers == 16
        assert ex.max_concurrent == 2
    finally:
        ex.stop()


@pytest.mark.parametrize("name", ["manual", "auto", "a", "nightly-report_2", "x" * 64])
def test_valid_executor_names(name):
    assert is_valid_executor_name(name)


@pytest.mark.parametrize(
    "name", ["", "-lead", "Upper", "has space", "x" * 65, None, 3, "a/b"]
)
def test_invalid_executor_names(name):
    assert not is_valid_executor_name(name)

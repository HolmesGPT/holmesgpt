"""Executor pool sizing, executor-name rule and task construction (ROB-1369)."""

import logging

import pytest

from holmes.core.conversations_worker.models import (
    ConversationTask,
    is_valid_executor_name,
)
from holmes.core.conversations_worker.sizing import ExecutorSizing


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


@pytest.mark.parametrize("name", ["manual", "auto", "a", "nightly-report_2", "x" * 64])
def test_valid_executor_names(name):
    assert is_valid_executor_name(name)


@pytest.mark.parametrize(
    "name", ["", "-lead", "Upper", "has space", "x" * 65, None, 3, "a/b", "manual\n"]
)
def test_invalid_executor_names(name):
    assert not is_valid_executor_name(name)


# ---- ConversationTask.from_row ----


def _row(**overrides):
    row = {
        "conversation_id": "c1",
        "account_id": "a1",
        "cluster_id": "cl1",
        "origin": "chat",
        "request_sequence": 3,
        "metadata": {"k": "v"},
        "title": "t",
        "user_id": "u1",
        "executor": "auto",
    }
    row.update(overrides)
    return row


def test_from_row_parses_required_fields():
    task = ConversationTask.from_row(_row())
    assert task is not None
    assert (task.conversation_id, task.account_id, task.cluster_id) == (
        "c1",
        "a1",
        "cl1",
    )
    assert task.request_sequence == 3
    assert task.metadata == {"k": "v"}
    assert task.title == "t"
    assert task.user_id == "u1"
    assert task.executor == "auto"


def test_from_row_tolerates_missing_optional_fields():
    task = ConversationTask.from_row(
        {"conversation_id": "c1", "account_id": "a1", "cluster_id": "cl1"}
    )
    assert task is not None
    assert task.origin == "chat"
    assert task.request_sequence == 1
    assert task.metadata == {}
    assert task.executor == "manual"


def test_from_row_claiming_executor_wins_over_the_column():
    task = ConversationTask.from_row(_row(executor="auto"), "manual")
    assert task is not None and task.executor == "manual"


def test_from_row_returns_none_on_bad_input():
    assert ConversationTask.from_row({"conversation_id": "c1"}) is None
    assert ConversationTask.from_row(_row(request_sequence="not-a-number")) is None

"""Executor-name rule, ConversationTask.from_row and row identity (ROB-1369)."""

import pytest

from holmes.core.conversations_worker.models import (
    ConversationTask,
    conversation_identity,
    is_valid_executor_name,
)


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


def test_from_row_reads_the_executor_from_the_row_only():
    assert ConversationTask.from_row(_row(executor="auto")).executor == "auto"


def test_from_row_returns_none_on_bad_input():
    assert ConversationTask.from_row({"conversation_id": "c1"}) is None
    assert ConversationTask.from_row(_row(request_sequence="not-a-number")) is None
    assert ConversationTask.from_row({"account_id": "a1", "cluster_id": "c"}) is None


def test_conversation_identity():
    assert conversation_identity({"conversation_id": "c1", "request_sequence": 2}) == (
        "c1",
        2,
    )
    assert conversation_identity({"conversation_id": "c1"}) == ("c1", 1)
    assert conversation_identity({"request_sequence": 2}) is None
    assert (
        conversation_identity({"conversation_id": "c1", "request_sequence": "x"})
        is None
    )

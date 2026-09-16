"""Unit tests for ExecutorSettings parsing and executor name validation."""

import logging

import pytest

from holmes.core.conversations_worker import executors as ex_mod
from holmes.core.conversations_worker.executors import (
    ExecutorSettings,
    is_valid_executor_name,
)


def _settings(monkeypatch, raw, manual=5, auto=3, default=2, cap=16):
    monkeypatch.setattr(ex_mod, "CONVERSATION_WORKER_EXECUTORS", raw)
    monkeypatch.setattr(ex_mod, "CONVERSATION_WORKER_MAX_CONCURRENT", manual)
    monkeypatch.setattr(ex_mod, "CONVERSATION_WORKER_AUTO_MAX_CONCURRENT", auto)
    monkeypatch.setattr(
        ex_mod, "CONVERSATION_WORKER_DEFAULT_EXECUTOR_MAX_CONCURRENT", default
    )
    monkeypatch.setattr(ex_mod, "CONVERSATION_WORKER_MAX_EXECUTORS", cap)
    return ExecutorSettings.from_env()


def test_defaults_keep_manual_on_legacy_max_concurrent(monkeypatch):
    s = _settings(monkeypatch, "", manual=7, auto=4, default=2)
    assert s.sizes == {"manual": 7, "auto": 4}
    assert s.size_for("manual") == 7
    assert s.size_for("auto") == 4
    assert s.size_for("anything-else") == 2
    assert s.max_executors == 16


def test_json_map_overrides_and_extends_defaults(monkeypatch):
    s = _settings(monkeypatch, '{"manual": 8, "auto": 1, "nightly": 4}')
    assert s.sizes == {"manual": 8, "auto": 1, "nightly": 4}


@pytest.mark.parametrize("raw", ["not json", "[1,2]", "42", '"str"'])
def test_invalid_json_is_ignored_with_error_log(monkeypatch, caplog, raw):
    with caplog.at_level(logging.ERROR):
        s = _settings(monkeypatch, raw)
    assert s.sizes == {"manual": 5, "auto": 3}
    assert any(
        "CONVERSATION_WORKER_EXECUTORS" in r.getMessage() for r in caplog.records
    )


def test_invalid_entries_are_skipped_individually(monkeypatch, caplog):
    with caplog.at_level(logging.ERROR):
        s = _settings(
            monkeypatch,
            '{"auto": 0, "Bad Name": 3, "ok": "4", "neg": -1, "nan": "x"}',
        )
    # auto keeps its default (0 rejected); "ok" accepts a numeric string.
    assert s.sizes == {"manual": 5, "auto": 3, "ok": 4}
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 4


def test_sizes_are_never_below_one():
    s = ExecutorSettings({}, default_size=0, max_executors=0)
    assert s.default_size == 1 and s.max_executors == 1


@pytest.mark.parametrize("name", ["manual", "auto", "a", "nightly-report_2", "x" * 64])
def test_valid_executor_names(name):
    assert is_valid_executor_name(name)


@pytest.mark.parametrize(
    "name", ["", "-lead", "Upper", "has space", "x" * 65, None, 3, "a/b"]
)
def test_invalid_executor_names(name):
    assert not is_valid_executor_name(name)

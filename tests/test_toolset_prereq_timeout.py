"""Tests for the per-toolset prerequisite timeout in ToolsetManager."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Tuple

import pytest

from holmes.core import toolset_manager as tm
from holmes.core.init_event import StatusEvent, StatusEventKind, ToolsetStatus
from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    Tool,
    Toolset,
    ToolsetStatusEnum,
)
from holmes.core.toolset_manager import ToolsetManager


class _NoopTool(Tool):
    name: str = "noop"
    description: str = "noop"

    def _invoke(self, params: dict, user_approved: bool = False) -> StructuredToolResult:  # noqa: D401
        return None  # type: ignore[return-value]

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return ""


class _SampleToolset(Toolset):
    name: str = "sample"
    description: str = "sample"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tools: List[Tool] = [_NoopTool()]


def _make_toolset(name: str, prereq_callable) -> Toolset:
    ts = _SampleToolset(
        prerequisites=[CallablePrerequisite(callable=prereq_callable)],
        config={},
    )
    ts.name = name
    return ts


def test_check_toolset_prerequisites_marks_slow_toolsets_failed():
    """Toolsets that exceed the timeout are marked FAILED with a clear error."""
    release = threading.Event()

    def slow_callable(_config: Dict[str, Any]) -> Tuple[bool, str]:
        # Block far longer than the test timeout. The executor is shut down
        # without waiting, so we release the gate at end of test.
        release.wait(timeout=10)
        return True, ""

    def fast_callable(_config: Dict[str, Any]) -> Tuple[bool, str]:
        return True, ""

    slow = _make_toolset("slow_ds", slow_callable)
    fast = _make_toolset("fast_ds", fast_callable)

    events: List[StatusEvent] = []

    try:
        start = time.monotonic()
        ToolsetManager.check_toolset_prerequisites(
            [slow, fast],
            silent=True,
            on_event=events.append,
            timeout_seconds=0.5,
        )
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"timeout was not enforced (took {elapsed:.2f}s)"

        assert fast.status == ToolsetStatusEnum.ENABLED
        assert slow.status == ToolsetStatusEnum.FAILED
        assert "0.5s" in (slow.error or "")
        assert "HOLMES_TOOLSET_PREREQ_TIMEOUT_SECONDS" in (slow.error or "")

        ready_events = {e.name: e for e in events if e.kind == StatusEventKind.TOOLSET_READY}
        assert ready_events["fast_ds"].status == ToolsetStatus.ENABLED
        assert ready_events["slow_ds"].status == ToolsetStatus.FAILED
    finally:
        release.set()


def test_check_toolset_prerequisites_no_timeout_when_all_fast():
    """All toolsets complete normally when none exceed the timeout."""

    def fast_callable(_config: Dict[str, Any]) -> Tuple[bool, str]:
        return True, ""

    a = _make_toolset("a", fast_callable)
    b = _make_toolset("b", fast_callable)

    events: List[StatusEvent] = []
    ToolsetManager.check_toolset_prerequisites(
        [a, b],
        silent=True,
        on_event=events.append,
        timeout_seconds=5.0,
    )

    assert a.status == ToolsetStatusEnum.ENABLED
    assert b.status == ToolsetStatusEnum.ENABLED

    ready_events = [e for e in events if e.kind == StatusEventKind.TOOLSET_READY]
    assert {e.name for e in ready_events} == {"a", "b"}
    assert all(e.status == ToolsetStatus.ENABLED for e in ready_events)


def test_late_completion_does_not_overwrite_failed_status():
    """A worker that finishes after the timeout must not flip the toolset back to ENABLED."""
    started = threading.Event()
    release = threading.Event()

    def slow_then_succeed(_config: Dict[str, Any]) -> Tuple[bool, str]:
        started.set()
        # Wait for the timeout handler to mark us FAILED and set _prereq_aborted.
        release.wait(timeout=5)
        return True, ""

    ts = _make_toolset("eventually_ok", slow_then_succeed)

    try:
        ToolsetManager.check_toolset_prerequisites(
            [ts],
            silent=True,
            timeout_seconds=0.3,
        )
        # The worker is still running; let it finish so it tries to commit.
        assert started.wait(timeout=2)
        assert ts.status == ToolsetStatusEnum.FAILED
        release.set()
        # Give the worker a moment to attempt its commit.
        time.sleep(0.2)
        # Status must remain FAILED; the abort guard blocks the late write.
        assert ts.status == ToolsetStatusEnum.FAILED
        assert ts.error and "did not complete" in ts.error
    finally:
        release.set()


def test_default_timeout_picked_up_from_env(monkeypatch):
    """The timeout default is read from HOLMES_TOOLSET_PREREQ_TIMEOUT_SECONDS."""
    monkeypatch.setenv("HOLMES_TOOLSET_PREREQ_TIMEOUT_SECONDS", "7.5")
    assert tm._get_prereq_timeout_seconds() == pytest.approx(7.5)

    monkeypatch.setenv("HOLMES_TOOLSET_PREREQ_TIMEOUT_SECONDS", "not-a-number")
    assert tm._get_prereq_timeout_seconds() == tm.DEFAULT_TOOLSET_PREREQ_TIMEOUT_SECONDS

    monkeypatch.setenv("HOLMES_TOOLSET_PREREQ_TIMEOUT_SECONDS", "0")
    assert tm._get_prereq_timeout_seconds() == tm.DEFAULT_TOOLSET_PREREQ_TIMEOUT_SECONDS

    monkeypatch.delenv("HOLMES_TOOLSET_PREREQ_TIMEOUT_SECONDS", raising=False)
    assert tm._get_prereq_timeout_seconds() == tm.DEFAULT_TOOLSET_PREREQ_TIMEOUT_SECONDS

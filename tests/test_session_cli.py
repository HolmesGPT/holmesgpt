"""Tests for the --continue / --resume wiring in the ask CLI and interactive loop."""

import os
import time
from unittest.mock import Mock

import pytest
import typer

from holmes.interactive import persist_session
from holmes.main import _resolve_session_to_resume
from holmes.utils.sessions import ChatSession, SessionManager, derive_title


def _make_session(manager: SessionManager, prompt: str) -> ChatSession:
    session = ChatSession(
        session_id=manager.new_session_id(),
        title=derive_title([{"role": "user", "content": prompt}]),
        working_directory=os.getcwd(),
        messages=[{"role": "user", "content": prompt}],
    )
    manager.save(session)
    return session


class TestResolveSessionToResume:
    def test_continue_returns_latest(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        _make_session(manager, "old question")
        time.sleep(0.01)
        newest = _make_session(manager, "new question")

        resolved = _resolve_session_to_resume(
            manager=manager,
            console=Mock(),
            continue_session=True,
            resume_session=False,
            session_id=None,
        )

        assert resolved is not None
        assert resolved.session_id == newest.session_id

    def test_session_id_returns_specific_session(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        _make_session(manager, "first")
        target = _make_session(manager, "second")

        resolved = _resolve_session_to_resume(
            manager=manager,
            console=Mock(),
            continue_session=False,
            resume_session=False,
            session_id=target.session_id,
        )

        assert resolved is not None
        assert resolved.session_id == target.session_id

    def test_no_flags_returns_none(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        resolved = _resolve_session_to_resume(
            manager=manager,
            console=Mock(),
            continue_session=False,
            resume_session=False,
            session_id=None,
        )
        assert resolved is None

    def test_mutually_exclusive_flags_raise(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        with pytest.raises(typer.BadParameter):
            _resolve_session_to_resume(
                manager=manager,
                console=Mock(),
                continue_session=True,
                resume_session=True,
                session_id=None,
            )

    def test_continue_with_no_sessions_raises(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path / "empty"))
        with pytest.raises(typer.BadParameter):
            _resolve_session_to_resume(
                manager=manager,
                console=Mock(),
                continue_session=True,
                resume_session=False,
                session_id=None,
            )

    def test_unknown_session_id_raises(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        with pytest.raises(typer.BadParameter):
            _resolve_session_to_resume(
                manager=manager,
                console=Mock(),
                continue_session=False,
                resume_session=False,
                session_id="missing",
            )


class TestPersistSession:
    def test_round_trip_with_tool_calls(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        session_id = SessionManager.new_session_id()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "why is the db slow?"},
            {"role": "assistant", "content": "checking"},
        ]

        class _ToolCall:
            def model_dump(self, mode=None):
                return {"tool_name": "kubectl_logs", "result": "ok"}

        persist_session(
            manager,
            session_id,
            messages,
            [_ToolCall()],
            "anthropic/claude-sonnet-4-5-20250929",
        )

        loaded = manager.load(session_id)
        assert loaded.title == "why is the db slow?"
        assert loaded.model == "anthropic/claude-sonnet-4-5-20250929"
        assert loaded.messages == messages
        assert loaded.tool_calls == [{"tool_name": "kubectl_logs", "result": "ok"}]
        assert loaded.working_directory == os.getcwd()
        assert loaded.metadata == {"session_type": "interactive"}

    def test_tolerates_unserializable_tool_calls(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        session_id = SessionManager.new_session_id()

        class _BadToolCall:
            def model_dump(self, mode=None):
                raise RuntimeError("cannot serialize")

        persist_session(
            manager,
            session_id,
            [{"role": "user", "content": "hi"}],
            [_BadToolCall()],
            None,
        )

        loaded = manager.load(session_id)
        assert loaded.tool_calls == []
        assert loaded.messages == [{"role": "user", "content": "hi"}]

    def test_persistence_can_be_disabled_via_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOLMES_DISABLE_SESSION_PERSISTENCE", "true")
        manager = SessionManager(sessions_dir=str(tmp_path))
        session_id = SessionManager.new_session_id()

        persist_session(
            manager,
            session_id,
            [{"role": "user", "content": "secret tool output"}],
            [],
            None,
        )

        # Nothing was written to disk.
        assert manager.list_sessions() == []

"""Tests for the --continue wiring in the ask CLI and the session save/load helpers."""

import os
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from holmes.core.tool_calling_llm import LLMResult
from holmes.interactive import deserialize_tool_calls, persist_session
from holmes.main import app
from holmes.utils.sessions import ChatSession, SessionManager, derive_title

runner = CliRunner()


def _tool_call_dict(tool_call_id="call_1", tool_name="kubectl_get"):
    """A serialized ToolCallResult as it would appear in a saved session."""
    return {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "description": f"{tool_name} pods",
        "result": {"status": "success", "data": "pod1 Running", "error": None},
    }


def _make_session(manager: SessionManager, prompt: str) -> ChatSession:
    session = ChatSession(
        session_id=manager.new_session_id(),
        title=derive_title([{"role": "user", "content": prompt}]),
        working_directory=os.getcwd(),
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "prior answer"},
        ],
    )
    manager.save(session)
    return session


class TestAskContinue:
    """End-to-end coverage of `holmes ask --continue` in non-interactive mode."""

    @patch("holmes.config.Config.create_toolcalling_llm")
    def test_continue_reuses_latest_session_history(
        self, mock_create_toolcalling_llm, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("holmes.utils.sessions.config_path_dir", str(tmp_path))
        manager = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        prior = _make_session(manager, "why is my pod crashing?")

        mock_ai = MagicMock()
        mock_ai.llm.model = "gpt-4o"
        mock_ai.call.return_value = LLMResult(
            result="it recovered",
            tool_calls=[],
            messages=prior.messages
            + [
                {"role": "user", "content": "did the fix work?"},
                {"role": "assistant", "content": "it recovered"},
            ],
        )
        mock_create_toolcalling_llm.return_value = mock_ai

        result = runner.invoke(
            app, ["ask", "--continue", "did the fix work?", "--no-interactive"]
        )

        assert result.exit_code == 0, f"CLI failed with output: {result.output}"
        # The LLM got the prior conversation plus the new question, not a fresh
        # system prompt.
        sent_messages = mock_ai.call.call_args[0][0]
        assert sent_messages[0] == {"role": "system", "content": "sys"}
        assert sent_messages[-1] == {"role": "user", "content": "did the fix work?"}

        # The same session file was updated in place.
        sessions = manager.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].session_id == prior.session_id
        assert sessions[0].user_turns == 2

    @patch("holmes.config.Config.create_toolcalling_llm")
    def test_continue_attaches_files_to_the_new_question(
        self, mock_create_toolcalling_llm, tmp_path, monkeypatch
    ):
        """--file must reach the LLM when continuing, not just print a message."""
        monkeypatch.setattr("holmes.utils.sessions.config_path_dir", str(tmp_path))
        manager = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        _make_session(manager, "why is my pod crashing?")
        log_file = tmp_path / "error.log"
        log_file.write_text("OOMKilled at 03:14")

        mock_ai = MagicMock()
        mock_ai.llm.model = "gpt-4o"
        mock_ai.call.return_value = LLMResult(result="ok", tool_calls=[], messages=[])
        mock_create_toolcalling_llm.return_value = mock_ai

        result = runner.invoke(
            app,
            [
                "ask",
                "-c",
                "what does this say?",
                "-f",
                str(log_file),
                "--no-interactive",
            ],
        )

        assert result.exit_code == 0, f"CLI failed with output: {result.output}"
        last_message = mock_ai.call.call_args[0][0][-1]
        assert "OOMKilled at 03:14" in last_message["content"]

    @patch("holmes.config.Config.create_toolcalling_llm")
    def test_continue_without_any_session_fails(
        self, mock_create_toolcalling_llm, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("holmes.utils.sessions.config_path_dir", str(tmp_path))

        result = runner.invoke(app, ["ask", "-c", "anything", "--no-interactive"])

        assert result.exit_code != 0
        assert "No previous sessions found" in result.output
        mock_create_toolcalling_llm.assert_not_called()

    @patch("holmes.config.Config.create_toolcalling_llm")
    def test_ask_without_continue_starts_a_new_session(
        self, mock_create_toolcalling_llm, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("holmes.utils.sessions.config_path_dir", str(tmp_path))
        manager = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        prior = _make_session(manager, "an old question")

        mock_ai = MagicMock()
        mock_ai.llm.model = "gpt-4o"
        mock_ai.call.return_value = LLMResult(
            result="answer",
            tool_calls=[],
            messages=[
                {"role": "system", "content": "fresh sys"},
                {"role": "user", "content": "a new question"},
                {"role": "assistant", "content": "answer"},
            ],
        )
        mock_create_toolcalling_llm.return_value = mock_ai

        result = runner.invoke(app, ["ask", "a new question", "--no-interactive"])

        assert result.exit_code == 0, f"CLI failed with output: {result.output}"
        session_ids = {s.session_id for s in manager.list_sessions()}
        assert len(session_ids) == 2
        assert prior.session_id in session_ids


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

        loaded = manager.latest()
        assert loaded is not None
        assert loaded.session_id == session_id
        assert loaded.title == "why is the db slow?"
        assert loaded.model == "anthropic/claude-sonnet-4-5-20250929"
        assert loaded.messages == messages
        assert loaded.tool_calls == [{"tool_name": "kubectl_logs", "result": "ok"}]
        assert loaded.working_directory == os.getcwd()
        assert loaded.metadata == {"session_type": "interactive"}

    def test_unserializable_tool_call_only_drops_itself(self, tmp_path):
        """A single bad tool call is skipped without discarding the good ones."""
        manager = SessionManager(sessions_dir=str(tmp_path))
        session_id = SessionManager.new_session_id()

        class _GoodToolCall:
            def model_dump(self, mode=None):
                return {"tool_name": "good"}

        class _BadToolCall:
            def model_dump(self, mode=None):
                raise RuntimeError("cannot serialize")

        persist_session(
            manager,
            session_id,
            [{"role": "user", "content": "hi"}],
            [_GoodToolCall(), _BadToolCall(), _GoodToolCall()],
            None,
        )

        loaded = manager.latest()
        assert loaded is not None
        assert loaded.tool_calls == [{"tool_name": "good"}, {"tool_name": "good"}]
        assert loaded.messages == [{"role": "user", "content": "hi"}]

    def test_persistence_can_be_disabled_via_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOLMES_DISABLE_SESSION_PERSISTENCE", "true")
        manager = SessionManager(sessions_dir=str(tmp_path))

        persist_session(
            manager,
            SessionManager.new_session_id(),
            [{"role": "user", "content": "secret tool output"}],
            [],
            None,
        )

        # Nothing was written to disk.
        assert manager.list_sessions() == []


class TestDeserializeToolCalls:
    def test_round_trips_saved_tool_calls(self):
        restored = deserialize_tool_calls([_tool_call_dict("a"), _tool_call_dict("b")])
        assert [tc.tool_call_id for tc in restored] == ["a", "b"]
        assert restored[0].result.data == "pod1 Running"
        # Restored objects must be real ToolCallResult instances usable by the UI
        # (e.g. they expose .description and can be re-serialized).
        assert restored[0].description == "kubectl_get pods"
        assert restored[0].model_dump(mode="json")["tool_call_id"] == "a"

    def test_skips_malformed_entries(self):
        restored = deserialize_tool_calls(
            [_tool_call_dict("ok"), {"garbage": True}, None]
        )
        assert [tc.tool_call_id for tc in restored] == ["ok"]

    def test_empty_input(self):
        assert deserialize_tool_calls([]) == []
        assert deserialize_tool_calls(None) == []  # type: ignore[arg-type]

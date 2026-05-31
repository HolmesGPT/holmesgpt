"""Unit tests for holmes.utils.sessions (local --continue/--resume storage)."""

import os
import time

import pytest

from holmes.utils.sessions import (
    ChatSession,
    SessionManager,
    SessionNotFoundError,
    derive_title,
)


def _make_session(manager: SessionManager, prompt: str, **kwargs) -> ChatSession:
    session = ChatSession(
        session_id=manager.new_session_id(),
        title=derive_title([{"role": "user", "content": prompt}]),
        working_directory=os.getcwd(),
        model="anthropic/claude-sonnet-4-5-20250929",
        messages=[
            {"role": "system", "content": "you are holmes"},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "answer"},
        ],
        **kwargs,
    )
    manager.save(session)
    return session


class TestSessionManager:
    def test_save_and_load_round_trip(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        saved = _make_session(manager, "why is my pod crashing?")

        loaded = manager.load(saved.session_id)

        assert loaded.session_id == saved.session_id
        assert loaded.title == "why is my pod crashing?"
        assert loaded.model == "anthropic/claude-sonnet-4-5-20250929"
        assert loaded.messages == saved.messages
        assert loaded.user_turns == 1
        assert loaded.message_count == 3

    def test_load_missing_raises(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        with pytest.raises(SessionNotFoundError):
            manager.load("does-not-exist")

    def test_list_sessions_ordered_most_recent_first(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        first = _make_session(manager, "first question")
        time.sleep(0.01)
        second = _make_session(manager, "second question")
        time.sleep(0.01)
        third = _make_session(manager, "third question")

        listed = manager.list_sessions()

        assert [s.session_id for s in listed] == [
            third.session_id,
            second.session_id,
            first.session_id,
        ]

    def test_latest_returns_most_recently_updated(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        _make_session(manager, "old")
        time.sleep(0.01)
        newest = _make_session(manager, "new")

        latest = manager.latest()

        assert latest is not None
        assert latest.session_id == newest.session_id

    def test_latest_none_when_empty(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path / "empty"))
        assert manager.latest() is None
        assert manager.list_sessions() == []

    def test_save_updates_updated_at_and_persists_in_place(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        session = _make_session(manager, "hello")
        first_updated_at = manager.load(session.session_id).updated_at

        time.sleep(0.01)
        session.messages.append({"role": "user", "content": "follow up"})
        manager.save(session)

        reloaded = manager.load(session.session_id)
        assert reloaded.updated_at > first_updated_at
        assert reloaded.user_turns == 2
        # Updating an existing session must not create a second file.
        assert len(manager.list_sessions()) == 1

    def test_atomic_save_leaves_no_temp_files(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        _make_session(manager, "tidy up")
        leftovers = [n for n in os.listdir(tmp_path) if not n.endswith(".json")]
        assert leftovers == []

    def test_list_skips_corrupt_files(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        good = _make_session(manager, "valid session")
        (tmp_path / "broken.json").write_text("{not valid json")

        listed = manager.list_sessions()

        assert [s.session_id for s in listed] == [good.session_id]

    def test_delete_removes_session(self, tmp_path):
        manager = SessionManager(sessions_dir=str(tmp_path))
        session = _make_session(manager, "delete me")
        manager.delete(session.session_id)
        assert manager.list_sessions() == []
        # Deleting a non-existent session is a no-op, not an error.
        manager.delete("missing")


class TestDeriveTitle:
    def test_uses_first_user_message(self):
        title = derive_title(
            [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "what is broken?"},
                {"role": "assistant", "content": "let me check"},
            ]
        )
        assert title == "what is broken?"

    def test_collapses_whitespace(self):
        assert derive_title([{"role": "user", "content": "  a\n\n  b\tc "}]) == "a b c"

    def test_truncates_long_titles(self):
        long_prompt = "word " * 50
        title = derive_title([{"role": "user", "content": long_prompt}])
        assert len(title) <= 80
        assert title.endswith("…")

    def test_handles_multimodal_content(self):
        title = derive_title(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look at this image"},
                        {"type": "image_url", "image_url": {"url": "data:..."}},
                    ],
                }
            ]
        )
        assert title == "look at this image"

    def test_falls_back_when_no_user_message(self):
        assert derive_title([{"role": "system", "content": "sys"}]) == "(untitled session)"
        assert derive_title([]) == "(untitled session)"

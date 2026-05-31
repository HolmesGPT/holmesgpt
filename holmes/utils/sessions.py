"""Local persistence for interactive ``holmes ask`` sessions.

Each session stores the full conversation (the same ``messages`` list that the
interactive loop and non-interactive ``ask`` already build) plus a little
metadata so it can be listed and resumed later via ``--continue`` /
``--resume``.

Sessions are written as one JSON file per session under
``<config_path_dir>/sessions`` (``~/.holmes/sessions`` by default). The payload
mirrors the shape produced by ``save_conversation_to_file`` /
``LLMResult.model_dump()`` (``messages`` + ``tool_calls`` + ``metadata``) so the
on-disk format stays consistent across HolmesGPT.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from holmes.core.config import config_path_dir

# Maximum length of the auto-generated session title (first user message).
_TITLE_MAX_LENGTH = 80

# Env var to opt out of writing sessions to disk (e.g. in CI). Reading existing
# sessions for --continue / --resume still works; only saving is disabled.
_DISABLE_PERSISTENCE_ENV = "HOLMES_DISABLE_SESSION_PERSISTENCE"


class SessionNotFoundError(Exception):
    """Raised when a session id cannot be found on disk."""


def session_persistence_disabled() -> bool:
    """Whether saving sessions to disk has been turned off via env var."""
    return os.environ.get(_DISABLE_PERSISTENCE_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_title(messages: List[Dict[str, Any]]) -> str:
    """Build a short, single-line title from the first user message."""
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            # Multi-modal content: pick the first text part if present.
            content = next(
                (
                    part.get("text")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ),
                None,
            )
        if not isinstance(content, str):
            continue
        title = " ".join(content.split())
        if not title:
            continue
        if len(title) > _TITLE_MAX_LENGTH:
            title = title[: _TITLE_MAX_LENGTH - 1].rstrip() + "…"
        return title
    return "(untitled session)"


class ChatSession(BaseModel):
    """A persisted interactive conversation that can be resumed later."""

    session_id: str
    title: str = "(untitled session)"
    working_directory: str = ""
    model: Optional[str] = None
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)
    # Same shape as LLMResult.messages / .tool_calls so the on-disk format
    # matches the rest of HolmesGPT's conversation serialization.
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def user_turns(self) -> int:
        return len([m for m in self.messages if m.get("role") == "user"])


class SessionManager:
    """Reads and writes :class:`ChatSession` files under the sessions directory."""

    def __init__(self, sessions_dir: Optional[str] = None) -> None:
        self.sessions_dir = sessions_dir or os.path.join(config_path_dir, "sessions")

    def _ensure_dir(self) -> None:
        os.makedirs(self.sessions_dir, exist_ok=True)

    def _path(self, session_id: str) -> str:
        return os.path.join(self.sessions_dir, f"{session_id}.json")

    @staticmethod
    def new_session_id() -> str:
        """Time-prefixed id so files sort chronologically and stay unique."""
        return f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"

    def save(self, session: ChatSession) -> None:
        """Atomically write a session to disk (write temp file, then rename)."""
        self._ensure_dir()
        session.updated_at = _utc_now_iso()
        target = self._path(session.session_id)
        tmp = f"{target}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(session.model_dump_json(indent=2))
            os.replace(tmp, target)
        except Exception:
            logging.exception("Failed to save session %s", session.session_id)
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def load(self, session_id: str) -> ChatSession:
        path = self._path(session_id)
        if not os.path.exists(path):
            raise SessionNotFoundError(session_id)
        with open(path, "r", encoding="utf-8") as f:
            return ChatSession.model_validate_json(f.read())

    def list_sessions(self) -> List[ChatSession]:
        """Return all valid sessions, most recently updated first.

        Corrupt or unreadable files are skipped (and logged) rather than
        breaking the listing.
        """
        if not os.path.isdir(self.sessions_dir):
            return []
        sessions: List[ChatSession] = []
        for name in os.listdir(self.sessions_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.sessions_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    sessions.append(ChatSession.model_validate_json(f.read()))
            except Exception as e:
                logging.debug("Skipping unreadable session file %s: %s", path, e)
                continue
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def latest(self) -> Optional[ChatSession]:
        """Return the most recently updated session, or ``None`` if there are none."""
        sessions = self.list_sessions()
        return sessions[0] if sessions else None

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        if os.path.exists(path):
            os.remove(path)

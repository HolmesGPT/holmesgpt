"""
Filesystem-based storage for large tool results.

When tool results exceed the context window limit, instead of dropping them,
we save them to the filesystem and return a pointer to the LLM so it can
access the data using bash commands (cat, grep, head, tail, etc.).
"""

import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

from holmes.common.env_vars import (
    HOLMES_TOOL_RESULT_STORAGE_ENABLED,
    HOLMES_TOOL_RESULT_STORAGE_PATH,
)


def get_storage_base_path() -> Path:
    return Path(HOLMES_TOOL_RESULT_STORAGE_PATH)


def generate_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:12]}"


def get_session_path(session_id: str) -> Path:
    return get_storage_base_path() / session_id


def ensure_session_directory(session_id: str) -> Path:
    session_path = get_session_path(session_id)
    session_path.mkdir(parents=True, exist_ok=True)
    return session_path


def cleanup_session(session_id: str) -> bool:
    session_path = get_session_path(session_id)
    if session_path.exists():
        try:
            shutil.rmtree(session_path)
            logging.debug(f"Cleaned up tool result storage for session {session_id}")
            return True
        except Exception as e:
            logging.warning(f"Failed to cleanup session {session_id}: {e}")
            return False
    return True


def save_large_result(
    session_id: str,
    tool_call_id: str,
    content: str,
) -> Optional[str]:
    """
    Save a large tool result to the filesystem as a plain .txt file.

    Returns the file path, or None if storage is disabled/failed.
    """
    if not HOLMES_TOOL_RESULT_STORAGE_ENABLED:
        return None

    try:
        session_path = ensure_session_directory(session_id)
        safe_id = tool_call_id.replace("/", "_").replace("\\", "_")
        file_path = session_path / f"{safe_id}.txt"
        file_path.write_text(content, encoding="utf-8")
        logging.info(f"Saved large tool result to filesystem: {file_path}")
        return str(file_path)
    except Exception as e:
        logging.warning(f"Failed to save tool result to filesystem: {e}")
        return None


def cleanup_all_sessions() -> int:
    """
    Clean up all tool result sessions.

    This is called at the start of each new HTTP request to ensure
    disk doesn't fill up with old results.

    Returns:
        Number of sessions cleaned up.
    """
    storage_path = get_storage_base_path()
    if not storage_path.exists():
        return 0

    count = 0
    try:
        for item in storage_path.iterdir():
            if item.is_dir() and item.name.startswith("sess_"):
                try:
                    shutil.rmtree(item)
                    count += 1
                except Exception as e:
                    logging.warning(f"Failed to cleanup session {item.name}: {e}")
    except Exception as e:
        logging.warning(f"Failed to list sessions for cleanup: {e}")

    if count > 0:
        logging.debug(f"Cleaned up {count} previous tool result sessions")
    return count


def get_session_cleanup_notice() -> str:
    """
    Get the notice to inject into user messages when a new request starts.

    This informs the LLM that any previously saved tool results have been deleted.
    """
    storage_path = get_storage_base_path()
    return (
        f"Note: This is a new request. Any tool results previously saved to "
        f"{storage_path}/ from prior requests have been deleted and are no longer accessible."
    )

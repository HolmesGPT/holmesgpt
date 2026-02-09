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


def generate_chat_id() -> str:
    return f"sess_{uuid.uuid4().hex[:12]}"


def get_chat_path(chat_id: str) -> Path:
    return get_storage_base_path() / chat_id


def ensure_chat_directory(chat_id: str) -> Path:
    chat_path = get_chat_path(chat_id)
    chat_path.mkdir(parents=True, exist_ok=True)
    return chat_path


def cleanup_chat(chat_id: str) -> bool:
    chat_path = get_chat_path(chat_id)
    if chat_path.exists():
        try:
            shutil.rmtree(chat_path)
            logging.debug(f"Cleaned up tool result storage for chat {chat_id}")
            return True
        except Exception as e:
            logging.warning(f"Failed to cleanup chat {chat_id}: {e}")
            return False
    return True


def save_large_result(
    chat_id: str,
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
        chat_path = ensure_chat_directory(chat_id)
        safe_id = tool_call_id.replace("/", "_").replace("\\", "_")
        file_path = chat_path / f"{safe_id}.txt"
        file_path.write_text(content, encoding="utf-8")
        logging.info(f"Saved large tool result to filesystem: {file_path}")
        return str(file_path)
    except Exception as e:
        logging.warning(f"Failed to save tool result to filesystem: {e}")
        return None


def touch_chat(chat_id: str) -> None:
    """Update the chat directory's mtime to mark it as recently used.

    Called at request start so that continued conversations keep their
    chat directory fresh, even if no new files are written.
    """
    chat_path = get_chat_path(chat_id)
    if chat_path.exists():
        try:
            chat_path.touch()
        except Exception as e:
            logging.warning(f"Failed to touch chat {chat_id}: {e}")


def cleanup_old_chats(max_chats: int = 3) -> int:
    """Keep the most recent chat directories and delete the rest.

    Chat directories are sorted by mtime (newest first). Only the first
    max_chats are kept; older ones are removed.

    Returns:
        Number of chat directories deleted.
    """
    storage_path = get_storage_base_path()
    if not storage_path.exists():
        return 0

    chats = []
    try:
        for item in storage_path.iterdir():
            if item.is_dir() and item.name.startswith("sess_"):
                try:
                    chats.append((item, item.stat().st_mtime))
                except OSError:
                    pass
    except Exception as e:
        logging.warning(f"Failed to list chats for cleanup: {e}")
        return 0

    chats.sort(key=lambda x: x[1], reverse=True)

    count = 0
    for item, _ in chats[max_chats:]:
        try:
            shutil.rmtree(item)
            count += 1
        except Exception as e:
            logging.warning(f"Failed to cleanup chat {item.name}: {e}")

    if count > 0:
        logging.debug(f"Cleaned up {count} old tool result chats, kept {min(len(chats), max_chats)}")
    return count

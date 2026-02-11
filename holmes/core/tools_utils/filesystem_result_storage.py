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
    HOLMES_TOOL_RESULT_STORAGE_PATH,
)


def get_storage_base_path() -> Path:
    return Path(HOLMES_TOOL_RESULT_STORAGE_PATH)


def generate_chat_id() -> str:
    return str(uuid.uuid4())


def get_chat_path(chat_id: str) -> Path:
    return get_storage_base_path() / chat_id / "tool_results"


def ensure_chat_directory(chat_id: str) -> Path:
    chat_path = get_chat_path(chat_id)
    chat_path.mkdir(parents=True, exist_ok=True)
    return chat_path


def cleanup_chat(chat_id: str) -> bool:
    """Delete the entire chat directory (UUID dir and its tool_results subdir)."""
    chat_root = get_storage_base_path() / chat_id
    if chat_root.exists():
        try:
            shutil.rmtree(chat_root)
            logging.debug(f"Cleaned up tool result storage for chat {chat_id}")
            return True
        except Exception as e:
            logging.warning(f"Failed to cleanup chat {chat_id}: {e}")
            return False
    return True


def save_large_result(
    chat_id: str,
    tool_name: str,
    tool_call_id: str,
    content: str,
    is_json: bool = False,
) -> Optional[str]:
    """
    Save a large tool result to the filesystem.

    Uses .json extension when the content is JSON, .txt otherwise.

    Returns the file path, or None if storage failed.
    """
    try:
        chat_path = ensure_chat_directory(chat_id)
        safe_name = tool_name.replace("/", "_").replace("\\", "_")
        safe_id = tool_call_id.replace("/", "_").replace("\\", "_")
        extension = ".json" if is_json else ".txt"
        file_path = chat_path / f"{safe_name}_{safe_id}{extension}"
        file_path.write_text(content, encoding="utf-8")
        logging.info(f"Saved large tool result to filesystem: {file_path}")
        return str(file_path)
    except Exception as e:
        logging.warning(f"Failed to save tool result to filesystem: {e}")
        return None

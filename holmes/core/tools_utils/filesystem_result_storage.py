"""
Filesystem-based storage for large tool results.

When tool results exceed the context window limit, instead of dropping them,
we save them to the filesystem and return a pointer to the LLM so it can
access the data using bash commands (cat, grep, head, tail, etc.).
"""

import logging
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from holmes.common.env_vars import (
    HOLMES_TOOL_RESULT_STORAGE_PATH,
)


@contextmanager
def tool_result_storage() -> Generator[Path, None, None]:
    """Context manager that creates a temp directory for tool results and cleans up after."""
    base = Path(HOLMES_TOOL_RESULT_STORAGE_PATH)
    chat_id = str(uuid.uuid4())
    tool_results_dir = base / chat_id / "tool_results"
    tool_results_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield tool_results_dir
    finally:
        chat_root = base / chat_id
        try:
            shutil.rmtree(chat_root)
            logging.debug(f"Cleaned up tool result storage: {chat_root}")
        except Exception as e:
            logging.warning(f"Failed to cleanup tool result storage {chat_root}: {e}")


def save_large_result(
    tool_results_dir: Path,
    tool_name: str,
    tool_call_id: str,
    content: str,
    is_json: bool = False,
) -> Optional[str]:
    """
    Save a large tool result to the filesystem.

    Returns the file path, or None if storage failed.
    """
    try:
        safe_name = re.sub(r"[^\w\-]", "_", tool_name)
        safe_id = re.sub(r"[^\w\-]", "_", tool_call_id)
        extension = ".json" if is_json else ".txt"
        file_path = tool_results_dir / f"{safe_name}_{safe_id}{extension}"
        file_path.write_text(content, encoding="utf-8")
        logging.info(f"Saved large tool result to filesystem: {file_path}")
        return str(file_path)
    except Exception as e:
        logging.warning(f"Failed to save tool result to filesystem: {e}")
        return None

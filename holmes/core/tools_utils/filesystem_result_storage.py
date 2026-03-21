"""
Filesystem-based storage for large tool results.

When tool results exceed the context window limit, instead of dropping them,
we save them to the filesystem and return a pointer to the LLM so it can
access the data using bash commands (cat, grep, head, tail, etc.).
Images are saved as separate files and can be read back via the
read_image_file tool.
"""

import base64
import logging
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Generator, List, Optional

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


MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def save_images(
    tool_results_dir: Path,
    tool_name: str,
    tool_call_id: str,
    images: List[Dict[str, str]],
) -> List[str]:
    """Save images from a tool result to the filesystem.

    Each image dict has {"data": "<base64>", "mimeType": "image/png"}.
    Returns a list of saved file paths.
    """
    saved: List[str] = []
    safe_name = re.sub(r"[^\w\-]", "_", tool_name)
    safe_id = re.sub(r"[^\w\-]", "_", tool_call_id)
    for i, img in enumerate(images):
        try:
            mime_type = img.get("mimeType", "image/png")
            ext = MIME_TO_EXT.get(mime_type)
            if not ext:
                logging.warning(
                    f"Skipping unsupported image MIME type '{mime_type}' for tool {tool_name} "
                    f"(supported: {', '.join(MIME_TO_EXT.keys())})"
                )
                continue
            file_path = tool_results_dir / f"{safe_name}_{safe_id}_img{i}{ext}"
            image_bytes = base64.b64decode(img["data"])
            file_path.write_bytes(image_bytes)
            saved.append(str(file_path))
            logging.info(f"Saved tool result image to filesystem: {file_path}")
        except Exception as e:
            logging.warning(f"Failed to save tool result image {i}: {e}")
    return saved

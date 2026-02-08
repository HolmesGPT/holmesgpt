"""
Filesystem-based storage for large tool results.

When tool results exceed the context window limit, instead of dropping them,
we save them to the filesystem and return a pointer to the LLM so it can
access the data using bash commands (cat, grep, jq, head, tail, etc.).
"""

import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

import yaml

from holmes.common.env_vars import (
    HOLMES_TOOL_RESULT_STORAGE_ENABLED,
    HOLMES_TOOL_RESULT_STORAGE_PATH,
)

MAX_STRUCTURE_HINT_CHARS = 700


def get_storage_base_path() -> Path:
    """Get the base path for tool result storage."""
    return Path(HOLMES_TOOL_RESULT_STORAGE_PATH)


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return f"sess_{uuid.uuid4().hex[:12]}"


def get_session_path(session_id: str) -> Path:
    """Get the storage path for a specific session."""
    return get_storage_base_path() / session_id


def ensure_session_directory(session_id: str) -> Path:
    """Create session directory if it doesn't exist."""
    session_path = get_session_path(session_id)
    session_path.mkdir(parents=True, exist_ok=True)
    return session_path


def cleanup_session(session_id: str) -> bool:
    """
    Clean up all tool results for a session.

    Returns True if cleanup was successful, False otherwise.
    """
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


def detect_file_extension(data: Any) -> str:
    """
    Detect appropriate file extension based on content type.

    Returns .json, .yaml, or .txt based on content analysis.
    """
    if data is None:
        return ".txt"

    # If already a string, try to parse
    if isinstance(data, str):
        data_str = data.strip()

        # Try JSON first
        if data_str.startswith(("{", "[")):
            try:
                json.loads(data_str)
                return ".json"
            except json.JSONDecodeError:
                pass

        # Try YAML (only if not JSON)
        try:
            parsed = yaml.safe_load(data_str)
            # yaml.safe_load parses plain strings successfully,
            # so check it's actually structured
            if isinstance(parsed, (dict, list)):
                return ".yaml"
        except yaml.YAMLError:
            pass

        return ".txt"

    # If dict/list, it will be serialized as JSON
    if isinstance(data, (dict, list)):
        return ".json"

    return ".txt"


def get_json_structure_hint(data: Any) -> Optional[str]:
    """
    Extract structure hint only if concise enough (≤700 chars).

    Returns None if structure is too complex to hint concisely.
    """
    if isinstance(data, dict):
        keys = list(data.keys())
        hint = f"Keys: {keys}"
        if len(hint) <= MAX_STRUCTURE_HINT_CHARS:
            return hint
        # Try truncating keys list until it fits
        for i in range(len(keys) - 1, 0, -1):
            hint = f"Keys: {keys[:i]} (and {len(keys) - i} more)"
            if len(hint) <= MAX_STRUCTURE_HINT_CHARS:
                return hint
        return None  # Even minimal hint too long
    elif isinstance(data, list):
        return f"Array with {len(data)} items"
    return None


def serialize_data(data: Any) -> str:
    """Serialize data to string for storage."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, (dict, list)):
        return json.dumps(data, indent=2, default=str)
    return str(data)


def save_large_result(
    session_id: str,
    tool_call_id: str,
    tool_name: str,
    data: Any,
    params: Optional[dict] = None,
    token_count: Optional[int] = None,
) -> Optional[str]:
    """
    Save a large tool result to the filesystem.

    Creates two files:
    - {tool_call_id}.{ext} - Raw tool output (grep/jq friendly)
    - {tool_call_id}.meta.json - Metadata for context

    Args:
        session_id: Session identifier for grouping results
        tool_call_id: Unique identifier for the tool call
        tool_name: Name of the tool that produced the result
        data: The raw tool output data
        params: Parameters used for the tool call
        token_count: Token count of the result (if known)

    Returns:
        Path to the raw data file, or None if storage is disabled/failed.
    """
    if not HOLMES_TOOL_RESULT_STORAGE_ENABLED:
        return None

    try:
        session_path = ensure_session_directory(session_id)

        # Detect file type and serialize
        extension = detect_file_extension(data)
        serialized_data = serialize_data(data)

        # Create safe filename from tool_call_id
        safe_id = tool_call_id.replace("/", "_").replace("\\", "_")

        # Write raw data file
        raw_file_path = session_path / f"{safe_id}{extension}"
        raw_file_path.write_text(serialized_data, encoding="utf-8")

        # Write metadata file
        metadata = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "params": params,
            "token_count": token_count,
            "file_type": extension.lstrip("."),
            "raw_file": str(raw_file_path),
        }
        meta_file_path = session_path / f"{safe_id}.meta.json"
        meta_file_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        logging.info(
            f"Saved large tool result to filesystem: {raw_file_path} "
            f"({token_count or 'unknown'} tokens)"
        )

        return str(raw_file_path)

    except Exception as e:
        logging.warning(f"Failed to save tool result to filesystem: {e}")
        return None


def format_filesystem_pointer_message(
    file_path: str,
    token_count: int,
    data: Any,
) -> str:
    """
    Format the message to return to the LLM when a result is stored on filesystem.

    Includes:
    - File paths for raw data and metadata
    - Structure hint (if small enough)
    - Example bash commands for accessing the data
    """
    raw_path = Path(file_path)
    meta_path = raw_path.with_suffix(".meta.json")
    extension = raw_path.suffix

    # Build the message
    lines = [
        f"Tool result too large for context window ({token_count:,} tokens). Saved to filesystem.",
        "",
        "Files:",
        f"- Raw output: {file_path}",
        f"- Metadata: {meta_path}",
    ]

    # Add structure hint if available and small enough
    structure_hint = get_json_structure_hint(data)
    if structure_hint:
        lines.append("")
        lines.append(structure_hint)

    # Add access instructions based on file type
    lines.append("")
    lines.append("Access with bash commands:")

    if extension == ".json":
        lines.extend(
            [
                f"- View structure: jq 'keys' {file_path}",
                f"- Filter: jq '.items[] | select(.name==\"foo\")' {file_path}",
                f'- Search: grep -i "error" {file_path}',
                f"- First 100 lines: head -100 {file_path}",
                f"- Count lines: wc -l {file_path}",
            ]
        )
    elif extension == ".yaml":
        lines.extend(
            [
                f'- Search: grep -i "error" {file_path}',
                f"- View start: head -100 {file_path}",
                f"- View end: tail -100 {file_path}",
                f'- Find sections: grep -n "^[a-zA-Z]" {file_path}',
                f"- Count lines: wc -l {file_path}",
            ]
        )
    else:  # .txt or other
        lines.extend(
            [
                f'- Search: grep -i "pattern" {file_path}',
                f"- View start: head -100 {file_path}",
                f"- View end: tail -100 {file_path}",
                f"- Count lines: wc -l {file_path}",
            ]
        )

    return "\n".join(lines)


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

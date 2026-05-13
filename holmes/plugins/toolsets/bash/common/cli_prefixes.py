"""
CLI-approved prefixes persistence.

This module handles loading and saving CLI-approved bash command prefixes
from ~/.holmes/bash_approved_prefixes.yaml.

Note: This is CLI-specific. Server mode uses message metadata for session prefixes.
The CLI mode must be explicitly enabled by calling enable_cli_mode() - this prevents
unnecessary file I/O in server mode.
"""

import logging
import json
import re
from pathlib import Path
from typing import Any, List, Optional

import yaml

from holmes.core.config import config_path_dir

BASH_APPROVED_PREFIXES_FILENAME = "bash_approved_prefixes.yaml"
DEFAULT_CLAUDE_CODE_SETTINGS_FILE = Path("~/.claude/settings.json")
_CLAUDE_CODE_BASH_PERMISSION_RE = re.compile(r"^Bash\((.+)\)$")

# CLI mode flag - only when enabled will we read from file
_cli_mode_enabled = False


def enable_cli_mode() -> None:
    """
    Enable CLI mode for prefix loading.

    Call this at the start of an interactive CLI session to enable
    file-based prefix loading. Server mode should NOT call this.
    """
    global _cli_mode_enabled
    _cli_mode_enabled = True


def is_cli_mode() -> bool:
    """Check if CLI mode is enabled."""
    return _cli_mode_enabled


def get_default_bash_approved_prefixes_file() -> Path:
    """Return the default path for persisted CLI-approved bash prefixes."""
    return Path(config_path_dir) / BASH_APPROVED_PREFIXES_FILENAME


def get_default_claude_code_settings_file() -> Path:
    """Return the default Claude Code settings path."""
    return DEFAULT_CLAUDE_CODE_SETTINGS_FILE.expanduser()


def load_approved_prefixes_file(path: Path) -> List[str]:
    """Load approved prefixes from a Holmes bash approved-prefixes file."""
    expanded_path = path.expanduser()
    if not expanded_path.exists():
        return []

    with expanded_path.open("r") as f:
        data = yaml.safe_load(f)

    if not data:
        return []
    if not isinstance(data, dict):
        raise ValueError(f"{expanded_path} must contain a YAML object")

    approved_prefixes = data.get("approved_prefixes", [])
    if approved_prefixes is None:
        return []
    if not isinstance(approved_prefixes, list):
        raise ValueError("'approved_prefixes' must be a list")

    return [prefix for prefix in approved_prefixes if isinstance(prefix, str)]


def dump_approved_prefixes(prefixes: List[str]) -> str:
    """Serialize approved prefixes using the Holmes YAML shape."""
    deduped = sorted(set(prefixes))
    return yaml.safe_dump({"approved_prefixes": deduped}, default_flow_style=False)


def save_approved_prefixes_file(path: Path, prefixes: List[str]) -> None:
    """Write approved prefixes to a Holmes bash approved-prefixes file."""
    expanded_path = path.expanduser()
    expanded_path.parent.mkdir(parents=True, exist_ok=True)
    expanded_path.write_text(dump_approved_prefixes(prefixes), encoding="utf-8")


def parse_claude_code_bash_permission(entry: str) -> Optional[str]:
    """Extract a Holmes bash prefix from one Claude Code permission entry."""
    match = _CLAUDE_CODE_BASH_PERMISSION_RE.match(entry)
    if not match:
        return None

    prefix = match.group(1).strip()
    if prefix.endswith(":*"):
        prefix = prefix[:-2].strip()
    elif prefix.endswith(" *"):
        prefix = prefix[:-2].strip()

    return prefix or None


def _prefixes_overlap(left: str, right: str) -> bool:
    """Return True when two command prefixes can match the same command."""
    return left == right or left.startswith(f"{right} ") or right.startswith(f"{left} ")


def extract_claude_code_bash_prefixes(
    settings: dict[str, Any],
) -> tuple[List[str], List[str]]:
    """Extract bash prefixes and ignored entries from Claude Code settings."""
    permissions = settings.get("permissions", {})
    if not isinstance(permissions, dict):
        raise ValueError("'permissions' must be an object")

    allow_entries = permissions.get("allow", [])
    if allow_entries is None:
        return [], []
    if not isinstance(allow_entries, list):
        raise ValueError("'permissions.allow' must be a list")

    deny_entries = permissions.get("deny", [])
    if deny_entries is None:
        deny_entries = []
    if not isinstance(deny_entries, list):
        raise ValueError("'permissions.deny' must be a list")

    deny_prefixes = [
        prefix
        for entry in deny_entries
        if isinstance(entry, str)
        for prefix in [parse_claude_code_bash_permission(entry)]
        if prefix is not None
    ]

    prefixes: List[str] = []
    ignored_entries: List[str] = []
    seen: set[str] = set()

    for entry in allow_entries:
        if not isinstance(entry, str):
            ignored_entries.append(repr(entry))
            continue

        prefix = parse_claude_code_bash_permission(entry)
        if prefix is None:
            ignored_entries.append(entry)
            continue

        if any(_prefixes_overlap(prefix, deny_prefix) for deny_prefix in deny_prefixes):
            ignored_entries.append(entry)
            continue

        if prefix not in seen:
            prefixes.append(prefix)
            seen.add(prefix)

    return prefixes, ignored_entries


def load_claude_code_bash_prefixes(path: Path) -> tuple[List[str], List[str]]:
    """Load Claude Code settings and extract bash prefixes."""
    expanded_path = path.expanduser()
    with expanded_path.open("r", encoding="utf-8") as f:
        settings = json.load(f)

    if not isinstance(settings, dict):
        raise ValueError(f"{expanded_path} must contain a JSON object")

    return extract_claude_code_bash_prefixes(settings)


def load_cli_bash_tools_approved_prefixes() -> List[str]:
    """
    Load approved prefixes from ~/.holmes/bash_approved_prefixes.yaml.

    Returns empty list if CLI mode is not enabled (server mode),
    avoiding unnecessary file I/O.
    """
    if not _cli_mode_enabled:
        return []

    try:
        return load_approved_prefixes_file(get_default_bash_approved_prefixes_file())
    except Exception as e:
        logging.warning(f"Failed to load approved prefixes: {e}")
        return []


def save_cli_bash_tools_approved_prefixes(prefixes: List[str]) -> None:
    """
    Save approved prefixes to ~/.holmes/bash_approved_prefixes.yaml.

    Note: This function works regardless of CLI mode, as saving is only
    called from interactive approval flow which is inherently CLI.
    """
    prefixes_file = get_default_bash_approved_prefixes_file()

    # Load existing prefixes and merge (bypass CLI mode check for internal use)
    try:
        existing = load_approved_prefixes_file(prefixes_file)
    except Exception:
        existing = []

    try:
        save_approved_prefixes_file(prefixes_file, prefixes + existing)
    except Exception as e:
        logging.error(f"Failed to save approved prefixes: {e}")

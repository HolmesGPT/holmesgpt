"""
CLI-approved prefixes persistence.

This module handles loading and saving CLI-approved bash command prefixes
from ~/.holmes/bash_approved_prefixes.yaml.

Note: This is CLI-specific. Server mode uses message metadata for session prefixes.
The CLI mode must be explicitly enabled by calling enable_cli_mode() - this prevents
unnecessary file I/O in server mode.
"""

import logging
import os
from typing import List

import yaml

from holmes.core.config import config_path_dir

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


def load_cli_bash_tools_approved_prefixes() -> List[str]:
    """
    Load approved prefixes from ~/.holmes/bash_approved_prefixes.yaml.

    Returns empty list if CLI mode is not enabled (server mode),
    avoiding unnecessary file I/O.
    """
    if not _cli_mode_enabled:
        return []

    prefixes_file = os.path.join(config_path_dir, "bash_approved_prefixes.yaml")
    if os.path.exists(prefixes_file):
        try:
            with open(prefixes_file, "r") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict) and "approved_prefixes" in data:
                    return data["approved_prefixes"]
        except Exception as e:
            logging.warning(f"Failed to load approved prefixes: {e}")
    return []


def save_cli_bash_tools_approved_prefixes(prefixes: List[str]) -> None:
    """
    Save approved prefixes to ~/.holmes/bash_approved_prefixes.yaml.

    Note: This function works regardless of CLI mode, as saving is only
    called from interactive approval flow which is inherently CLI.
    """
    prefixes_file = os.path.join(config_path_dir, "bash_approved_prefixes.yaml")
    os.makedirs(config_path_dir, exist_ok=True)

    # Load existing prefixes and merge (bypass CLI mode check for internal use)
    existing: set[str] = set()
    if os.path.exists(prefixes_file):
        try:
            with open(prefixes_file, "r") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict) and "approved_prefixes" in data:
                    existing = set(data["approved_prefixes"])
        except Exception:
            pass

    updated = sorted(set(prefixes) | existing)

    try:
        with open(prefixes_file, "w") as f:
            yaml.safe_dump({"approved_prefixes": updated}, f, default_flow_style=False)
    except Exception as e:
        logging.error(f"Failed to save approved prefixes: {e}")

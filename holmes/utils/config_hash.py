import hashlib
import json
import logging
import os
from typing import Optional

from holmes.core.config import config_path_dir

DEFAULT_CONFIG_HASHES_LOCATION = os.path.join(config_path_dir, "config_hashes")


def compute_file_hash(file_path: str) -> Optional[str]:
    """Compute SHA1 hash of a file's full contents."""
    try:
        with open(file_path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except (OSError, IOError) as e:
        logging.warning(f"Could not read file for hashing: {file_path}: {e}")
        return None


def load_config_hashes(
    hash_file_path: str = DEFAULT_CONFIG_HASHES_LOCATION,
) -> dict[str, str]:
    """Load stored config hashes from disk."""
    if not os.path.exists(hash_file_path):
        return {}
    try:
        with open(hash_file_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logging.warning(f"Could not load config hashes from {hash_file_path}: {e}")
        return {}


def save_config_hashes(
    hashes: dict[str, str],
    hash_file_path: str = DEFAULT_CONFIG_HASHES_LOCATION,
) -> None:
    """Save config hashes to disk."""
    dir_path = os.path.dirname(hash_file_path)
    try:
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        with open(hash_file_path, "w") as f:
            json.dump(hashes, f, indent=2)
    except Exception as e:
        logging.warning(f"Could not save config hashes to {hash_file_path}: {e}")


def check_and_update_config_hashes(
    file_paths: list[str],
    hash_file_path: str = DEFAULT_CONFIG_HASHES_LOCATION,
) -> bool:
    """
    Check if any datasource config file hashes have changed since the last run.

    Computes SHA1 hashes for each datasource config file and compares them
    against the stored hashes in ~/.holmes/config_hashes. If any hash has
    changed (or a file was added/removed), returns True to signal that
    toolsets should be refreshed.

    The updated hashes are saved before returning so the next run uses them.
    """
    stored_hashes = load_config_hashes(hash_file_path)
    current_hashes: dict[str, str] = {}
    changed = False

    for file_path in file_paths:
        if not file_path or not os.path.exists(file_path):
            continue
        abs_path = os.path.abspath(file_path)
        file_hash = compute_file_hash(abs_path)
        if file_hash is None:
            continue
        current_hashes[abs_path] = file_hash
        if abs_path not in stored_hashes or stored_hashes[abs_path] != file_hash:
            logging.info(f"Config hash changed for datasource: {abs_path}")
            changed = True

    # Check if any previously tracked files were removed
    for stored_path in stored_hashes:
        if stored_path not in current_hashes:
            logging.info(f"Previously tracked datasource config removed: {stored_path}")
            changed = True

    if changed:
        save_config_hashes(current_hashes, hash_file_path)

    return changed

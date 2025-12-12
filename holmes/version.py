"""
Centralized version management for Holmes.
Handles current version detection, latest version fetching, and comparison logic.
"""

import json
import os
import subprocess
import sys
import threading
from typing import Optional, NamedTuple
from functools import cache
import requests  # type: ignore
from pydantic import BaseModel, ConfigDict
from holmes.common.env_vars import ROBUSTA_API_ENDPOINT

# For relative imports to work in Python 3.6 - see https://stackoverflow.com/a/49375740
this_path = os.path.dirname(os.path.realpath(__file__))
sys.path.append(this_path)

# Version checking API constants
HOLMES_GET_INFO_URL = f"{ROBUSTA_API_ENDPOINT}/api/holmes/get_info"
TIMEOUT = 0.5


class HolmesInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    latest_version: Optional[str] = None


class VersionCheckResult(NamedTuple):
    """Result of version check with all relevant info"""

    is_latest: bool
    current_version: str
    latest_version: Optional[str] = None
    update_message: Optional[str] = None


def is_official_release() -> bool:
    """Check if this is an official release (version was patched by CI/CD)"""
    from holmes import __version__

    return not __version__.startswith("0.0.0")


@cache
def get_version() -> str:
    """
    Get the current version of Holmes.
    Returns the official version if patched by CI/CD, otherwise builds from git.
    """
    from holmes import __version__

    # the version string was patched by a release - return __version__ which will be correct
    if is_official_release():
        return __version__

    # we are running from an unreleased dev version
    archival_file_path = os.path.join(this_path, ".git_archival.json")
    if os.path.exists(archival_file_path):
        try:
            with open(archival_file_path, "r") as f:
                archival_data = json.load(f)
                refs = archival_data.get("refs", "")
                hash_short = archival_data.get("hash-short", "")

                # Check if Git substitution didn't happen (placeholders are still present)
                if "$Format:" in refs or "$Format:" in hash_short:
                    # Placeholders not substituted, skip to next method
                    pass
                else:
                    # Valid archival data found
                    return f"dev-{refs}-{hash_short}"
        except Exception:
            pass

    # Now try git commands for development environments
    try:
        env = os.environ.copy()
        # Set ceiling to prevent walking up beyond the project root
        # We want to allow access to holmes/.git but not beyond holmes
        project_root = os.path.dirname(this_path)  # holmes
        env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(
            project_root
        )  # holmes's parent

        # Get the latest git tag
        tag = (
            subprocess.check_output(
                ["git", "describe", "--tags"],
                stderr=subprocess.STDOUT,
                cwd=this_path,
                env=env,
            )
            .decode()
            .strip()
        )

        # Get the current branch name
        branch = (
            subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                stderr=subprocess.STDOUT,
                cwd=this_path,
                env=env,
            )
            .decode()
            .strip()
        )

        # Check if there are uncommitted changes
        status = (
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                stderr=subprocess.STDOUT,
                cwd=this_path,
                env=env,
            )
            .decode()
            .strip()
        )
        dirty = "-dirty" if status else ""

        return f"dev-{tag}-{branch}{dirty}"

    except Exception:
        pass

    return "dev-unknown"


@cache
def fetch_holmes_info() -> Optional[HolmesInfo]:
    """Fetch latest version information from Robusta API"""
    try:
        response = requests.get(HOLMES_GET_INFO_URL, timeout=TIMEOUT)
        response.raise_for_status()
        result = response.json()
        return HolmesInfo(**result)
    except Exception:
        return None


def check_version() -> VersionCheckResult:
    """
    Centralized version checking logic.
    Returns complete version check result with message.
    """
    current_version = get_version()
    holmes_info = fetch_holmes_info()

    # Default to latest if we can't determine
    if not holmes_info or not holmes_info.latest_version or not current_version:
        return VersionCheckResult(
            is_latest=True, current_version=current_version or "unknown"
        )

    # Dev versions are considered latest
    if current_version.startswith("dev-"):
        return VersionCheckResult(
            is_latest=True,
            current_version=current_version,
            latest_version=holmes_info.latest_version,
        )

    # Check if current version starts with latest version
    is_latest = current_version.startswith(holmes_info.latest_version)

    update_message = None
    if not is_latest:
        update_message = f"Update available: {holmes_info.latest_version} (current: {current_version})"

    return VersionCheckResult(
        is_latest=is_latest,
        current_version=current_version,
        latest_version=holmes_info.latest_version,
        update_message=update_message,
    )


def check_version_async(callback):
    """
    Async version check for background use.
    Calls callback with VersionCheckResult when complete.
    """

    def _check():
        try:
            result = check_version()
            callback(result)
        except Exception:
            # Silent failure - call callback with "latest" result
            callback(VersionCheckResult(is_latest=True, current_version="unknown"))

    thread = threading.Thread(target=_check, daemon=True)
    thread.start()
    return thread

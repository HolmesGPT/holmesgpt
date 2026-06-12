"""Lightweight litellm version helpers.

Kept dependency-free (only stdlib + packaging) so it can be imported from both
``holmes.utils.holmes_status`` and ``holmes.core.llm`` without creating an import
cycle through ``holmes.config``.
"""

import logging
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Optional

from packaging.version import InvalidVersion, Version


@lru_cache(maxsize=1)
def get_litellm_version() -> Optional[str]:
    """Return the installed litellm version, or None if it can't be resolved.

    Reported to the platform via HolmesStatus.metadata so relay/frontend can
    gate model selection against each cluster's litellm compatibility.
    """
    try:
        return _pkg_version("litellm")
    except PackageNotFoundError:
        return None
    except Exception as exc:
        logging.debug(f"Failed to resolve litellm version: {exc}")
        return None


def is_litellm_compatible(
    min_litellm_version: Optional[str],
    current_litellm_version: Optional[str],
) -> bool:
    """Return True if a model requiring ``min_litellm_version`` can run.

    Fail closed: a missing/unparseable ``min_litellm_version`` is treated as
    incompatible. If the current version can't be resolved, assume compatible
    (don't hide models just because we couldn't read our own version).
    """
    if not min_litellm_version:
        return False
    try:
        required = Version(str(min_litellm_version).strip())
    except InvalidVersion:
        logging.warning(
            "Could not parse min_litellm_version %r; treating model as unavailable",
            min_litellm_version,
        )
        return False

    if not current_litellm_version:
        return True
    try:
        current = Version(str(current_litellm_version).strip())
    except InvalidVersion:
        return True

    return current >= required

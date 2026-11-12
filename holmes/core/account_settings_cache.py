"""The one cached read of this Holmes's ``AccountSettings.settings`` row.

Every setting Holmes reads from that row goes through one
``AccountSettingsCache``, so the row is read once per TTL rather than once per
setting, every reader sees the same snapshot, and a change in the UI reaches
them all within the same TTL (relay caches the row the same way).
"""

import logging
import math
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

AccountSettings = Dict[str, Any]

DEFAULT_ACCOUNT_SETTINGS_CACHE_TTL_SEC = 60


def account_settings_cache_ttl() -> int:
    """``ACCOUNT_SETTINGS_CACHE_TTL_SEC``, else the older
    ``SKILL_HIERARCHY_CACHE_TTL_SEC``, else 60. Read at startup, so a bad value
    falls back to the default instead of stopping Holmes."""
    name = "ACCOUNT_SETTINGS_CACHE_TTL_SEC"
    raw = os.environ.get(name)
    if raw is None and "SKILL_HIERARCHY_CACHE_TTL_SEC" in os.environ:
        name = "SKILL_HIERARCHY_CACHE_TTL_SEC"
        raw = os.environ[name]
        logging.warning(
            "SKILL_HIERARCHY_CACHE_TTL_SEC is deprecated; it now sets the cache "
            "for every AccountSettings read. Use ACCOUNT_SETTINGS_CACHE_TTL_SEC."
        )
    if raw is None:
        return DEFAULT_ACCOUNT_SETTINGS_CACHE_TTL_SEC
    try:
        ttl = int(raw)
        if ttl <= 0:
            raise ValueError(f"must be positive, got {ttl}")
    except ValueError as e:
        logging.warning(
            "Invalid %s=%r (%s); falling back to %ss",
            name,
            raw,
            e,
            DEFAULT_ACCOUNT_SETTINGS_CACHE_TTL_SEC,
        )
        return DEFAULT_ACCOUNT_SETTINGS_CACHE_TTL_SEC
    return ttl


class AccountSettingsCache:
    """``get``: the settings read within the TTL. After a failed re-read it
    serves the last value read for another TTL, so a database blip does not
    change what the settings control. ``None`` only when no read ever succeeded.

    ``get_fresh``: for gates that refuse when unsure. The settings read within
    the TTL, or ``None`` after a failed read; never an older value.

    A failed read is retried once per TTL, not on every call.
    """

    def __init__(
        self,
        read: Callable[[], AccountSettings],
        ttl: float = DEFAULT_ACCOUNT_SETTINGS_CACHE_TTL_SEC,
        timer: Callable[[], float] = time.monotonic,
    ):
        self._read = read
        self._ttl = ttl
        self._timer = timer
        self._lock = threading.Lock()
        self._value: Optional[AccountSettings] = None
        self._read_at = -math.inf
        self._failed_at = -math.inf

    def get(self) -> Optional[AccountSettings]:
        with self._lock:
            if self._is_fresh():
                return self._value
            if self._failed_recently():
                return self._value
        settings = self._refresh()
        if settings is not None:
            return settings
        with self._lock:
            if self._value is not None:
                logging.warning(
                    "Could not re-read AccountSettings; serving the last value "
                    "read for another %ss",
                    self._ttl,
                )
            return self._value

    def get_fresh(self) -> Optional[AccountSettings]:
        with self._lock:
            if self._is_fresh():
                return self._value
            if self._failed_recently():
                return None
        return self._refresh()

    def clear(self) -> None:
        with self._lock:
            self._value = None
            self._read_at = -math.inf
            self._failed_at = -math.inf

    def _is_fresh(self) -> bool:
        return self._value is not None and self._timer() - self._read_at < self._ttl

    def _failed_recently(self) -> bool:
        return self._timer() - self._failed_at < self._ttl

    def _refresh(self) -> Optional[AccountSettings]:
        """The settings just read, or None when the read failed."""
        try:
            settings = self._read()
        except Exception:
            logging.warning("Failed to read AccountSettings", exc_info=True)
            with self._lock:
                self._failed_at = self._timer()
            return None
        if not isinstance(settings, dict):
            logging.warning("Ignoring AccountSettings.settings of type %s", type(settings))
            settings = {}
        with self._lock:
            self._value = settings
            self._read_at = self._timer()
            self._failed_at = -math.inf
        return settings

"""Executor sizes (ROB-1369): how many conversations an executor runs at once."""

import logging
from typing import Any, Dict, Mapping, Optional, Set

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from holmes.common.env_vars import CONVERSATION_WORKER_MAX_CONCURRENT
from holmes.core.conversations_worker.models import AUTO_EXECUTOR, MANUAL_EXECUTOR

# The one bound on an executor's size. Its thread pool is created with this
# many threads (spawned lazily) so a live settings change up to this size needs
# no new pool, and a huge account setting is clamped to it here.
THREAD_CEILING = 64

# Used when the account settings say nothing about the name.
BUILTIN_EXECUTOR_SIZES: Dict[str, int] = {MANUAL_EXECUTOR: 10, AUTO_EXECUTOR: 1}


def positive_int(value: object) -> Optional[int]:
    """``value`` as a positive int, or None when it is not one (bool excluded:
    a stray ``true`` must not become 1 thread)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        n = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


class ExecutorSizing(BaseModel):
    """Size per executor name, and the only place the account setting is
    validated and the thread ceiling applied.

    ``size_for(name, account_sizes)`` resolves, in order:
      1. ``account_sizes[name]`` — the raw ``AccountSettings.settings
         .conversation_executors`` value as the DAL read it, written from the
         UI (Settings → LLMs sets 'manual', Settings → Triage sets 'auto');
         entries that are not a positive int are ignored (warned once);
      2. the built-in default for the name (manual=10, auto=1);
      3. ``base_size`` (``CONVERSATION_WORKER_MAX_CONCURRENT``, 5) for any
         other name.
    Every result is clamped to ``thread_ceiling``.
    """

    base_size: int = CONVERSATION_WORKER_MAX_CONCURRENT
    builtin_sizes: Dict[str, int] = Field(
        default_factory=lambda: dict(BUILTIN_EXECUTOR_SIZES)
    )
    thread_ceiling: int = THREAD_CEILING

    _warned: Set[str] = PrivateAttr(default_factory=set)

    @field_validator("base_size", "thread_ceiling", mode="before")
    @classmethod
    def _at_least_one(cls, value: object) -> int:
        return positive_int(value) or 1

    def size_for(self, name: str, account_sizes: Any = None) -> int:
        return min(self._unclamped_size_for(name, account_sizes), self.thread_ceiling)

    def _unclamped_size_for(self, name: str, account_sizes: Any) -> int:
        if account_sizes and not isinstance(account_sizes, Mapping):
            self._warn_once(
                "shape",
                "Ignoring malformed conversation_executors account setting: %r",
                account_sizes,
            )
            account_sizes = None
        if account_sizes and name in account_sizes:
            size = positive_int(account_sizes[name])
            if size is not None:
                return size
            self._warn_once(
                name,
                "Ignoring invalid account setting conversation_executors[%r]=%r",
                name,
                account_sizes[name],
            )
        return self.builtin_sizes.get(name, self.base_size)

    def _warn_once(self, key: str, msg: str, *args: object) -> None:
        # size_for runs on every discovery tick; warn once per cause.
        if key in self._warned:
            return
        self._warned.add(key)
        logging.warning(msg, *args)

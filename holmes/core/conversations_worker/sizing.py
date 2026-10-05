"""Executor pool sizes (ROB-1369)."""

import logging
from typing import Dict, Mapping, Optional

from holmes.common.env_vars import CONVERSATION_WORKER_MAX_CONCURRENT
from holmes.core.conversations_worker.models import AUTO_EXECUTOR, DEFAULT_EXECUTOR

# A pool is created with this many threads (spawned lazily) so a live settings
# change up to this size needs no new pool; it also bounds a huge account setting.
THREAD_CEILING = 64

# Used when the account settings say nothing about the name.
BUILTIN_EXECUTOR_SIZES: Dict[str, int] = {DEFAULT_EXECUTOR: 10, AUTO_EXECUTOR: 2}


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


class ExecutorSizing:
    """Pool size per executor name.

    ``size_for(name, account_sizes)`` resolves, in order:
      1. ``account_sizes[name]`` — AccountSettings.settings.conversation_executors,
         written from the UI (Settings → LLMs sets 'manual', Settings → Triage
         sets 'auto');
      2. the built-in default for the name (manual=10, auto=2);
      3. ``CONVERSATION_WORKER_MAX_CONCURRENT`` (5) for any other name.
    Every result is capped at the thread ceiling.
    """

    def __init__(
        self,
        base_size: int = CONVERSATION_WORKER_MAX_CONCURRENT,
        builtin_sizes: Optional[Mapping[str, int]] = None,
        thread_ceiling: int = THREAD_CEILING,
    ):
        self.base_size = positive_int(base_size) or 1
        self.thread_ceiling = positive_int(thread_ceiling) or 1
        self.builtin_sizes = dict(
            BUILTIN_EXECUTOR_SIZES if builtin_sizes is None else builtin_sizes
        )
        self._warned: set = set()

    def size_for(
        self, name: str, account_sizes: Optional[Mapping[str, object]] = None
    ) -> int:
        return min(self._unclamped_size_for(name, account_sizes), self.thread_ceiling)

    def _unclamped_size_for(
        self, name: str, account_sizes: Optional[Mapping[str, object]]
    ) -> int:
        if account_sizes:
            size = positive_int(account_sizes.get(name))
            if size is not None:
                return size
            if name in account_sizes and name not in self._warned:
                # size_for runs on every discovery tick; warn once per name.
                self._warned.add(name)
                logging.warning(
                    "Ignoring invalid account setting conversation_executors[%r]=%r",
                    name,
                    account_sizes.get(name),
                )
        return self.builtin_sizes.get(name, self.base_size)

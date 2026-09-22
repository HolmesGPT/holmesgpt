"""Per-executor conversation pools (ROB-1369).

Every Conversations row names the executor that must run it. The worker keeps
one ``ConversationExecutor`` per name, created lazily the first time a pending
conversation names it, so live user asks ('manual') and background work
('auto': alert triage, triggered workflows) never compete for the same slots.
"""

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Mapping, Optional

from holmes.common.env_vars import (
    CONVERSATION_WORKER_EXECUTOR_MAX_CONCURRENT_ENV_PREFIX,
    CONVERSATION_WORKER_EXECUTOR_THREAD_CEILING,
    CONVERSATION_WORKER_MAX_CONCURRENT,
    CONVERSATION_WORKER_MAX_EXECUTORS,
    CONVERSATION_WORKER_SLOT_STUCK_WARN_SECONDS,
)
from holmes.core.conversations_worker.models import (
    AUTO_EXECUTOR,
    DEFAULT_EXECUTOR,
    ConversationTask,
)

# Saturation logging (ROB-759) is transition-based, not periodic: the single
# INFO line fires only after this long of CONTINUOUS zero-free-slots; the
# stuck-slot WARNING repeats at most every _STUCK_WARN_RATE_LIMIT_SECONDS.
_SATURATION_LOG_AFTER_SECONDS = 60.0
_STUCK_WARN_RATE_LIMIT_SECONDS = 300.0

# Executor names come from broadcast payloads and DB rows written by other
# services; keep them to a conservative slug so a bad payload can't name a
# pool something unloggable or unbounded.
_EXECUTOR_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def is_valid_executor_name(name: object) -> bool:
    return isinstance(name, str) and bool(_EXECUTOR_NAME_RE.match(name))


# Built-in per-executor defaults, used when neither the account settings nor a
# per-name env var says otherwise.
BUILTIN_EXECUTOR_SIZES: Dict[str, int] = {DEFAULT_EXECUTOR: 10, AUTO_EXECUTOR: 2}


def _positive_int(value: object) -> Optional[int]:
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


class ExecutorSettings:
    """Pool size per executor name.

    ``size_for(name, account_sizes)`` resolves, in order:
      1. ``account_sizes[name]`` — AccountSettings.settings.conversation_executors,
         written from the UI (Settings → LLM Models sets 'manual', Settings →
         AI Triage sets 'auto');
      2. env ``CONVERSATION_WORKER_MAX_CONCURRENT_<NAME>`` (upper-cased name);
      3. the built-in default for the name (manual=10, auto=2);
      4. env ``CONVERSATION_WORKER_MAX_CONCURRENT`` (5) for any other name.
    """

    def __init__(
        self,
        base_size: int = CONVERSATION_WORKER_MAX_CONCURRENT,
        max_executors: int = CONVERSATION_WORKER_MAX_EXECUTORS,
        thread_ceiling: int = CONVERSATION_WORKER_EXECUTOR_THREAD_CEILING,
        builtin_sizes: Optional[Mapping[str, int]] = None,
        env: Optional[Mapping[str, str]] = None,
    ):
        self.base_size = _positive_int(base_size) or 1
        self.max_executors = _positive_int(max_executors) or 1
        self.thread_ceiling = _positive_int(thread_ceiling) or 1
        self.builtin_sizes = dict(
            BUILTIN_EXECUTOR_SIZES if builtin_sizes is None else builtin_sizes
        )
        self._env = env if env is not None else os.environ

    @classmethod
    def from_env(cls) -> "ExecutorSettings":
        return cls()

    def env_size_for(self, name: str) -> Optional[int]:
        raw = self._env.get(
            f"{CONVERSATION_WORKER_EXECUTOR_MAX_CONCURRENT_ENV_PREFIX}{name.upper()}"
        )
        if raw is None:
            return None
        size = _positive_int(raw)
        if size is None:
            logging.error(
                "Ignoring invalid %s%s=%r (expected a positive integer)",
                CONVERSATION_WORKER_EXECUTOR_MAX_CONCURRENT_ENV_PREFIX,
                name.upper(),
                raw,
            )
        return size

    def size_for(
        self, name: str, account_sizes: Optional[Mapping[str, object]] = None
    ) -> int:
        if account_sizes:
            size = _positive_int(account_sizes.get(name))
            if size is not None:
                return size
            if name in account_sizes:
                logging.warning(
                    "Ignoring invalid account setting conversation_executors[%r]=%r",
                    name,
                    account_sizes.get(name),
                )
        size = self.env_size_for(name)
        if size is not None:
            return size
        return self.builtin_sizes.get(name, self.base_size)


class _ActiveTask:
    """An in-flight conversation: the task itself plus when it took its slot."""

    __slots__ = ("task", "started")

    def __init__(self, task: ConversationTask, started: float):
        self.task = task
        self.started = started


class ConversationExecutor:
    """One named pool: its threads, in-flight set, and event-driven claim loop.

    The loop only wakes on ``wake()`` (a broadcast naming this executor, the
    worker's discovery poll, or a slot freeing); ``claim_fn`` does the actual
    claim + dispatch so the DB contract stays in the worker.
    """

    def __init__(
        self,
        name: str,
        max_concurrent: int,
        thread_ceiling: int = CONVERSATION_WORKER_EXECUTOR_THREAD_CEILING,
    ):
        self.name = name
        self.max_concurrent = max(1, int(max_concurrent))
        # The pool holds up to thread_ceiling threads so set_max_concurrent()
        # can raise the limit live; only max_concurrent tasks are ever
        # submitted (claims are bounded by free_slots()), so extra threads
        # are never spawned.
        self.thread_ceiling = max(self.max_concurrent, int(thread_ceiling))
        self._pool: Optional[ThreadPoolExecutor] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.notify_event = threading.Event()
        self._active: Dict[tuple, _ActiveTask] = {}
        self._active_lock = threading.Lock()
        # See _SATURATION_LOG_AFTER_SECONDS. None sentinels, never 0.0:
        # time.monotonic() can be small on a fresh host.
        self._saturated_since: Optional[float] = None
        self._saturation_logged = False
        self._last_stuck_warn: Optional[float] = None

    # ---- lifecycle ----

    @property
    def running(self) -> bool:
        return self._running

    def start(self, claim_fn: Callable[["ConversationExecutor"], None]) -> None:
        if self._running:
            return
        self._running = True
        self._pool = ThreadPoolExecutor(
            max_workers=self.thread_ceiling,
            thread_name_prefix=f"conversation-executor-{self.name}",
        )
        self._thread = threading.Thread(
            target=self._loop,
            args=(claim_fn,),
            daemon=True,
            name=f"conversation-claim-loop-{self.name}",
        )
        self._thread.start()
        logging.info(
            "Conversation executor %r started (max_concurrent=%d)",
            self.name,
            self.max_concurrent,
        )

    def stop(self) -> None:
        self._running = False
        self.notify_event.set()
        if self._pool is not None:
            # Don't block on in-flight conversations; they are retired by the
            # worker's shutdown sweep.
            self._pool.shutdown(wait=False)
            self._pool = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def wake(self) -> None:
        self.notify_event.set()

    def set_max_concurrent(self, value: int) -> bool:
        """Apply a new size (from account settings) without recreating the
        pool. Capped at the thread ceiling. Returns True when it changed."""
        new = max(1, min(int(value), self.thread_ceiling))
        if new == self.max_concurrent:
            return False
        logging.info(
            "Conversation executor %r max_concurrent %d -> %d",
            self.name,
            self.max_concurrent,
            new,
        )
        self.max_concurrent = new
        # Slots may have opened up.
        self.notify_event.set()
        return True

    def _loop(self, claim_fn: Callable[["ConversationExecutor"], None]) -> None:
        while self._running:
            triggered = self.notify_event.wait()
            if not self._running:
                break
            self.notify_event.clear()
            try:
                claim_fn(self)
            except Exception:
                logging.exception(
                    "Error in conversation executor %r claim loop (triggered=%s)",
                    self.name,
                    triggered,
                    exc_info=True,
                )

    # ---- capacity ----

    def submit(self, fn: Callable, *args) -> None:
        """Hand a task to the pool. Raises RuntimeError once stopped."""
        if self._pool is None:
            raise RuntimeError("executor pool is not running")
        self._pool.submit(fn, *args)

    def track(self, task: ConversationTask) -> None:
        with self._active_lock:
            self._active[task.active_key] = _ActiveTask(task, time.monotonic())

    def untrack(self, task: ConversationTask) -> None:
        with self._active_lock:
            self._active.pop(task.active_key, None)

    def active_count(self) -> int:
        with self._active_lock:
            return len(self._active)

    def active_entries(self) -> List[_ActiveTask]:
        with self._active_lock:
            return list(self._active.values())

    def free_slots(self) -> int:
        return self.max_concurrent - self.active_count()

    # ---- saturation logging (ROB-759) ----

    def note_saturation(self) -> None:
        now = time.monotonic()
        if self._saturated_since is None:
            self._saturated_since = now
            return
        if (
            not self._saturation_logged
            and now - self._saturated_since >= _SATURATION_LOG_AFTER_SECONDS
        ):
            self._saturation_logged = True
            with self._active_lock:
                ages = sorted(
                    (round(now - entry.started, 1), key)
                    for key, entry in self._active.items()
                )
            logging.info(
                "Conversation executor %r claim capacity saturated for %.0fs: all %d "
                "slots in use; pending conversations will not be claimed until one "
                "finishes. In-flight (age_seconds, (conversation_id, "
                "request_sequence)): %s",
                self.name,
                now - self._saturated_since,
                self.max_concurrent,
                ages,
            )
        if (
            self._last_stuck_warn is None
            or now - self._last_stuck_warn >= _STUCK_WARN_RATE_LIMIT_SECONDS
        ):
            with self._active_lock:
                stuck = sorted(
                    (round(now - entry.started, 1), key)
                    for key, entry in self._active.items()
                    if now - entry.started
                    >= CONVERSATION_WORKER_SLOT_STUCK_WARN_SECONDS
                )
            if stuck:
                self._last_stuck_warn = now
                logging.warning(
                    "Conversation executor %r slot(s) stuck: %d in-flight "
                    "conversation(s) running longer than %.0fs while claiming is "
                    "blocked at full capacity. Stuck (age_seconds, "
                    "(conversation_id, request_sequence)): %s",
                    self.name,
                    len(stuck),
                    CONVERSATION_WORKER_SLOT_STUCK_WARN_SECONDS,
                    stuck,
                )

    def note_capacity_available(self, free: int) -> None:
        if self._saturation_logged:
            duration = time.monotonic() - (self._saturated_since or 0.0)
            logging.info(
                "Conversation executor %r claim capacity available again (free=%d) "
                "after %.0fs saturated",
                self.name,
                free,
                duration,
            )
        self._saturated_since = None
        self._saturation_logged = False

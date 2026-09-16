"""Per-executor conversation pools (ROB-1369).

Every Conversations row names the executor that must run it. The worker keeps
one ``ConversationExecutor`` per name, created lazily the first time a pending
conversation names it, so live user asks ('manual') and background work
('auto': alert triage, triggered workflows) never compete for the same slots.
"""

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from holmes.common.env_vars import (
    CONVERSATION_WORKER_AUTO_MAX_CONCURRENT,
    CONVERSATION_WORKER_DEFAULT_EXECUTOR_MAX_CONCURRENT,
    CONVERSATION_WORKER_EXECUTORS,
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


class ExecutorSettings:
    """Pool size per executor name, from env.

    ``CONVERSATION_WORKER_EXECUTORS`` is a JSON object ``{name: size}``. It is
    overlaid on the built-in defaults ('manual' keeps honoring
    CONVERSATION_WORKER_MAX_CONCURRENT so existing tuning carries over). Names
    absent from the map get ``default_size``.
    """

    def __init__(
        self,
        sizes: Dict[str, int],
        default_size: int,
        max_executors: int,
    ):
        self.sizes = dict(sizes)
        self.default_size = max(1, int(default_size))
        self.max_executors = max(1, int(max_executors))

    @classmethod
    def from_env(cls) -> "ExecutorSettings":
        sizes: Dict[str, int] = {
            DEFAULT_EXECUTOR: CONVERSATION_WORKER_MAX_CONCURRENT,
            AUTO_EXECUTOR: CONVERSATION_WORKER_AUTO_MAX_CONCURRENT,
        }
        raw = (CONVERSATION_WORKER_EXECUTORS or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("expected a JSON object")
            except ValueError:
                logging.error(
                    "CONVERSATION_WORKER_EXECUTORS is not a JSON object of "
                    "name -> size; ignoring it (%r)",
                    raw,
                )
                parsed = {}
            for name, size in parsed.items():
                if not is_valid_executor_name(name):
                    logging.error(
                        "CONVERSATION_WORKER_EXECUTORS: invalid executor name %r ignored",
                        name,
                    )
                    continue
                try:
                    size_int = int(size)
                except (TypeError, ValueError):
                    size_int = 0
                if size_int <= 0:
                    logging.error(
                        "CONVERSATION_WORKER_EXECUTORS: executor %r has invalid size %r; ignored",
                        name,
                        size,
                    )
                    continue
                sizes[name] = size_int
        return cls(
            sizes=sizes,
            default_size=CONVERSATION_WORKER_DEFAULT_EXECUTOR_MAX_CONCURRENT,
            max_executors=CONVERSATION_WORKER_MAX_EXECUTORS,
        )

    def size_for(self, name: str) -> int:
        return self.sizes.get(name, self.default_size)


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

    def __init__(self, name: str, max_concurrent: int):
        self.name = name
        self.max_concurrent = max(1, int(max_concurrent))
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
            max_workers=self.max_concurrent,
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

"""One named conversation executor (ROB-1369).

Every Conversations row names the executor that must run it. The registry
keeps one ``ConversationExecutor`` per name, so live user asks ('manual') and
background work ('auto': alert triage, triggered workflows) never compete for
the same slots. An executor owns everything between "a row is pending" and
"the processor runs it": the claim loop, the claim RPC bounded by its free
slots, dispatch into its thread pool, the in-flight set and saturation logging.
"Pool" below always means that ``ThreadPoolExecutor``, never the executor.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Dict, List, Optional

from holmes.common.env_vars import CONVERSATION_WORKER_SLOT_STUCK_WARN_SECONDS
from holmes.core.conversations_worker.models import ConversationTask
from holmes.core.conversations_worker.sizing import THREAD_CEILING

if TYPE_CHECKING:
    from holmes.core.conversations_worker.processor import ConversationProcessor
    from holmes.core.supabase_dal import SupabaseDal

# Saturation logging (ROB-759) is transition-based, not periodic: the single
# INFO line fires only after this long of CONTINUOUS zero-free-slots; the
# stuck-slot WARNING repeats at most every _STUCK_WARN_RATE_LIMIT_SECONDS.
_SATURATION_LOG_AFTER_SECONDS = 60.0
_STUCK_WARN_RATE_LIMIT_SECONDS = 300.0
# See ConversationExecutor._loop.
_LOOP_SAFETY_TIMEOUT_SECONDS = 300.0


class _ActiveTask:
    """An in-flight conversation: the task itself plus when it took its slot."""

    __slots__ = ("task", "started")

    def __init__(self, task: ConversationTask, started: float):
        self.task = task
        self.started = started


class ConversationExecutor:
    """One named executor: its claim loop, in-flight set and thread pool.

    The loop only wakes on ``wake()`` (a broadcast naming this executor, the
    registry's discovery poll, or a slot freeing). Each wake claims at most
    ``free_slots()`` pending rows naming this executor and hands them to the
    processor on the pool. ``max_concurrent`` arrives already clamped by
    ``ExecutorSizing``; the pool is created with ``THREAD_CEILING`` threads
    (spawned lazily) so ``set_max_concurrent()`` can raise the limit live.
    """

    def __init__(
        self,
        name: str,
        max_concurrent: int,
        dal: "SupabaseDal",
        holmes_id: str,
        processor: "ConversationProcessor",
    ):
        self.name = name
        self.dal = dal
        self.holmes_id = holmes_id
        self.processor = processor
        self.max_concurrent = max(1, int(max_concurrent))
        self.notify_event = threading.Event()
        # One lock for the running flag, the pool and the in-flight set, so
        # "may I still submit this claimed row?" and shutdown are decided
        # atomically here and nowhere else.
        self._lock = threading.Lock()
        self._running = False
        self._pool: Optional[ThreadPoolExecutor] = None
        self._thread: Optional[threading.Thread] = None
        self._active: Dict[tuple, _ActiveTask] = {}
        # See _SATURATION_LOG_AFTER_SECONDS. None sentinels, never 0.0:
        # time.monotonic() can be small on a fresh host.
        self._saturated_since: Optional[float] = None
        self._saturation_logged = False
        self._last_stuck_warn: Optional[float] = None

    # ---- lifecycle ----

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._pool = ThreadPoolExecutor(
                max_workers=THREAD_CEILING,
                thread_name_prefix=f"conversation-executor-{self.name}",
            )
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"conversation-claim-loop-{self.name}",
        )
        self._thread.start()
        logging.info(
            "Conversation executor %r started (max_concurrent=%d)",
            self.name,
            self.max_concurrent,
        )

    def shutdown(self) -> None:
        """Stop claiming and accepting work, and wake the loop so it exits.
        Does not block: in-flight conversations keep running on their threads
        and are retired by the runtime's shutdown sweep."""
        with self._lock:
            self._running = False
            pool, self._pool = self._pool, None
        self.notify_event.set()
        if pool is not None:
            pool.shutdown(wait=False)

    def join(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def wake(self) -> None:
        self.notify_event.set()

    def set_max_concurrent(self, value: int) -> bool:
        """Apply a new size (already clamped by ``ExecutorSizing``) without
        recreating the pool. Returns True when it changed."""
        new = max(1, int(value))
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

    def _loop(self) -> None:
        # Event-driven, with a slow self-check so an executor that missed a
        # wake (or outlived the discovery loop) still drains its backlog.
        while self._running:
            self.notify_event.wait(timeout=_LOOP_SAFETY_TIMEOUT_SECONDS)
            if not self._running:
                break
            self.notify_event.clear()
            try:
                self.claim_and_dispatch()
            except Exception:
                logging.exception(
                    "Error in conversation executor %r claim loop",
                    self.name,
                    exc_info=True,
                )

    # ---- claiming ----

    def claim_and_dispatch(self) -> None:
        """Claim up to ``free_slots()`` pending rows naming this executor and
        submit each to the pool. The claim RPC already set them 'running'; the
        surplus stays 'pending' for another instance."""
        if not self._running:
            # Shutting down: leave pending rows for another instance instead of
            # claiming them only to retire them.
            return
        free = self.free_slots()
        if free <= 0:
            # Logged transition-based so a full pool is distinguishable from a
            # dead claim loop (ROB-759).
            self.note_saturation()
            return
        self.note_capacity_available(free)
        claimed = self.dal.claim_n_pending_conversations(
            self.holmes_id, free, executor=self.name
        )
        if claimed:
            logging.info(
                "Executor %r claimed %d conversation(s) (free slots=%d)",
                self.name,
                len(claimed),
                free,
            )
        for conv in claimed:
            task = ConversationTask.from_row(conv)
            if task is None:
                self.processor.fail_unparsed_row(
                    conv, "Failed to parse conversation row"
                )
                continue
            self._dispatch(task)

    def _dispatch(self, task: ConversationTask) -> None:
        """Submit a claimed (already 'running') row to the pool, or retire it.

        No DB write on the happy path: the claim set 'running'. A
        request_sequence bumped after the claim (stop/retry) is caught later as
        ConversationReassignedError.
        """
        with self._lock:
            if self._running and self._pool is not None:
                self._track_locked(task)
                try:
                    self._pool.submit(self._run, task)
                    return
                except RuntimeError:
                    self._untrack_locked(task)
        # The claim already set the row 'running' with our assignee and this
        # executor is shut down, so nothing else would ever finish it: retire
        # it now the same way the shutdown sweep retires in-flight turns,
        # instead of leaving it for the stale-conversation sweep.
        logging.warning(
            "Executor %r unavailable; retiring claimed conversation %s",
            self.name,
            task.conversation_id,
        )
        self.processor.retire(task)

    def _run(self, task: ConversationTask) -> None:
        try:
            self.processor.run(task)
        finally:
            self.untrack(task)
            # A slot freed up: re-claim pending rows.
            self.wake()

    # ---- capacity ----

    def track(self, task: ConversationTask) -> None:
        """Count ``task`` as in flight (what ``_dispatch`` does before submitting)."""
        with self._lock:
            self._track_locked(task)

    def untrack(self, task: ConversationTask) -> None:
        with self._lock:
            self._untrack_locked(task)

    def _track_locked(self, task: ConversationTask) -> None:
        self._active[task.active_key] = _ActiveTask(task, time.monotonic())

    def _untrack_locked(self, task: ConversationTask) -> None:
        self._active.pop(task.active_key, None)

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def active_tasks(self) -> List[ConversationTask]:
        with self._lock:
            return [entry.task for entry in self._active.values()]

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
            with self._lock:
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
            with self._lock:
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

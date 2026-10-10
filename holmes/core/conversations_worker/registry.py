"""Which executors exist, and how big (ROB-1369).

The registry starts with no executors. One is created the first time DB-backed
discovery (``pending_conversation_executors()``) reports pending rows for a
name, so a stray broadcast cannot spawn an executor no row needs. Discovery
runs on every safety-net poll, on every (re)subscribe drain, and whenever a
broadcast names an executor that does not exist yet.
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from holmes.common.env_vars import (
    CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITH_REALTIME,
    CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITHOUT_REALTIME,
)
from holmes.core.conversations_worker.executor import ConversationExecutor
from holmes.core.conversations_worker.models import (
    EXECUTOR_UNAVAILABLE_ERROR_CODE,
    AUTO_EXECUTOR,
    MANUAL_EXECUTOR,
    ConversationTask,
    is_valid_executor_name,
)
from holmes.core.conversations_worker.sizing import ExecutorSizing

if TYPE_CHECKING:
    from holmes.core.conversations_worker.processor import ConversationProcessor
    from holmes.core.supabase_dal import SupabaseDal

# Executors one process creates on demand, so a bogus executor name in a
# broadcast or a row cannot spawn unbounded thread pools.
MAX_EXECUTORS = 16
# Rejected names are logged at most this often, per cause.
_REJECT_LOG_RATE_LIMIT_SECONDS = 300.0
# Rows naming an executor this instance cannot run are failed instead of left
# 'pending' forever; this many per discovery tick.
_UNAVAILABLE_FAIL_BATCH = 20


class ExecutorRegistry:
    """The executors of one runtime: creation on demand, the cap, live
    resizing from account settings, and the discovery loop that drives them."""

    def __init__(
        self,
        dal: "SupabaseDal",
        holmes_id: str,
        processor: "ConversationProcessor",
        sizing: Optional[ExecutorSizing] = None,
        max_executors: int = MAX_EXECUTORS,
    ):
        self.dal = dal
        self.holmes_id = holmes_id
        self.processor = processor
        self.sizing = sizing or ExecutorSizing()
        self.max_executors = max(1, int(max_executors))
        self._executors: Dict[str, ConversationExecutor] = {}
        self._lock = threading.Lock()
        self._started = False
        self._discovery_event = threading.Event()
        self._discovery_thread: Optional[threading.Thread] = None
        self._last_reject_log: Dict[str, float] = {}

    # ---- lifecycle ----

    def start(
        self, realtime_connected: Callable[[], bool], discover_immediately: bool
    ) -> None:
        """Start the discovery loop. With Realtime, the SUBSCRIBED drain wakes
        it for the first discovery (so the subscription exists before the first
        claim); without, it discovers immediately."""
        self._started = True
        self._discovery_event.clear()
        self._discovery_thread = threading.Thread(
            target=self._discovery_loop,
            args=(realtime_connected, discover_immediately),
            daemon=True,
            name="conversation-executor-discovery",
        )
        self._discovery_thread.start()

    def quiesce(self) -> None:
        """Stop discovery and every executor's claiming, without blocking and
        without forgetting in-flight work: ``active_tasks()`` still lists the
        turns running on the executors, so the runtime's shutdown sweep can
        retire them. Must run before that sweep: retiring a turn frees its
        slot, which wakes its executor, which would otherwise claim a fresh
        row that nothing retires."""
        with self._lock:
            self._started = False
            executors = list(self._executors.values())
        self._discovery_event.set()
        for ex in executors:
            try:
                ex.shutdown()
            except Exception:
                logging.debug("Executor %r shutdown failed", ex.name, exc_info=True)

    def stop(self) -> None:
        self.quiesce()
        with self._lock:
            executors = list(self._executors.values())
            self._executors = {}
        # The executors are already shut down (quiesce); a claim loop blocked
        # in its own dispatch sees that before it is joined here.
        for ex in executors:
            ex.join()
        if self._discovery_thread is not None:
            # Bounded: the loop checks _started on every wake.
            self._discovery_thread.join(timeout=5)
            self._discovery_thread = None

    # ---- executors ----

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._executors)

    def get(self, name: str) -> Optional[ConversationExecutor]:
        with self._lock:
            return self._executors.get(name)

    def active_tasks(self) -> List[ConversationTask]:
        with self._lock:
            executors = list(self._executors.values())
        tasks: List[ConversationTask] = []
        for ex in executors:
            tasks.extend(ex.active_tasks())
        return tasks

    def get_or_create(
        self, name: str, account_sizes: Optional[Dict[str, int]] = None
    ) -> Optional[ConversationExecutor]:
        """The executor for ``name``, created on first use.

        None (logged, rate-limited) for an invalid name, before start / after
        stop, or once ``max_executors`` exist. Discovery fails the pending rows
        of such a name so the request surfaces as an error instead of hanging
        (see ``_fail_pending_rows``).
        """
        if not is_valid_executor_name(name):
            self._log_reject("invalid executor name %r", name)
            return None
        ex = self.get(name)
        if ex is not None:
            return ex
        if not self._started:
            return None
        # The size lookup may hit Supabase; keep it out of the lock so a slow
        # read does not stall the other executors.
        if account_sizes is None:
            account_sizes = self.account_sizes()
        size = self.sizing.size_for(name, account_sizes)
        with self._lock:
            ex = self._executors.get(name)
            if ex is not None:
                return ex
            if not self._started:
                return None
            if len(self._executors) >= self.max_executors:
                self._log_reject(
                    "executor %r not created: %d executors already exist (limit %d)",
                    name,
                    len(self._executors),
                    self.max_executors,
                )
                return None
            ex = ConversationExecutor(
                name=name,
                max_concurrent=size,
                dal=self.dal,
                holmes_id=self.holmes_id,
                processor=self.processor,
            )
            self._executors[name] = ex
            # Started under the lock so stop() cannot detach the executor
            # between the insert and the start and leave an untracked claim loop.
            ex.start()
        return ex

    def account_sizes(self) -> Any:
        """The raw ``conversation_executors`` account setting; ``ExecutorSizing``
        validates it."""
        try:
            return self.dal.get_conversation_executor_sizes() or {}
        except Exception:
            logging.warning(
                "Could not read account executor sizes; using built-in sizes",
                exc_info=True,
            )
            return {}

    def _log_reject(self, msg: str, *args: Any) -> None:
        # Rate-limited per message so one noisy cause cannot mask another.
        now = time.monotonic()
        last = self._last_reject_log.get(msg)
        if last is not None and now - last < _REJECT_LOG_RATE_LIMIT_SECONDS:
            return
        self._last_reject_log[msg] = now
        logging.warning(msg, *args)

    # ---- wake-ups ----

    def on_pending(self, executor: Optional[str] = None) -> None:
        """Routing target for 'pending_conversations' broadcasts. Non-blocking.

        A broadcast naming an existing executor wakes exactly that executor.
        Anything else — a name without an executor yet, a broadcast without a
        name (a (re)subscribe drain, a pgchanges notification) — goes through
        discovery, which creates executors only for names that really have
        pending rows.
        """
        if executor is not None and is_valid_executor_name(executor):
            ex = self.get(executor)
            if ex is not None:
                ex.wake()
                return
        elif executor is not None:
            self._log_reject(
                "pending_conversations broadcast named invalid executor %r; "
                "falling back to discovery",
                executor,
            )
        self._discovery_event.set()

    # ---- discovery ----

    def discover(self) -> None:
        """Ask the DB which executors have pending rows; wake (or create) each.

        Existing executors are woken too — the poll is the at-most-once safety
        net for a lost broadcast, and a woken executor with nothing to claim
        costs one cheap RPC. Sizes are re-resolved on every tick so a settings
        change applies without a restart.
        """
        names: List[str] = self.dal.list_pending_conversation_executors()
        account_sizes = self.account_sizes()
        with self._lock:
            existing = list(self._executors.values())
        for ex in existing:
            ex.set_max_concurrent(self.sizing.size_for(ex.name, account_sizes))
        # DB order (by name), then executors with no pending rows: which names
        # get an executor when the cap is hit must not depend on set order.
        for name in dict.fromkeys([*names, *(ex.name for ex in existing)]):
            created = self.get_or_create(name, account_sizes)
            if created is not None:
                created.wake()
            elif name in names and self._started:
                self._fail_pending_rows(name)

    def _fail_pending_rows(self, name: str) -> None:
        """Claim and fail rows naming an executor this instance will never run.

        Every instance applies the same cap and name rule, so such a row would
        otherwise sit 'pending' with no error until its claim window closes.
        """
        if not is_valid_executor_name(name):
            description = f"Invalid Holmes executor name {name!r}"
        else:
            description = (
                f"No Holmes executor available for {name!r}: this agent "
                f"already runs {len(self.names())} executors "
                f"(limit {self.max_executors}). Use {MANUAL_EXECUTOR!r} or "
                f"{AUTO_EXECUTOR!r}."
            )
        try:
            claimed = self.dal.claim_n_pending_conversations(
                self.holmes_id, _UNAVAILABLE_FAIL_BATCH, executor=name
            )
        except Exception:
            logging.exception(
                "Failed to claim rows of unavailable executor %r", name, exc_info=True
            )
            return
        for conv in claimed:
            task = ConversationTask.from_row(conv)
            if task is None:
                continue
            logging.warning(
                "Failing conversation %s: %s", task.conversation_id, description
            )
            self.processor.fail(
                task, description, error_code=EXECUTOR_UNAVAILABLE_ERROR_CODE
            )

    def _discovery_loop(
        self, realtime_connected: Callable[[], bool], discover_immediately: bool
    ) -> None:
        if discover_immediately:
            self._run_discovery(triggered=False)
        while self._started:
            if realtime_connected():
                timeout = CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITH_REALTIME
            else:
                timeout = CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITHOUT_REALTIME
            triggered = self._discovery_event.wait(timeout=timeout)
            if not self._started:
                break
            self._discovery_event.clear()
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                logging.debug(
                    "Executor discovery tick (triggered=%s, realtime=%s, executors=%s)",
                    triggered,
                    realtime_connected(),
                    self.names(),
                )
            self._run_discovery(triggered)

    def _run_discovery(self, triggered: bool) -> None:
        try:
            self.discover()
        except Exception:
            logging.exception(
                "Error in executor discovery (triggered=%s)", triggered, exc_info=True
            )

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING, Union

from starlette.requests import Request

from holmes.common.env_vars import (
    CONVERSATION_WORKER_EVENT_BATCH_INTERVAL_SECONDS,
    CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITH_REALTIME,
    CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITHOUT_REALTIME,
    CONVERSATION_WORKER_REALTIME_ENABLED,
    CONVERSATION_WORKER_REALTIME_VERIFY_INITIAL_BACKOFF_SECONDS,
    CONVERSATION_WORKER_REALTIME_VERIFY_MAX_BACKOFF_SECONDS,
)
from holmes.core.conversations import build_chat_messages
from holmes.core.conversations_worker.event_publisher import (
    ConversationEventPublisher,
)
from holmes.core.conversations_worker.executors import (
    ConversationExecutor,
    ExecutorSettings,
    _ActiveTask,
    is_valid_executor_name,
)
from holmes.core.conversations_worker.models import (
    DEFAULT_EXECUTOR,
    EVENT_USER_MESSAGE,
    ConversationReassignedError,
    ConversationStatus,
    ConversationTask,
)
from holmes.core.conversations_worker.realtime_manager import RealtimeWorker
from holmes.core.conversations_worker.tool_call_worker import ToolCallWorker
from holmes.core.models import ChatRequest
from holmes.core.conversation_links import resolve_conversation_link
from holmes.core.supabase_dal import SupabaseDnsException
from postgrest.exceptions import APIError as PGAPIError
from holmes.core.prompt import PromptComponent
from holmes.core.tools import PrerequisiteCacheMode, ToolsetTag
from holmes.core.tools_utils.filesystem_result_storage import (
    tool_result_storage,
)
from holmes.core.tools_utils.frontend_tools import (
    FrontendToolCollisionError,
    inject_frontend_tools,
)
from holmes.core.tracing import TracingFactory, langfuse_trace_attributes
from holmes.core.usage_recorder import (
    build_chat_recorder_state,
    stream_with_usage_recording,
)
from holmes.utils.holmes_status import update_holmes_status_in_db
from holmes.core.relay_refusal import RELAY_REFUSAL_ERROR_CODES, RelayRefusal
from holmes.utils.stream import StreamEvents

if TYPE_CHECKING:
    from fastapi.responses import StreamingResponse
    from holmes.config import Config
    from holmes.core.models import ChatResponse
    from holmes.core.supabase_dal import SupabaseDal

ChatFunction = Callable[
    [ChatRequest, Request], Union["ChatResponse", "StreamingResponse"]
]

__all__ = ["ConversationWorker", "_ActiveTask"]

# Shutdown handling. When the pod is asked to stop (SIGTERM from a rollout,
# node drain, scale-down), whatever conversations we are mid-turn on are never
# going to finish: the executors are not drained, the threads are daemons, and
# nothing else picks the row back up (the claim RPCs only take 'pending').
# Before this, the row simply stayed 'running' with our now-dead assignee until
# the pg_cron stale sweep retired it hours later — a spinner in the UI the whole
# time. We now retire them ourselves: an error event carrying the reason below,
# then status 'timeout'.
SHUTDOWN_REASON = "Holmes Restarted"
SHUTDOWN_ERROR_DESCRIPTION = (
    f"{SHUTDOWN_REASON} — this request was interrupted before it finished. "
    "Ask again to retry."
)
# Distinct from the generic 5000 so this is greppable and the FE can special-case
# it later; unmapped codes render `description` as-is today.
SHUTDOWN_ERROR_CODE = 5205
# Wall-clock budget for the whole retirement sweep. It is sequential and each
# row costs up to two DAL calls, each retrying 3 times with backoff, so a slow
# or unreachable Supabase could otherwise eat the container's termination grace
# period and earn us a SIGKILL — leaving the remaining rows 'running', the very
# thing this is here to prevent. Rows we don't reach fall back to the pg_cron
# stale sweep, exactly as they did before.
SHUTDOWN_RETIRE_BUDGET_SECONDS = 10.0

# Unknown executor names beyond the pool cap are logged at most this often.
_EXECUTOR_REJECT_LOG_RATE_LIMIT_SECONDS = 300.0


class ConversationWorker:
    """
    Conversation Worker.

    Active participant that picks up pending Conversation rows from Supabase,
    runs them through the existing /api/chat pipeline (via chat_function),
    and writes results back as ConversationEvents in real-time.

    Lifecycle: pending → running (claimed + processing) → completed/failed.
    The claim RPC lands a row directly in 'running' ('queued' is deprecated), so
    a conversation waiting for capacity stays 'pending'.

    Executors (ROB-1369): every row names an executor. The worker starts with
    NO executor pools — only the Realtime listener and a discovery loop. A pool
    is created the first time a pending conversation names it (from a
    'pending_conversations' broadcast carrying ``executor``, or from the
    discovery poll, which asks the DB which executor names currently have
    pending rows). Each pool claims only rows naming its executor, so 'manual'
    user asks never wait behind 'auto' background investigations.
    """

    def __init__(
        self,
        dal: "SupabaseDal",
        config: "Config",
        chat_function: ChatFunction,
        executor_settings: Optional[ExecutorSettings] = None,
    ):
        self.dal = dal
        self.config = config
        self.chat_function = chat_function
        # Globally-unique process id (presence key + assignee). hostname alone
        # isn't unique across pod restarts/replicas, so add pid + short uuid4.
        hostname = os.environ.get("HOSTNAME") or "local"
        self.holmes_id = f"{hostname}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        self._running = False
        self._active_started = False

        self._executor_settings = executor_settings or ExecutorSettings.from_env()
        self._executors: Dict[str, ConversationExecutor] = {}
        self._executors_lock = threading.Lock()
        self._last_executor_reject_log: Dict[str, float] = {}

        # Discovery loop: the safety-net poll (Realtime is at-most-once) and the
        # target of executor-less wakeups (reconnects, older publishers). It
        # asks the DB which executors have pending rows and wakes/creates them.
        self._discovery_thread: Optional[threading.Thread] = None
        self._discovery_event = threading.Event()

        # Guards the _running check + executor.submit against the stop() race.
        self._dispatch_lock = threading.Lock()

        self._realtime_manager: Optional[RealtimeWorker] = None

        # Executes cross-cluster remote tool calls (RemoteToolCalls rows) in
        # its own pool; RealtimeWorker routes 'pending_tool_calls' broadcasts
        # to it (same holmes:submit channel the conversation worker uses).
        self._tool_call_worker = ToolCallWorker(
            dal=self.dal, config=self.config, holmes_id=self.holmes_id
        )

        # Background thread that verifies Supabase Realtime is actually
        # enabled by calling the is_realtime_enabled() RPC.  HolmesStatus
        # advertises supports_realtime_conversations=False on startup and
        # only flips to True once the verifier gets a definitive True from
        # Supabase. On a definitive False the verifier shuts the worker
        # down. Connectivity errors trigger an exponential backoff retry
        # — we keep retrying until Supabase responds.
        self._realtime_verify_thread: Optional[threading.Thread] = None
        # Used by the verifier to wait between retries; setting it during
        # stop() makes the thread exit promptly.
        self._realtime_verify_stop = threading.Event()

    def start(self) -> None:
        if not self.dal.enabled:
            logging.info("ConversationWorker not started - Supabase DAL not enabled")
            return
        if self._running:
            logging.warning("ConversationWorker is already running")
            return

        # We mark the worker as running so stop() / status checks see a
        # consistent state, but defer spinning up the discovery loop and
        # Realtime subscription until the verifier confirms Supabase
        # Realtime is actually enabled.  Until then we don't poll or
        # subscribe — that would be wasted load against a project that
        # doesn't support our use case.
        self._running = True

        self._realtime_verify_stop.clear()
        self._realtime_verify_thread = threading.Thread(
            target=self._realtime_verify_loop,
            daemon=True,
            name="conversation-realtime-verify",
        )
        self._realtime_verify_thread.start()

        logging.info(
            "ConversationWorker waiting for Supabase Realtime verification "
            "(holmes_id=%s, account=%s, cluster=%s)",
            self.holmes_id,
            self.dal.account_id,
            self.dal.cluster,
        )

    def _start_active_workers(self) -> None:
        """
        Spin up the components that consume conversations — the Realtime
        manager (optional), the discovery loop and the tool-call worker.
        Executor pools are NOT created here; see _get_or_create_executor.

        Called by the verifier once Supabase confirms Realtime is enabled.
        Idempotent: if already started (re-entrant call), returns early.
        """
        if self._active_started:
            return
        self._active_started = True

        if CONVERSATION_WORKER_REALTIME_ENABLED:
            try:
                self._realtime_manager = RealtimeWorker(
                    dal=self.dal,
                    holmes_id=self.holmes_id,
                    conversation_worker=self,
                    tool_call_worker=self._tool_call_worker,
                )
                self._realtime_manager.start()
            except Exception:
                logging.warning(
                    "Failed to start Realtime manager; continuing with polling only",
                    exc_info=True,
                )
                self._realtime_manager = None

        self._discovery_event.clear()
        self._discovery_thread = threading.Thread(
            target=self._discovery_loop,
            daemon=True,
            name="conversation-executor-discovery",
        )
        self._discovery_thread.start()

        try:
            self._tool_call_worker.start(realtime_connected_fn=self._realtime_connected)
        except Exception:
            logging.exception("Failed to start ToolCallWorker", exc_info=True)

        logging.info(
            "ConversationWorker active (holmes_id=%s, account=%s, cluster=%s, "
            "realtime=%s, builtin_executor_sizes=%s, base_size=%d, "
            "account_executor_sizes=%s)",
            self.holmes_id,
            self.dal.account_id,
            self.dal.cluster,
            self._realtime_manager is not None,
            self._executor_settings.builtin_sizes,
            self._executor_settings.base_size,
            self._account_executor_sizes(),
        )

    def stop(self) -> None:
        logging.info("Stopping ConversationWorker...")
        self._running = False
        self._discovery_event.set()
        self._realtime_verify_stop.set()
        # Retire whatever we're mid-turn on before tearing the pools down. Must
        # happen while the rows still carry our assignee and 'running' status —
        # both RPCs guard on that. Flipping the status also makes any straggler
        # write from the in-flight thread fail with MISMATCH, which the
        # publisher already handles as ConversationReassignedError, so the
        # abandoned turn unwinds quietly instead of racing us.
        try:
            self._timeout_active_conversations()
        except Exception:
            logging.exception(
                "Failed to retire in-flight conversations during shutdown",
                exc_info=True,
            )
        try:
            self._tool_call_worker.stop()
        except Exception:
            logging.debug("ToolCallWorker stop failed", exc_info=True)

        if self._realtime_manager:
            try:
                self._realtime_manager.stop()
            except Exception:
                logging.exception("Error stopping realtime manager", exc_info=True)
        # Let any in-flight _dispatch finish before shutting the pools down.
        # Only the non-blocking shutdown happens under the lock: joining a
        # claim loop that is itself waiting on _dispatch_lock would deadlock.
        with self._dispatch_lock:
            with self._executors_lock:
                executors = list(self._executors.values())
                self._executors = {}
            for ex in executors:
                try:
                    ex.shutdown_pool()
                except Exception:
                    logging.debug("Executor %r stop failed", ex.name, exc_info=True)
        for ex in executors:
            ex.join()
        if self._discovery_thread:
            # Bounded join: the loop wakes once per notify or poll interval and
            # checks ``self._running``; if it is somehow stuck we still return
            # promptly rather than hang the shutdown path.
            self._discovery_thread.join(timeout=5)
            self._discovery_thread = None
        self._active_started = False
        # Drop the realtime manager handle so a subsequent start() can
        # bring up a fresh one. The reference itself was already torn
        # down above via _realtime_manager.stop().
        self._realtime_manager = None
        # Don't join the verify thread from inside itself — when the
        # verifier triggers stop() on a definitive False, it's running on
        # this very thread. ``current_thread()`` lets us skip the join in
        # that case; the daemon flag guarantees it won't outlive the
        # process.
        if (
            self._realtime_verify_thread
            and self._realtime_verify_thread is not threading.current_thread()
        ):
            self._realtime_verify_thread.join(timeout=5)
            self._realtime_verify_thread = None
        logging.info("ConversationWorker stopped")

    # ---- realtime verifier ----

    def _realtime_verify_loop(self) -> None:
        """
        Repeatedly call ``is_realtime_enabled()`` until Supabase gives a
        definitive answer. We keep retrying on connectivity errors with
        exponential backoff so a transient network blip doesn't cause us
        to either silently advertise stale capabilities or shut the
        worker down prematurely.

        Outcomes:
            * Definitive ``True``  → flip HolmesStatus.supports_realtime_*
              to their env-var-driven values and exit the loop.
            * Definitive ``False`` → log and call ``self.stop()``; status
              fields stay at their default ``False``.
            * Connectivity error  → wait with exponential backoff and try
              again.
        """
        backoff = CONVERSATION_WORKER_REALTIME_VERIFY_INITIAL_BACKOFF_SECONDS
        max_backoff = CONVERSATION_WORKER_REALTIME_VERIFY_MAX_BACKOFF_SECONDS

        while self._running and not self._realtime_verify_stop.is_set():
            try:
                result = self.dal.is_realtime_enabled()
            except (
                SupabaseDnsException,
                PGAPIError,
                ConnectionError,
                TimeoutError,
                OSError,
            ):
                # Transient — keep retrying with backoff.
                logging.warning(
                    "Connectivity error in realtime verify loop; will retry with backoff",
                    exc_info=True,
                )
                result = None
            except Exception:
                # is_realtime_enabled() already converts transport errors
                # to None, so an exception escaping here is almost certainly
                # a programming defect. Surface it loudly and stop the
                # verify thread instead of silently retrying forever; the
                # worker will continue in polling-only / unverified mode,
                # but the failure will be visible in logs/alerts.
                logging.exception(
                    "Unexpected error in realtime verify loop; not retrying",
                )
                raise

            if result is True:
                logging.info(
                    "Supabase Realtime is enabled — starting conversation "
                    "polling/subscription and updating HolmesStatus"
                )
                try:
                    update_holmes_status_in_db(
                        self.dal, self.config, realtime_available=True
                    )
                except Exception:
                    logging.exception(
                        "Failed to update HolmesStatus after realtime " "verification",
                        exc_info=True,
                    )
                # Spin up the discovery loop and (if enabled) Realtime
                # subscription now that we know they'll do useful work. If
                # stop() raced us, _running is already False — don't bring up
                # workers that will immediately need to be torn down.
                if self._running and not self._realtime_verify_stop.is_set():
                    try:
                        self._start_active_workers()
                    except Exception:
                        logging.exception(
                            "Failed to start active workers after realtime "
                            "verification",
                            exc_info=True,
                        )
                return

            if result is False:
                logging.warning(
                    "Supabase Realtime is not enabled on this project — "
                    "shutting down ConversationWorker"
                )
                # HolmesStatus already advertises false by default, so no
                # further write is needed. Trigger a shutdown — note that
                # stop() detects we're calling from the verify thread and
                # skips the self-join.
                try:
                    self.stop()
                except Exception:
                    logging.exception(
                        "Error during ConversationWorker shutdown after "
                        "realtime check returned False",
                        exc_info=True,
                    )
                return

            # result is None — Supabase couldn't be reached. Wait and retry.
            logging.info(
                "is_realtime_enabled() inconclusive — retrying in %.1fs",
                backoff,
            )
            if self._realtime_verify_stop.wait(timeout=backoff):
                return  # stop() was called; bail out
            backoff = min(backoff * 2, max_backoff)

    # ---- executors ----

    def executor_names(self) -> List[str]:
        with self._executors_lock:
            return sorted(self._executors)

    def _get_or_create_executor(self, name: str) -> Optional[ConversationExecutor]:
        """The pool for ``name``, created on first use from the settings.

        Returns None (and logs, rate-limited) for an invalid name or when the
        process already holds ``max_executors`` pools — a bogus broadcast must
        not be able to spawn unbounded thread pools. Rows naming a rejected
        executor stay 'pending' — every instance runs the same validation —
        until the claim window closes.
        """
        if not is_valid_executor_name(name):
            self._log_executor_reject("invalid executor name %r", name)
            return None
        with self._executors_lock:
            ex = self._executors.get(name)
        if ex is not None:
            return ex
        if not self._running or not self._active_started:
            return None
        # The size lookup may hit Supabase; keep it out of the lock so a slow
        # read does not stall claim_pending_conversations for other executors.
        size = self._executor_settings.size_for(name, self._account_executor_sizes())
        with self._executors_lock:
            ex = self._executors.get(name)
            if ex is not None:
                return ex
            if not self._running or not self._active_started:
                return None
            if len(self._executors) >= self._executor_settings.max_executors:
                self._log_executor_reject(
                    "executor %r not created: %d executors already exist "
                    "(CONVERSATION_WORKER_MAX_EXECUTORS)",
                    name,
                    len(self._executors),
                )
                return None
            ex = ConversationExecutor(
                name=name,
                max_concurrent=size,
                thread_ceiling=self._executor_settings.thread_ceiling,
            )
            self._executors[name] = ex
            # Started under the lock so stop() cannot detach the pool between
            # the insert and the start and leave an untracked claim loop.
            ex.start(self._try_claim_and_dispatch)
        return ex

    def _account_executor_sizes(self) -> Dict[str, int]:
        try:
            return self.dal.get_conversation_executor_sizes() or {}
        except Exception:
            logging.warning(
                "Could not read account executor sizes; using env/built-in",
                exc_info=True,
            )
            return {}

    def _refresh_executor_sizes(self) -> None:
        """Apply account-setting changes (Settings → LLM Models / AI Triage) to
        running pools; called on every discovery tick."""
        with self._executors_lock:
            executors = list(self._executors.values())
        if not executors:
            return
        account_sizes = self._account_executor_sizes()
        for ex in executors:
            ex.set_max_concurrent(
                self._executor_settings.size_for(ex.name, account_sizes)
            )

    def _log_executor_reject(self, msg: str, *args: Any) -> None:
        # Rate-limited per message so one noisy cause cannot mask another.
        now = time.monotonic()
        last = self._last_executor_reject_log.get(msg)
        if last is not None and now - last < _EXECUTOR_REJECT_LOG_RATE_LIMIT_SECONDS:
            return
        self._last_executor_reject_log[msg] = now
        logging.warning(msg, *args)

    def claim_pending_conversations(self, executor: Optional[str] = None) -> None:
        """Routing target for RealtimeWorker on 'pending_conversations'
        broadcasts. Non-blocking.

        A broadcast naming an executor wakes exactly that pool. A name without
        a pool yet — and a broadcast without a name (an older publisher, a
        (re)subscribe drain, a pgchanges notification) — goes through the
        discovery loop, which asks the DB which executors have pending rows and
        creates pools for those only, so a stray broadcast cannot spawn a pool
        no row needs and use up the executor cap.
        """
        if executor is not None and is_valid_executor_name(executor):
            with self._executors_lock:
                ex = self._executors.get(executor)
            if ex is not None:
                ex.wake()
                return
        elif executor is not None:
            self._log_executor_reject(
                "pending_conversations broadcast named invalid executor %r; "
                "falling back to discovery",
                executor,
            )
        self._discovery_event.set()

    def _realtime_connected(self) -> bool:
        if self._realtime_manager is None:
            return False
        try:
            return bool(self._realtime_manager.is_connected())
        except Exception:
            return False

    # ---- discovery loop ----

    def _discovery_loop(self) -> None:
        # When Realtime is enabled, the SUBSCRIBED callback fires
        # claim_pending_conversations() which wakes this loop for the first
        # discovery — guaranteeing the subscription is established before we
        # try to claim. On reconnects the same callback fires again, so
        # conversations missed during a disconnect are drained. When Realtime
        # is disabled, discover immediately on startup.
        if self._realtime_manager is None:
            self._discover_and_wake()

        while self._running:
            if self._realtime_connected():
                timeout = CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITH_REALTIME
            else:
                timeout = CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITHOUT_REALTIME

            triggered = self._discovery_event.wait(timeout=timeout)
            if not self._running:
                break
            self._discovery_event.clear()
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                logging.debug(
                    "Executor discovery tick (triggered=%s, realtime=%s, executors=%s)",
                    triggered,
                    self._realtime_connected(),
                    self.executor_names(),
                )
            try:
                self._discover_and_wake()
            except Exception:
                logging.exception(
                    "Error in ConversationWorker discovery loop (triggered=%s)",
                    triggered,
                    exc_info=True,
                )

    def _discover_and_wake(self) -> None:
        """Ask the DB which executors have pending rows; wake (or create) each.

        Existing pools are woken too — the poll is the at-most-once safety net
        for a lost broadcast, and a woken pool with nothing to claim costs one
        cheap RPC.
        """
        names: List[str] = self.dal.list_pending_conversation_executors()
        self._refresh_executor_sizes()
        for name in set(names) | set(self.executor_names()):
            ex = self._get_or_create_executor(name)
            if ex is not None:
                ex.wake()

    # ---- claim + dispatch ----

    def _try_claim_and_dispatch(self, executor: ConversationExecutor) -> None:
        # Claim only as many pending rows naming this executor as it has free
        # slots and submit each straight to its pool (the claim already set them
        # 'running'). The surplus stays 'pending' for another instance.
        # _process_conversation_safe wakes the executor to re-claim as slots free.
        if not self._running:
            # Shutting down: leave pending rows for another instance instead of
            # claiming them only to retire them.
            return
        free = executor.free_slots()
        if free <= 0:
            # All slots occupied: pending rows stay unclaimed until a slot
            # frees. Logged transition-based so this is distinguishable from a
            # dead claim loop (ROB-759).
            executor.note_saturation()
            return
        executor.note_capacity_available(free)
        claimed = self.dal.claim_n_pending_conversations(
            self.holmes_id, free, executor=executor.name
        )
        if claimed:
            logging.info(
                "Executor %r claimed %d conversation(s) (free slots=%d)",
                executor.name,
                len(claimed),
                free,
            )
        for conv in claimed:
            task = self._build_task_from_conversation_row(conv, executor.name)
            if task is None:
                cid = conv.get("conversation_id")
                seq = conv.get("request_sequence")
                if cid and seq is not None:
                    try:
                        self._post_error_event(
                            ConversationTask(
                                conversation_id=cid,
                                account_id=conv.get("account_id", ""),
                                cluster_id=conv.get("cluster_id", ""),
                                origin=conv.get("origin", ""),
                                request_sequence=int(seq),
                                executor=executor.name,
                            ),
                            "Failed to parse conversation row",
                        )
                        self.dal.update_conversation_status(
                            conversation_id=cid,
                            request_sequence=int(seq),
                            assignee=self.holmes_id,
                            status="failed",
                        )
                    except Exception:
                        logging.exception(
                            "Failed to mark unparseable conversation %s as failed",
                            cid,
                            exc_info=True,
                        )
                continue
            self._dispatch(task, executor)

    def _dispatch(
        self, task: ConversationTask, executor: Optional[ConversationExecutor] = None
    ) -> None:
        """Submit a claimed (already 'running') conversation to its executor.

        No DB write here — the claim set 'running'. A request_sequence bumped
        after the claim (stop/retry) is caught later as ConversationReassignedError.
        """
        with self._dispatch_lock:
            if self._running:
                if executor is None:
                    executor = self._get_or_create_executor(task.executor)
                if executor is not None and executor.running:
                    executor.track(task)
                    try:
                        executor.submit(self._process_conversation_safe, task)
                        return
                    except RuntimeError:
                        executor.untrack(task)
        # The claim already set the row 'running' with our assignee, and the
        # shutdown sweep has run (or the pool is gone), so nothing else would
        # ever finish it: retire it now the same way stop() retires in-flight
        # turns, instead of leaving it for the stale-conversation sweep.
        logging.warning(
            "Executor %r unavailable; retiring claimed conversation %s",
            task.executor,
            task.conversation_id,
        )
        self._retire_unrunnable_task(task)

    def _retire_unrunnable_task(self, task: ConversationTask) -> None:
        try:
            self._post_error_event(
                task,
                SHUTDOWN_ERROR_DESCRIPTION,
                error_code=SHUTDOWN_ERROR_CODE,
                reason=SHUTDOWN_REASON,
            )
            self._timeout_conversation(task)
        except Exception:
            logging.exception(
                "Failed to retire claimed conversation %s",
                task.conversation_id,
                exc_info=True,
            )

    def _executor_for(self, task: ConversationTask) -> Optional[ConversationExecutor]:
        with self._executors_lock:
            return self._executors.get(task.executor)

    def _build_task_from_conversation_row(
        self, conv: Dict[str, Any], executor: Optional[str] = None
    ) -> Optional[ConversationTask]:
        try:
            return ConversationTask(
                conversation_id=conv["conversation_id"],
                account_id=conv["account_id"],
                cluster_id=conv["cluster_id"],
                origin=conv.get("origin", "chat"),
                request_sequence=int(conv.get("request_sequence", 1)),
                metadata=conv.get("metadata") or {},
                title=conv.get("title"),
                # RLS-bound owner; the per-turn identity (see _process_conversation).
                user_id=conv.get("user_id"),
                # The pool that claimed the row wins over the column: a filtered
                # claim can only return rows naming the claiming executor.
                executor=executor or conv.get("executor") or DEFAULT_EXECUTOR,
            )
        except Exception:
            logging.exception(
                "Failed to build conversation task from row (conversation_id=%s)",
                conv.get("conversation_id", "unknown"),
                exc_info=True,
            )
            return None

    # ---- error reporting helpers ----

    def _post_error_event(
        self,
        task: ConversationTask,
        description: str,
        error_code: int = 5000,
        raw_error: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Post an error event to ConversationEvents so subscribers can see the failure reason."""
        data: Dict[str, Any] = {
            "description": description,
            "error_code": error_code,
            "msg": description,
            "success": False,
        }
        # Short machine-readable cause (e.g. "Holmes Restarted") alongside the
        # human-readable description the UI renders. Kept separate so a caller
        # can group/filter on it without parsing prose.
        if reason is not None:
            data["reason"] = reason
        # Full upstream error, included only for Robusta-AI (relay) models where
        # the error originates from our own backend and is safe to surface.
        if raw_error is not None:
            data["raw_error"] = raw_error
        try:
            self.dal.post_conversation_events(
                conversation_id=task.conversation_id,
                assignee=self.holmes_id,
                request_sequence=task.request_sequence,
                events=[
                    {
                        "event": "error",
                        "data": data,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            )
        except Exception:
            logging.exception(
                "Failed to post error event for conversation %s",
                task.conversation_id,
                exc_info=True,
            )

    def _fail_conversation(
        self,
        task: ConversationTask,
        description: str,
        error_code: int = 5000,
        raw_error: Optional[str] = None,
    ) -> None:
        """Post an error event and then mark the conversation as failed."""
        self._post_error_event(task, description, error_code, raw_error=raw_error)
        try:
            self.dal.update_conversation_status(
                conversation_id=task.conversation_id,
                request_sequence=task.request_sequence,
                assignee=self.holmes_id,
                status="failed",
            )
        except Exception:
            logging.exception(
                "Failed to mark conversation %s as failed",
                task.conversation_id,
                exc_info=True,
            )

    # ---- shutdown ----

    def _timeout_active_conversations(self) -> None:
        """Mark every conversation this worker is still processing as timed out.

        Called from ``stop()`` — i.e. on a graceful shutdown (SIGTERM from a
        rollout / drain), not on SIGKILL or an OOM kill, where nothing of ours
        runs and the pg_cron stale sweep remains the backstop.

        Each row gets an error event carrying ``reason="Holmes Restarted"``
        first (post_conversation_events requires status='running', so the order
        matters) and is then transitioned to 'timeout'. The sweep is bounded by
        ``SHUTDOWN_RETIRE_BUDGET_SECONDS`` so a slow Supabase cannot hold the
        process past its termination grace period.
        """
        with self._executors_lock:
            executors = list(self._executors.values())
        entries: List[_ActiveTask] = []
        for ex in executors:
            entries.extend(ex.active_entries())
        if not entries:
            return

        logging.info(
            "Shutdown: marking %d in-flight conversation(s) as timed out (%s)",
            len(entries),
            SHUTDOWN_REASON,
        )
        deadline = time.monotonic() + SHUTDOWN_RETIRE_BUDGET_SECONDS
        for index, entry in enumerate(entries):
            if time.monotonic() >= deadline:
                logging.warning(
                    "Shutdown retirement budget (%.0fs) exhausted; leaving %d "
                    "conversation(s) to the stale sweep",
                    SHUTDOWN_RETIRE_BUDGET_SECONDS,
                    len(entries) - index,
                )
                break
            task = entry.task
            try:
                self._post_error_event(
                    task,
                    SHUTDOWN_ERROR_DESCRIPTION,
                    error_code=SHUTDOWN_ERROR_CODE,
                    reason=SHUTDOWN_REASON,
                )
                self._timeout_conversation(task)
            except Exception:
                logging.warning(
                    "Failed to time out conversation %s on shutdown",
                    task.conversation_id,
                    exc_info=True,
                )

    def _timeout_conversation(self, task: ConversationTask) -> None:
        """Transition one conversation to 'timeout'.

        'timeout' as a *target* status needs robusta-storage migration
        20260817121606, which is applied before this ships (see that
        migration's DEPLOY ORDER note).
        """
        try:
            self.dal.update_conversation_status(
                conversation_id=task.conversation_id,
                request_sequence=task.request_sequence,
                assignee=self.holmes_id,
                status=ConversationStatus.TIMEOUT.value,
            )
        except ConversationReassignedError:
            # The turn finished (or was stopped/retried) while we were shutting
            # down — whoever owns the row now has already set its status.
            return

    # ---- per-conversation processing ----

    def _process_conversation_safe(self, task: ConversationTask) -> None:
        try:
            self._process_conversation(task)
        except ConversationReassignedError as e:
            # Another worker claimed this conversation or the initiator bumped
            # request_sequence (e.g. stop_conversation) while we were working.
            # The DB already reflects the new state — do NOT call
            # update_conversation_status, which would either fail (status guard)
            # or race with the new owner.
            logging.warning(
                "Conversation %s was reassigned mid-process: %s",
                task.conversation_id,
                e,
            )
        except Exception as e:
            logging.exception(
                "Error processing conversation %s: %s",
                task.conversation_id,
                e,
                exc_info=True,
            )
            self._fail_conversation(
                task, "An internal error occurred while processing your request"
            )
        finally:
            executor = self._executor_for(task)
            if executor is not None:
                executor.untrack(task)
                # A slot freed up — wake this executor to re-claim pending rows.
                executor.wake()

    def _process_conversation(self, task: ConversationTask) -> None:
        events = self.dal.get_conversation_events(task.conversation_id)
        self._hydrate_task_from_events(task, events)

        data = task.user_message_data
        ask = data.get("ask")

        # A follow-up may carry only tool_decisions / frontend_tool_results
        # (no new user question). Holmes resumes the prior assistant turn.
        resume_only = bool(
            not ask
            and (data.get("tool_decisions") or data.get("frontend_tool_results"))
        )
        if resume_only:
            ask = self._extract_last_user_ask(task.conversation_history) or "Continue"

        if not ask:
            logging.warning(
                "Conversation %s has no user question, marking as failed",
                task.conversation_id,
            )
            self._fail_conversation(
                task, "No user question found in conversation events"
            )
            return

        publisher = ConversationEventPublisher(
            dal=self.dal,
            conversation_id=task.conversation_id,
            assignee=self.holmes_id,
            request_sequence=task.request_sequence,
            batch_interval_seconds=CONVERSATION_WORKER_EVENT_BATCH_INTERVAL_SECONDS,
        )

        # If tool_decisions are present, auto-enable tool approval.
        enable_tool_approval = bool(data.get("enable_tool_approval"))
        if data.get("tool_decisions"):
            enable_tool_approval = True

        # Identity comes from the RLS-bound Conversations row only. The event's
        # data is client-controlled; a user_id there that disagrees with the
        # row is rejected rather than trusted (ROB-1107).
        event_user_id = data.get("user_id")
        if event_user_id not in (None, "") and str(event_user_id) != str(
            task.user_id or ""
        ):
            logging.warning(
                "Conversation %s: user_message event user_id does not match "
                "the Conversations row owner; rejecting turn",
                task.conversation_id,
            )
            self._fail_conversation(
                task,
                "Conversation event identity does not match the conversation owner",
            )
            return
        resolved_user_id = task.user_id
        # Per-conversation OAuth opt-out. When a Conversations row carries
        # `metadata.oauth_enabled = false` (e.g. triggered workflows that
        # don't want Holmes acting under the workflow creator's per-user
        # OAuth tokens), drop user_id before it reaches ChatRequest so the
        # OAuth resolver in tool_calling_llm has no user to key on.
        oauth_enabled = (
            task.metadata.get("oauth_enabled", True) if task.metadata else True
        )
        if not oauth_enabled:
            resolved_user_id = None

        def from_event_or_conversation(key: str) -> Any:
            # Per-event presence wins, not truthiness — so an explicit empty
            # value from the FE (e.g. "" to deliberately clear a field) keeps
            # priority over the row-level metadata fallback and we don't
            # reintroduce stale Conversation-row values. Only fall back to
            # task.metadata when the per-turn event omits the key entirely.
            if key in data:
                return data[key]
            return task.metadata.get(key) if task.metadata else None

        resolved_user_email = from_event_or_conversation("user_email")
        resolved_request_source = from_event_or_conversation("request_source")
        # source_ref, request_type, and conversation_link are conversation-level
        # (one alert id / one creation-time classification / one originating
        # surface per chat), so the FE may put them on the Conversations row's
        # metadata instead of each per-turn event. A resolved None for
        # request_type still lets build_chat_recorder_state's auto-detection
        # run (Slack-prefix → 'slack_chat', fallback → 'user_chat').
        resolved_source_ref = from_event_or_conversation("source_ref")
        resolved_request_type = from_event_or_conversation("request_type")
        # Unlike its sibling fields, conversation_link deliberately ignores
        # per-turn events: nothing legitimately sends it per-event, so honoring
        # one would only let a client override the link relay stamped onto the
        # Conversations metadata (triggered workflows, alert triage).
        resolved_conversation_link = resolve_conversation_link(
            resolved_request_source,
            task.conversation_id,
            task.account_id,
            task.metadata.get("conversation_link") if task.metadata else None,
        )

        chat_request = ChatRequest(
            ask=ask,
            images=data.get("images"),
            model=data.get("model"),
            conversation_history=task.conversation_history,
            stream=True,
            additional_system_prompt=data.get("additional_system_prompt"),
            enable_tool_approval=enable_tool_approval,
            tool_decisions=data.get("tool_decisions"),  # type: ignore[arg-type]
            frontend_tools=data.get("frontend_tools"),  # type: ignore[arg-type]
            frontend_tool_results=data.get("frontend_tool_results"),  # type: ignore[arg-type]
            response_format=data.get("response_format"),
            behavior_controls=data.get("behavior_controls"),
            # meta / is_internal still come from the per-event blob only —
            # they're per-turn signals, not Conversation-level state.
            # user_id / user_email / request_type / request_source /
            # source_ref fall back to the Conversations row when the FE
            # didn't repeat them in the per-turn event. None for
            # request_type still lets build_chat_recorder_state's Slack
            # auto-detection and 'user_chat' default run.
            user_id=resolved_user_id,
            user_email=resolved_user_email,
            request_type=resolved_request_type,
            request_source=resolved_request_source,
            source_ref=resolved_source_ref,
            conversation_id=task.conversation_id,
            conversation_source="conversations",
            conversation_link=resolved_conversation_link,
            meta=data.get("meta"),
            is_internal=data.get("is_internal"),
        )

        self._run_chat_and_publish(
            task, chat_request, publisher, resume_only=resume_only
        )

    def _hydrate_task_from_events(
        self, task: ConversationTask, events: List[Dict[str, Any]]
    ) -> None:
        """Populate ``user_message_data`` and ``conversation_history`` from events.

        ``events`` is the flat chronological list returned by
        ``get_conversation_events``: ``[{event, data, ts}, ...]``.

        1. The LATEST ``user_message`` event's ``data`` dict becomes
           ``task.user_message_data`` — passed straight to ChatRequest.
           Exception: if a terminal event (``ai_answer_end`` /
           ``approval_required``) appears AFTER the latest user_message,
           that user_message has already been processed. ``user_message_data``
           is left empty so ``_process_conversation`` fails cleanly instead
           of silently re-running the stale question.
        2. The latest terminal event (``ai_answer_end`` / ``approval_required``)
           before that user_message provides the ``messages`` array used as
           ``conversation_history``.
        """
        current_user_idx: int = -1
        terminal_events = ("ai_answer_end", "approval_required")

        for idx, ev in enumerate(events):
            if ev.get("event") == EVENT_USER_MESSAGE:
                current_user_idx = idx

        if current_user_idx >= 0:
            already_answered = any(
                ev.get("event") in terminal_events
                for ev in events[current_user_idx + 1 :]
            )
            if not already_answered:
                task.user_message_data = events[current_user_idx].get("data") or {}

        upper = current_user_idx if current_user_idx >= 0 else len(events)
        for idx in range(upper - 1, -1, -1):
            ev = events[idx]
            if ev.get("event") in terminal_events:
                messages = (ev.get("data") or {}).get("messages")
                if messages:
                    task.conversation_history = messages
                    break

    @staticmethod
    def _extract_last_user_ask(history: Optional[list]) -> Optional[str]:
        """Pull the most recent user message text from an OpenAI-format history.

        Tolerates malformed (non-dict) entries by skipping them.
        """
        if not history:
            return None
        for msg in reversed(history):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str) and content:
                return content
            if isinstance(content, list):
                # Vision message: find the first text part
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text")
                        if isinstance(text, str) and text:
                            return text
        return None

    def _resolve_alert_name(
        self, task: ConversationTask, chat_request: ChatRequest
    ) -> Optional[str]:
        """The firing alert's ``GroupedIssues.aggregation_key``, for alert flows only.

        Returned only for alert conversations, so ordinary chat keeps being offered every
        skill (alert-scoped ones included, with their alert names in the description).

        BOTH id fields are needed: triage names the GroupedIssue in ``metadata.finding_id``
        and sets no ``source_ref``, while the FE's alert-investigation flow uses
        ``source_ref``. Wiring only ``source_ref`` leaves triage silently unfiltered.

        `request_type` comes from the Conversations metadata first -- relay persists
        'alert_investigation' there, while ChatRequest.request_type carries a different,
        backend-set taxonomy ('user_chat', 'scheduled_prompt', …).
        """
        meta = task.metadata or {}
        markers = {
            meta.get("request_type"),
            meta.get("request_source"),
            chat_request.request_type,
            chat_request.request_source,
        }
        if "alert_investigation" not in markers:
            return None

        issue_id = (
            meta.get("finding_id") or chat_request.source_ref or meta.get("source_ref")
        )
        if not issue_id:
            logging.debug(
                "Alert investigation %s carries no finding_id/source_ref; "
                "alert-scoped skills will not be filtered.",
                task.conversation_id,
            )
            return None

        try:
            issue = self.dal.get_issue_data(str(issue_id))
        except Exception:
            logging.warning(
                "Could not resolve issue %s for alert-scoped skills", issue_id
            )
            return None
        return (issue or {}).get("aggregation_key") or None

    def _run_chat_and_publish(
        self,
        task: ConversationTask,
        chat_request: ChatRequest,
        publisher: ConversationEventPublisher,
        resume_only: bool = False,
    ) -> None:
        """
        Run Holmes on the chat_request and stream StreamMessages into the publisher.
        Mirrors server.py::chat() for the streaming path but hands raw StreamMessages
        to the publisher instead of SSE-wrapping.
        """
        server_tracer = TracingFactory.create_tracer(
            trace_type=os.environ.get("HOLMES_TRACE_BACKEND")
        )

        # chat_request.user_id is the already-resolved "user Holmes may act on behalf of" --
        # the same value the per-user OAuth resolver keys on, so a conversation that opted out
        # via metadata.oauth_enabled = false loads no personal skills either.
        skills = self.config.get_skill_catalog(
            user_id=chat_request.user_id,
            alert_name=self._resolve_alert_name(task, chat_request),
        )

        prompt_component_overrides = None
        if chat_request.behavior_controls:
            prompt_component_overrides = {}
            for k, v in chat_request.behavior_controls.items():
                try:
                    prompt_component_overrides[PromptComponent(k.lower())] = v
                except ValueError:
                    pass

        storage = tool_result_storage()
        tool_results_dir = storage.__enter__()
        is_robusta_model = False
        try:
            ai = self.config.create_toolcalling_llm(
                dal=self.dal,
                toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLUSTER],
                enable_all_toolsets_possible=False,
                prerequisite_cache=PrerequisiteCacheMode.DISABLED,
                reuse_executor=True,
                model=chat_request.model,
                tracer=server_tracer,
                tool_results_dir=tool_results_dir,
            )
            is_robusta_model = ai.llm.is_robusta_model

            request_ai = self._inject_frontend_tools(ai, chat_request, task)
            if request_ai is None:
                return

            global_instructions = self.dal.get_global_instructions_for_account()
            if resume_only and chat_request.conversation_history:
                # Pure tool-decision / frontend-tool-result resume. Don't append
                # a new user message — call_stream consumes the existing history
                # plus tool_decisions to produce the next turn.
                messages = list(chat_request.conversation_history)
            else:
                messages = build_chat_messages(
                    chat_request.ask,
                    chat_request.conversation_history,
                    ai=ai,
                    config=self.config,
                    global_instructions=global_instructions,
                    additional_system_prompt=chat_request.additional_system_prompt,
                    skills=skills,
                    images=chat_request.images,
                    prompt_component_overrides=prompt_component_overrides,
                    conversation_link=chat_request.conversation_link,
                )

            # Write an initial ai_message event (optional) - skip; call_stream will emit events
            trace_span = server_tracer.start_trace("holmesgpt.investigation")
            trace_span.log(
                input=chat_request.ask,
                metadata={
                    "holmesgpt.investigation.question": chat_request.ask[:1024],
                    "holmesgpt.investigation.stream": True,
                    "holmesgpt.conversation_id": task.conversation_id,
                    # Langfuse trace-level attributes (user, session, metadata).
                    **langfuse_trace_attributes(
                        chat_request.ask,
                        user_id=chat_request.user_id,
                        user_email=chat_request.user_email,
                        account_id=task.account_id,
                        session_id=task.conversation_id,
                        cluster_id=task.cluster_id,
                        model=chat_request.model,
                        request_source=chat_request.request_source,
                    ),
                },
            )

            # Build request_context with user_id so per-user OAuth tools resolve
            # correctly inside call_stream (matches the regular /api/chat flow
            # in server.py). Also surface conversation_id and cluster_name so
            # the platform-mcp toolset can hardwire them onto its outbound
            # requests (as X-Robusta-* headers) — keeping them out of the
            # LLM-visible tool schema.
            request_context: Optional[Dict[str, Any]] = None
            if chat_request.user_id:
                request_context = {"user_id": chat_request.user_id}
            if task.user_id:
                # Row owner, sent to relay for RBAC even when user_id was
                # dropped by the OAuth opt-out.
                request_context = request_context or {}
                request_context["conversation_owner_id"] = task.user_id
            if task.conversation_id:
                request_context = request_context or {}
                request_context["conversation_id"] = task.conversation_id
            if self.config.cluster_name:
                request_context = request_context or {}
                request_context["cluster_name"] = self.config.cluster_name

            try:
                # Wrap the raw stream with the usage recorder BEFORE the
                # publisher consumes it, so the recorder sees Holmes' native
                # StreamMessage events (TOOL_RESULT / ANSWER_END / etc.) and
                # can fire one HolmesUsageEvents row per worker-driven turn.
                # Mirrors the wiring in server.py::chat() for the streaming
                # path; without this the worker bypasses the recorder entirely.
                recorder_state = build_chat_recorder_state(
                    chat_request,
                    request_ai,
                    dal=self.dal,
                    is_streaming=True,
                )
                raw_stream = request_ai.call_stream(
                    msgs=messages,
                    enable_tool_approval=chat_request.enable_tool_approval or False,
                    tool_decisions=chat_request.tool_decisions,
                    frontend_tool_results=chat_request.frontend_tool_results,
                    response_format=chat_request.response_format,
                    request_context=request_context,
                    trace_span=trace_span,
                )
                stream = stream_with_usage_recording(raw_stream, recorder_state)

                terminal = publisher.consume(stream)
                if terminal is None:
                    # The stream ended without a terminal event (or the
                    # terminal batch could not be saved). Post an explanatory
                    # error event before marking the conversation failed so
                    # the UI shows why instead of an unexplained status flip.
                    logging.error(
                        "Conversation %s ended without a terminal event",
                        task.conversation_id,
                    )
                    self._fail_conversation(
                        task, "Conversation ended without a terminal event"
                    )
                else:
                    status = self._terminal_to_status(terminal)
                    ok = self.dal.update_conversation_status(
                        conversation_id=task.conversation_id,
                        request_sequence=task.request_sequence,
                        assignee=self.holmes_id,
                        status=status,
                    )
                    if not ok:
                        logging.warning(
                            "Failed to mark conversation %s complete (status=%s)",
                            task.conversation_id,
                            status,
                        )
            finally:
                trace_span.end()
        except ConversationReassignedError as e:
            logging.warning(
                "Conversation %s was reassigned: %s", task.conversation_id, e
            )
        except RelayRefusal as e:
            # The platform refused the call on a Robusta-hosted model. Its
            # sentence is what the user has to act on, and the error code says
            # which refusal it was (ROB-1389).
            logging.warning(
                "Relay refused the chat for conversation %s (status %s): %s",
                task.conversation_id,
                e.status_code,
                e,
            )
            self._fail_conversation(
                task,
                str(e),
                error_code=RELAY_REFUSAL_ERROR_CODES[e.status_code],
                raw_error=str(e),
            )
        except Exception as e:
            logging.exception(
                "Error running chat for conversation %s: %s",
                task.conversation_id,
                e,
                exc_info=True,
            )
            # Surface the raw error only for Robusta-AI models (our own backend).
            raw_error = None
            if is_robusta_model:
                raw_error = str(e)
            self._fail_conversation(
                task,
                "An internal error occurred while processing your request",
                raw_error=raw_error,
            )
        finally:
            storage.__exit__(None, None, None)

    def _inject_frontend_tools(
        self,
        ai: Any,
        chat_request: ChatRequest,
        task: ConversationTask,
    ) -> Any:
        """Return the AI to use for ``call_stream``, or ``None`` if a name collision failed the conversation."""
        try:
            request_ai, _has_pause = inject_frontend_tools(
                ai, chat_request.frontend_tools
            )
        except FrontendToolCollisionError as e:
            self._fail_conversation(task, str(e), error_code=4000)
            return None
        return request_ai

    @staticmethod
    def _terminal_to_status(terminal: Optional[StreamEvents]) -> str:
        """Map the terminal StreamEvents value observed by the publisher to the
        string status we pass to ``update_conversation_status``."""
        if (
            terminal == StreamEvents.ANSWER_END
            or terminal == StreamEvents.APPROVAL_REQUIRED
        ):
            return ConversationStatus.COMPLETED.value
        return ConversationStatus.FAILED.value

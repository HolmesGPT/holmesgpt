import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING, Union

from starlette.requests import Request

from holmes.common.env_vars import (
    CONVERSATION_WORKER_EVENT_BATCH_INTERVAL_SECONDS,
    CONVERSATION_WORKER_MAX_CONCURRENT,
    CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITH_REALTIME,
    CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITHOUT_REALTIME,
    CONVERSATION_WORKER_REALTIME_ENABLED,
    CONVERSATION_WORKER_SLOT_STUCK_WARN_SECONDS,
    CONVERSATION_WORKER_REALTIME_VERIFY_INITIAL_BACKOFF_SECONDS,
    CONVERSATION_WORKER_REALTIME_VERIFY_MAX_BACKOFF_SECONDS,
)
from holmes.core.conversations import build_chat_messages
from holmes.core.conversations_worker.event_publisher import (
    ConversationEventPublisher,
)
from holmes.core.conversations_worker.models import (
    EVENT_USER_MESSAGE,
    ConversationReassignedError,
    ConversationStatus,
    ConversationTask,
)
from holmes.core.conversations_worker.realtime_manager import RealtimeWorker
from holmes.core.conversations_worker.tool_call_worker import ToolCallWorker
from holmes.core.models import ChatRequest
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
from holmes.utils.stream import StreamEvents

if TYPE_CHECKING:
    from fastapi.responses import StreamingResponse
    from holmes.config import Config
    from holmes.core.models import ChatResponse
    from holmes.core.supabase_dal import SupabaseDal

ChatFunction = Callable[
    [ChatRequest, Request], Union["ChatResponse", "StreamingResponse"]
]

# Saturation logging (ROB-759): the claim loop previously skipped claiming
# with zero output when all executor slots were occupied, which looked
# identical to a dead loop. Logging is transition-based, not periodic, so a
# healthy busy worker stays quiet:
#  * Saturation must persist CONTINUOUSLY for this long before the single
#    INFO line is emitted. A worker churning through a backlog frees a slot
#    on every completion (which wakes the claim loop synchronously), so its
#    saturation clock keeps resetting and it never logs — no enter/exit
#    flicker. Only a worker where nothing completes accumulates the full
#    window.
_SATURATION_LOG_AFTER_SECONDS = 60.0
#  * The stuck-slot WARNING (in-flight age above
#    CONVERSATION_WORKER_SLOT_STUCK_WARN_SECONDS while claiming is blocked)
#    repeats at most this often.
_STUCK_WARN_RATE_LIMIT_SECONDS = 300.0

# Shutdown handling. When the pod is asked to stop (SIGTERM from a rollout,
# node drain, scale-down), whatever conversations we are mid-turn on are never
# going to finish: the executor is not drained, the threads are daemons, and
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


class _ActiveTask:
    """An in-flight conversation: the task itself plus when it took its slot.

    ``started`` is ``time.monotonic()`` and feeds the saturation/stuck-slot
    logging; ``task`` is kept so the shutdown path can address the row (it
    needs conversation_id + request_sequence, which the dict key alone no
    longer suffices for once we have to write to the DB).
    """

    __slots__ = ("task", "started")

    def __init__(self, task: "ConversationTask", started: float):
        self.task = task
        self.started = started


class ConversationWorker:
    """
    Conversation Worker.

    Active participant that picks up pending Conversation rows from Supabase,
    runs them through the existing /api/chat pipeline (via chat_function),
    and writes results back as ConversationEvents in real-time.

    Lifecycle: pending → running (claimed + processing) → completed/failed.
    The claim RPC lands a row directly in 'running' ('queued' is deprecated), so
    a conversation waiting for capacity stays 'pending'.
    """

    def __init__(
        self,
        dal: "SupabaseDal",
        config: "Config",
        chat_function: ChatFunction,
    ):
        self.dal = dal
        self.config = config
        self.chat_function = chat_function
        # Globally-unique process id (presence key + assignee). hostname alone
        # isn't unique across pod restarts/replicas, so add pid + short uuid4.
        hostname = os.environ.get("HOSTNAME") or "local"
        self.holmes_id = f"{hostname}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        self._running = False
        self._claim_thread: Optional[threading.Thread] = None
        self._notify_event = threading.Event()
        self._executor: Optional[ThreadPoolExecutor] = None

        # In-flight (running) tasks, keyed by (conversation_id, request_sequence)
        # — see ConversationTask.active_key — so overlapping turns of one
        # conversation are counted separately for capacity. The value is the
        # monotonic start time, so the claim loop can report how long each
        # in-flight task has been holding a slot (ROB-759).
        self._active_conversation_ids: Dict[Any, _ActiveTask] = {}
        self._active_lock = threading.Lock()

        # Saturation-transition logging state (ROB-759). _saturated_since is
        # the start of the current CONTINUOUS zero-free-slots stretch (None
        # when a claim attempt found free capacity); _saturation_logged marks
        # that the one INFO line for this stretch was emitted (its matching
        # exit line logs the total duration); _last_stuck_warn rate-limits
        # the stuck-slot WARNING. None means "never warned" — do NOT use 0.0
        # as the sentinel: time.monotonic() is seconds since boot on Linux,
        # so on a freshly booted host `now - 0.0` can be below the rate-limit
        # window and the FIRST warning would be silently suppressed.
        self._saturated_since: Optional[float] = None
        self._saturation_logged: bool = False
        self._last_stuck_warn: Optional[float] = None

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
            logging.info(
                "ConversationWorker not started - Supabase DAL not enabled"
            )
            return
        if self._running:
            logging.warning("ConversationWorker is already running")
            return

        # We mark the worker as running so stop() / status checks see a
        # consistent state, but defer spinning up the executor, claim loop,
        # and Realtime subscription until the verifier confirms Supabase
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
        Spin up the components that actually consume conversations — the
        executor, the claim loop, and (optionally) the Realtime manager.

        Called by the verifier once Supabase confirms Realtime is enabled.
        Idempotent: if already started (re-entrant call), returns early.
        """
        if self._executor is not None or self._claim_thread is not None:
            return

        self._executor = ThreadPoolExecutor(
            max_workers=CONVERSATION_WORKER_MAX_CONCURRENT,
            thread_name_prefix="conversation-worker",
        )

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

        self._claim_thread = threading.Thread(
            target=self._claim_loop,
            daemon=True,
            name="conversation-claim-loop",
        )
        self._claim_thread.start()

        try:
            self._tool_call_worker.start(
                realtime_connected_fn=self._realtime_connected
            )
        except Exception:
            logging.exception("Failed to start ToolCallWorker", exc_info=True)

        logging.info(
            "ConversationWorker active (holmes_id=%s, account=%s, cluster=%s, realtime=%s)",
            self.holmes_id,
            self.dal.account_id,
            self.dal.cluster,
            self._realtime_manager is not None,
        )

    def stop(self) -> None:
        logging.info("Stopping ConversationWorker...")
        self._running = False
        self._notify_event.set()
        self._realtime_verify_stop.set()
        # Retire whatever we're mid-turn on before tearing the pool down. Must
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
        # Let any in-flight _dispatch finish before shutting the executor down.
        with self._dispatch_lock:
            if self._executor:
                # shutdown(wait=False): prevent new tasks from being accepted,
                # but don't block on in-flight conversations.
                self._executor.shutdown(wait=False)
                self._executor = None
        if self._claim_thread:
            # Bounded join: the claim loop wakes up once per notify or poll
            # interval and checks ``self._running``, so 5 seconds is plenty
            # for the common case. If it's somehow stuck we still return
            # promptly rather than hang the shutdown path.
            self._claim_thread.join(timeout=5)
            self._claim_thread = None
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
                        "Failed to update HolmesStatus after realtime "
                        "verification",
                        exc_info=True,
                    )
                # Spin up the executor, claim loop, and (if enabled)
                # Realtime subscription now that we know they'll do useful
                # work. If stop() raced us, _running is already False —
                # don't bring up workers that will immediately need to be
                # torn down.
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

    # ---- claim loop ----

    def _claim_loop(self) -> None:
        # When Realtime is enabled, the SUBSCRIBED callback fires
        # on_new_pending() which wakes this loop for the first claim —
        # guaranteeing the subscription is established before we try to
        # claim.  On reconnects the same callback fires again, ensuring
        # we re-claim any conversations missed during disconnection.
        # When Realtime is disabled, claim immediately on startup.
        if self._realtime_manager is None:
            self._try_claim_and_dispatch()

        while self._running:
            if self._realtime_connected():
                timeout = CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITH_REALTIME
            else:
                timeout = CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITHOUT_REALTIME

            triggered = self._notify_event.wait(timeout=timeout)
            if not self._running:
                break
            self._notify_event.clear()
            # Per-tick trace (ROB-759): proves the loop is alive and shows
            # whether a quiet worker is idle or out of capacity. Guarded so
            # the lock acquisition and realtime check run only when DEBUG
            # logging is actually enabled — this fires every poll tick.
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                with self._active_lock:
                    active = len(self._active_conversation_ids)
                logging.debug(
                    "Claim loop tick (triggered=%s, realtime=%s, active=%d/%d)",
                    triggered,
                    self._realtime_connected(),
                    active,
                    CONVERSATION_WORKER_MAX_CONCURRENT,
                )
            try:
                self._try_claim_and_dispatch()
            except Exception:
                logging.exception(
                    "Error in ConversationWorker claim loop (triggered=%s)",
                    triggered,
                    exc_info=True,
                )

    def claim_pending_conversations(self) -> None:
        """Routing target for RealtimeWorker on 'pending_conversations'
        broadcasts. Non-blocking: wakes the claim loop."""
        self._notify_event.set()

    def _realtime_connected(self) -> bool:
        if self._realtime_manager is None:
            return False
        try:
            return bool(self._realtime_manager.is_connected())
        except Exception:
            return False

    def _free_claim_slots(self) -> int:
        """Pool slots free right now: MAX_CONCURRENT minus in-flight tasks.

        Surplus stays 'pending' for the next poll or another instance to claim.
        """
        with self._active_lock:
            active = len(self._active_conversation_ids)
        return CONVERSATION_WORKER_MAX_CONCURRENT - active

    def _note_saturation(self) -> None:
        """Transition-based logging for a claim attempt that found 0 free slots.

        Deliberately NOT edge-triggered: under a backlog every completed
        conversation wakes the claim loop, which briefly sees a free slot and
        immediately refills it — an enter/exit pair per completion would be
        pure flicker. Instead the saturation clock must run CONTINUOUSLY for
        _SATURATION_LOG_AFTER_SECONDS before the single INFO line is emitted;
        any claim attempt that finds capacity resets it (see
        _note_capacity_available). Full capacity under load is a normal state,
        hence INFO; the WARNING is reserved for slots held longer than
        CONVERSATION_WORKER_SLOT_STUCK_WARN_SECONDS — an actual anomaly.
        """
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
                    for key, entry in self._active_conversation_ids.items()
                )
            logging.info(
                "Conversation claim capacity saturated for %.0fs: all %d slots "
                "in use; pending conversations will not be claimed until one "
                "finishes. In-flight (age_seconds, (conversation_id, "
                "request_sequence)): %s",
                now - self._saturated_since,
                CONVERSATION_WORKER_MAX_CONCURRENT,
                ages,
            )
        if (
            self._last_stuck_warn is None
            or now - self._last_stuck_warn >= _STUCK_WARN_RATE_LIMIT_SECONDS
        ):
            with self._active_lock:
                stuck = sorted(
                    (round(now - entry.started, 1), key)
                    for key, entry in self._active_conversation_ids.items()
                    if now - entry.started
                    >= CONVERSATION_WORKER_SLOT_STUCK_WARN_SECONDS
                )
            if stuck:
                self._last_stuck_warn = now
                logging.warning(
                    "Conversation slot(s) stuck: %d in-flight conversation(s) "
                    "running longer than %.0fs while claiming is blocked at "
                    "full capacity. Stuck (age_seconds, (conversation_id, "
                    "request_sequence)): %s",
                    len(stuck),
                    CONVERSATION_WORKER_SLOT_STUCK_WARN_SECONDS,
                    stuck,
                )

    def _note_capacity_available(self, free: int) -> None:
        """Reset the saturation clock; log the exit line if the enter line fired."""
        if self._saturation_logged:
            duration = time.monotonic() - (self._saturated_since or 0.0)
            logging.info(
                "Conversation claim capacity available again (free=%d) after "
                "%.0fs saturated",
                free,
                duration,
            )
        self._saturated_since = None
        self._saturation_logged = False

    def _try_claim_and_dispatch(self) -> None:
        # Claim only as many pending rows as we have free slots and submit each
        # straight to the executor (the claim already set them 'running'). The
        # surplus stays 'pending' for another instance. _process_conversation_safe
        # wakes this loop to re-claim as slots free.
        free = self._free_claim_slots()
        if free <= 0:
            # All slots occupied: pending rows stay unclaimed until a slot
            # frees, and previously this returned with zero log output —
            # indistinguishable from a dead claim loop (ROB-759).
            self._note_saturation()
            return
        self._note_capacity_available(free)
        claimed = self.dal.claim_n_pending_conversations(self.holmes_id, free)
        if claimed:
            logging.info(
                "Claimed %d conversation(s) (free slots=%d)", len(claimed), free
            )
        for conv in claimed:
            task = self._build_task_from_conversation_row(conv)
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
            self._dispatch(task)

    def _dispatch(self, task: ConversationTask) -> None:
        """Submit a claimed (already 'running') conversation to the executor.

        No DB write here — the claim set 'running'. A request_sequence bumped
        after the claim (stop/retry) is caught later as ConversationReassignedError.
        """
        with self._dispatch_lock:
            if not self._running or self._executor is None:
                return
            with self._active_lock:
                self._active_conversation_ids[task.active_key] = _ActiveTask(
                    task, time.monotonic()
                )
            try:
                self._executor.submit(self._process_conversation_safe, task)
            except RuntimeError:
                # Pool shut down (stop() raced); row stays 'running' and is
                # recovered by the stale-conversation timeout sweep.
                with self._active_lock:
                    self._active_conversation_ids.pop(task.active_key, None)
                logging.warning(
                    "Executor shut down; dropping claimed conversation %s",
                    task.conversation_id,
                )

    def _build_task_from_conversation_row(
        self, conv: Dict[str, Any]
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
                # Conversations.user_id (set by the FE when it created the row)
                # — surfaced on the task so per-turn ChatRequest construction
                # can use it as a fallback when the user_message event's data
                # doesn't carry user_id explicitly.
                user_id=conv.get("user_id"),
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
        with self._active_lock:
            entries = list(self._active_conversation_ids.values())
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
        """Transition one conversation to 'timeout', falling back to 'failed'.

        'timeout' as a *target* status needs robusta-storage migration
        20260817121606. Cloud databases are migrated by hand with no pipeline,
        so a Holmes release can legitimately reach an environment whose
        update_conversation_status still rejects it ("Invalid status: timeout").
        In that case we fall back to 'failed', which every version accepts —
        the conversation is still terminal, still carries the "Holmes Restarted"
        error event, and stays followup-able. Once the migration is applied the
        first call succeeds and this branch goes cold.
        """
        try:
            if self.dal.update_conversation_status(
                conversation_id=task.conversation_id,
                request_sequence=task.request_sequence,
                assignee=self.holmes_id,
                status=ConversationStatus.TIMEOUT.value,
            ):
                return
        except ConversationReassignedError:
            # The turn finished (or was stopped/retried) while we were shutting
            # down — whoever owns the row now has already set its status.
            return

        # The DAL swallows non-mismatch RPC errors and returns False, so this
        # covers both "Invalid status: timeout" from an un-migrated database
        # and a transient Supabase failure. Either way, try the status every
        # version accepts rather than leave the row 'running'.
        logging.info(
            "Could not set conversation %s to 'timeout'; falling back to "
            "'failed'",
            task.conversation_id,
        )
        try:
            self.dal.update_conversation_status(
                conversation_id=task.conversation_id,
                request_sequence=task.request_sequence,
                assignee=self.holmes_id,
                status=ConversationStatus.FAILED.value,
            )
        except ConversationReassignedError:
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
            with self._active_lock:
                self._active_conversation_ids.pop(task.active_key, None)
            # A slot freed up — wake the claim loop to re-claim pending rows.
            self._notify_event.set()

    def _process_conversation(self, task: ConversationTask) -> None:
        events = self.dal.get_conversation_events(task.conversation_id)
        self._hydrate_task_from_events(task, events)

        data = task.user_message_data
        ask = data.get("ask")

        # A follow-up may carry only tool_decisions / frontend_tool_results
        # (no new user question). Holmes resumes the prior assistant turn.
        resume_only = bool(
            not ask and (data.get("tool_decisions") or data.get("frontend_tool_results"))
        )
        if resume_only:
            ask = self._extract_last_user_ask(task.conversation_history) or "Continue"

        if not ask:
            logging.warning(
                "Conversation %s has no user question, marking as failed",
                task.conversation_id,
            )
            self._fail_conversation(task, "No user question found in conversation events")
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

        # AI usage tracking (HolmesUsageEvents) — resolve user_id and
        # request_source with row-level fallbacks. The FE writes both onto
        # the Conversations row when it creates the chat (user_id as a
        # column, request_source under metadata) but doesn't necessarily
        # repeat them in every user_message event's data. Without this
        # fallback, follow-up turns produce HolmesUsageEvents rows with
        # NULL user_id / request_source even though the values are known.
        # Per-event data still wins so the FE can override per-turn (e.g.
        # an alert-investigation chat that pivots to a freeform question).
        resolved_user_id = data.get("user_id") or task.user_id
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
        # Per-event presence wins, not truthiness — so an explicit empty
        # value from the FE (e.g. "" to deliberately clear a field) keeps
        # priority over the row-level metadata fallback and we don't
        # reintroduce stale Conversation-row values. Only fall back to
        # task.metadata when the per-turn event omits the key entirely.
        resolved_user_email = (
            data["user_email"]
            if "user_email" in data
            else (task.metadata.get("user_email") if task.metadata else None)
        )
        resolved_request_source = (
            data["request_source"]
            if "request_source" in data
            else (task.metadata.get("request_source") if task.metadata else None)
        )
        # source_ref is conversation-level for alert investigations (the
        # whole chat is about one alert id), so the FE puts it on the
        # Conversations row's metadata, not in each per-turn event.
        resolved_source_ref = (
            data["source_ref"]
            if "source_ref" in data
            else (task.metadata.get("source_ref") if task.metadata else None)
        )
        # request_type may also live under Conversations.metadata when the
        # FE classifies a whole chat once at creation time. Same key-presence
        # semantics — leaving the resolved value None when neither source
        # supplies it preserves build_chat_recorder_state's auto-detection
        # (Slack-prefix → 'slack_chat', fallback → 'user_chat').
        resolved_request_type = (
            data["request_type"]
            if "request_type" in data
            else (task.metadata.get("request_type") if task.metadata else None)
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
                for ev in events[current_user_idx + 1:]
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

        skills = self.config.get_skill_catalog()

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
            is_robusta_model = bool(getattr(ai.llm, "is_robusta_model", False))

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
                }
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

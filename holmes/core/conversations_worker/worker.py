"""The conversation worker's process lifecycle.

``ConversationRuntime`` owns no conversation and no pool. It verifies that
Supabase Realtime is enabled, then starts the pieces that do the work and
stops them in the right order on shutdown:

* ``ExecutorRegistry`` (registry.py): which executor pools exist and how big;
  the discovery loop that creates and wakes them.
* ``ConversationExecutor`` (executors.py): one pool — its claim loop,
  dispatch and in-flight set.
* ``ConversationProcessor`` (processor.py): one claimed row → one
  conversation turn, and the outcome writes.
* ``RealtimeWorker`` / ``ToolCallWorker``: the broadcast subscription and the
  remote tool-call pool, unchanged.
"""

import logging
import os
import threading
import time
import uuid
from typing import List, Optional, TYPE_CHECKING

from postgrest.exceptions import APIError as PGAPIError

from holmes.common.env_vars import (
    CONVERSATION_WORKER_REALTIME_ENABLED,
    CONVERSATION_WORKER_REALTIME_VERIFY_INITIAL_BACKOFF_SECONDS,
    CONVERSATION_WORKER_REALTIME_VERIFY_MAX_BACKOFF_SECONDS,
)
from holmes.core.conversations_worker.models import ConversationTask
from holmes.core.conversations_worker.processor import ConversationProcessor
from holmes.core.conversations_worker.realtime_manager import RealtimeWorker
from holmes.core.conversations_worker.registry import (
    MAX_EXECUTORS,
    ExecutorRegistry,
)
from holmes.core.conversations_worker.sizing import ExecutorSizing
from holmes.core.conversations_worker.tool_call_worker import ToolCallWorker
from holmes.core.supabase_dal import SupabaseDnsException
from holmes.utils.holmes_status import update_holmes_status_in_db

if TYPE_CHECKING:
    from holmes.config import Config
    from holmes.core.supabase_dal import SupabaseDal

__all__ = ["ConversationRuntime", "SHUTDOWN_RETIRE_BUDGET_SECONDS"]

# Wall-clock budget for the shutdown retirement sweep. It is sequential and
# each row costs up to two DAL calls, each retrying 3 times with backoff, so a
# slow or unreachable Supabase could otherwise eat the container's termination
# grace period and earn us a SIGKILL — leaving the remaining rows 'running',
# the very thing the sweep is here to prevent. Rows we don't reach fall back
# to the pg_cron stale sweep, exactly as they did before.
SHUTDOWN_RETIRE_BUDGET_SECONDS = 10.0


class ConversationRuntime:
    """Starts and stops the conversation worker inside the Holmes server.

    Lifecycle of a row: pending → running (claimed + processing) →
    completed/failed. The claim RPC lands a row directly in 'running', so a
    conversation waiting for capacity stays 'pending'.

    Nothing consumes conversations until ``is_realtime_enabled()`` returns a
    definitive True; see ``_realtime_verify_loop``.
    """

    def __init__(
        self,
        dal: "SupabaseDal",
        config: "Config",
        sizing: Optional[ExecutorSizing] = None,
        max_executors: int = MAX_EXECUTORS,
    ):
        self.dal = dal
        self.config = config
        # Globally-unique process id (presence key + assignee). hostname alone
        # isn't unique across pod restarts/replicas, so add pid + short uuid4.
        hostname = os.environ.get("HOSTNAME") or "local"
        self.holmes_id = f"{hostname}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        self.processor = ConversationProcessor(
            dal=self.dal, config=self.config, holmes_id=self.holmes_id
        )
        self.executors = ExecutorRegistry(
            dal=self.dal,
            holmes_id=self.holmes_id,
            processor=self.processor,
            sizing=sizing,
            max_executors=max_executors,
        )
        # Executes cross-cluster remote tool calls (RemoteToolCalls rows) in
        # its own pool; RealtimeWorker routes 'pending_tool_calls' broadcasts
        # to it (same holmes:submit channel the conversation worker uses).
        self._tool_call_worker = ToolCallWorker(
            dal=self.dal, config=self.config, holmes_id=self.holmes_id
        )
        self._realtime_manager: Optional[RealtimeWorker] = None

        self._running = False
        self._active_started = False

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

    # ---- lifecycle ----

    def start(self) -> None:
        if not self.dal.enabled:
            logging.info("ConversationRuntime not started - Supabase DAL not enabled")
            return
        if self._running:
            logging.warning("ConversationRuntime is already running")
            return

        # Mark running so stop() / status checks see a consistent state, but
        # defer the discovery loop and Realtime subscription until the
        # verifier confirms Supabase Realtime is enabled — polling a project
        # that doesn't support our use case is wasted load.
        self._running = True

        self._realtime_verify_stop.clear()
        self._realtime_verify_thread = threading.Thread(
            target=self._realtime_verify_loop,
            daemon=True,
            name="conversation-realtime-verify",
        )
        self._realtime_verify_thread.start()

        logging.info(
            "ConversationRuntime waiting for Supabase Realtime verification "
            "(holmes_id=%s, account=%s, cluster=%s)",
            self.holmes_id,
            self.dal.account_id,
            self.dal.cluster,
        )

    def _start_active_workers(self) -> None:
        """Start the components that consume conversations — the Realtime
        manager (optional), executor discovery and the tool-call worker.
        Executor pools are created on demand by the registry, not here.

        Called by the verifier once Supabase confirms Realtime is enabled.
        Idempotent.
        """
        if self._active_started:
            return
        self._active_started = True

        if CONVERSATION_WORKER_REALTIME_ENABLED:
            try:
                self._realtime_manager = RealtimeWorker(
                    dal=self.dal,
                    holmes_id=self.holmes_id,
                    on_new_pending=self.executors.on_pending,
                    tool_call_worker=self._tool_call_worker,
                )
                self._realtime_manager.start()
            except Exception:
                logging.warning(
                    "Failed to start Realtime manager; continuing with polling only",
                    exc_info=True,
                )
                self._realtime_manager = None

        # With Realtime, the SUBSCRIBED drain triggers the first discovery so
        # the subscription exists before the first claim; without it, discover
        # right away.
        self.executors.start(
            realtime_connected=self._realtime_connected,
            discover_immediately=self._realtime_manager is None,
        )

        try:
            self._tool_call_worker.start(realtime_connected_fn=self._realtime_connected)
        except Exception:
            logging.exception("Failed to start ToolCallWorker", exc_info=True)

        logging.info(
            "ConversationRuntime active (holmes_id=%s, account=%s, cluster=%s, "
            "realtime=%s, builtin_executor_sizes=%s, base_size=%d, "
            "account_executor_sizes=%s)",
            self.holmes_id,
            self.dal.account_id,
            self.dal.cluster,
            self._realtime_manager is not None,
            self.executors.sizing.builtin_sizes,
            self.executors.sizing.base_size,
            self.executors.account_sizes(),
        )

    def stop(self) -> None:
        logging.info("Stopping ConversationRuntime...")
        self._running = False
        self._realtime_verify_stop.set()
        # Retire whatever we're mid-turn on before tearing the pools down. Must
        # happen while the rows still carry our assignee and 'running' status —
        # both RPCs guard on that. Flipping the status also makes any straggler
        # write from the in-flight thread fail with MISMATCH, which the
        # publisher already handles as ConversationReassignedError, so the
        # abandoned turn unwinds quietly instead of racing us.
        try:
            self._retire_in_flight()
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
        self.executors.stop()
        self._active_started = False
        # Drop the realtime manager handle so a subsequent start() can bring
        # up a fresh one.
        self._realtime_manager = None
        # Don't join the verify thread from inside itself — when the verifier
        # triggers stop() on a definitive False, it's running on this very
        # thread. The daemon flag guarantees it won't outlive the process.
        if (
            self._realtime_verify_thread
            and self._realtime_verify_thread is not threading.current_thread()
        ):
            self._realtime_verify_thread.join(timeout=5)
            self._realtime_verify_thread = None
        logging.info("ConversationRuntime stopped")

    def _retire_in_flight(self) -> None:
        """Mark every conversation this process is still processing as timed out.

        Runs on a graceful shutdown (SIGTERM from a rollout / drain), not on
        SIGKILL or an OOM kill, where nothing of ours runs and the pg_cron
        stale sweep remains the backstop. Bounded by
        ``SHUTDOWN_RETIRE_BUDGET_SECONDS``.
        """
        tasks: List[ConversationTask] = self.executors.active_tasks()
        if not tasks:
            return
        logging.info(
            "Shutdown: marking %d in-flight conversation(s) as timed out", len(tasks)
        )
        deadline = time.monotonic() + SHUTDOWN_RETIRE_BUDGET_SECONDS
        for index, task in enumerate(tasks):
            if time.monotonic() >= deadline:
                logging.warning(
                    "Shutdown retirement budget (%.0fs) exhausted; leaving %d "
                    "conversation(s) to the stale sweep",
                    SHUTDOWN_RETIRE_BUDGET_SECONDS,
                    len(tasks) - index,
                )
                break
            self.processor.retire(task)

    def _realtime_connected(self) -> bool:
        if self._realtime_manager is None:
            return False
        try:
            return bool(self._realtime_manager.is_connected())
        except Exception:
            return False

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
                        "Failed to update HolmesStatus after realtime verification",
                        exc_info=True,
                    )
                # If stop() raced us, _running is already False — don't bring
                # up workers that will immediately need to be torn down.
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
                    "shutting down ConversationRuntime"
                )
                # HolmesStatus already advertises false by default, so no
                # further write is needed. stop() detects we're calling from
                # the verify thread and skips the self-join.
                try:
                    self.stop()
                except Exception:
                    logging.exception(
                        "Error during ConversationRuntime shutdown after "
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

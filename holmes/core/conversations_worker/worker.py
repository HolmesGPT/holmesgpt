import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING, Union

from starlette.requests import Request

from holmes.common.env_vars import (
    CONVERSATION_WORKER_EVENT_BATCH_INTERVAL_SECONDS,
    CONVERSATION_WORKER_MAX_CONCURRENT,
    CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITH_REALTIME,
    CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITHOUT_REALTIME,
    CONVERSATION_WORKER_REALTIME_ENABLED,
)
from holmes.core.conversations import build_chat_messages
from holmes.core.conversations_worker.event_publisher import (
    ConversationEventPublisher,
)
from holmes.core.conversations_worker.models import (
    EVENT_USER_MESSAGE,
    ConversationReassignedError,
    ConversationTask,
)
from holmes.core.conversations_worker.realtime_manager import RealtimeManager
from holmes.core.models import ChatRequest
from holmes.core.prompt import PromptComponent
from holmes.core.tools import PrerequisiteCacheMode, ToolsetTag
from holmes.core.tools_utils.filesystem_result_storage import (
    tool_result_storage,
)
from holmes.core.tools_utils.frontend_tools import (
    FrontendToolCollisionError,
    inject_frontend_tools,
)
from holmes.core.tracing import TracingFactory
from holmes.utils.stream import StreamEvents

if TYPE_CHECKING:
    from fastapi.responses import StreamingResponse
    from holmes.config import Config
    from holmes.core.models import ChatResponse
    from holmes.core.supabase_dal import SupabaseDal

ChatFunction = Callable[
    [ChatRequest, Request], Union["ChatResponse", "StreamingResponse"]
]



class ConversationWorker:
    """
    Conversation Worker.

    Active participant that picks up pending Conversation rows from Supabase,
    runs them through the existing /api/chat pipeline (via chat_function),
    and writes results back as ConversationEvents in real-time.

    Lifecycle: pending → queued (claimed) → running (processing) → completed/failed.
    Presence is advertised for both queued and running conversations.
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
        # Uniquely identify this Holmes process (presence key, assignee value
        # in Conversations). HOSTNAME alone is not unique because a pod can
        # restart and re-use the same name, and two replicas in different pods
        # can have the same env var in tests. Combining hostname + pid +
        # short uuid4 makes it globally unique across process lifetimes.
        hostname = os.environ.get("HOSTNAME") or "local"
        self.holmes_id = f"{hostname}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        self._running = False
        self._claim_thread: Optional[threading.Thread] = None
        self._notify_event = threading.Event()
        self._executor: Optional[ThreadPoolExecutor] = None

        # Tracks conversations currently being processed (running state).
        self._active_conversation_ids: set = set()
        self._active_lock = threading.Lock()

        # Conversations that have been claimed (queued) but not yet submitted
        # to the executor because we're at capacity.
        self._queued_tasks: deque = deque()
        self._queued_lock = threading.Lock()

        # Serializes _dispatch_queued with stop() so that the capacity check,
        # DB transition, active-set update, and executor.submit are atomic —
        # prevents submitting to a shut-down executor or exceeding
        # MAX_CONCURRENT when _dispatch_queued runs from multiple threads
        # (claim loop + _process_conversation_safe finally block).
        self._dispatch_lock = threading.Lock()

        self._realtime_manager: Optional[RealtimeManager] = None

    def start(self) -> None:
        if not self.dal.enabled:
            logging.info(
                "ConversationWorker not started - Supabase DAL not enabled"
            )
            return
        if self._running:
            logging.warning("ConversationWorker is already running")
            return

        self._running = True
        self._executor = ThreadPoolExecutor(
            max_workers=CONVERSATION_WORKER_MAX_CONCURRENT,
            thread_name_prefix="conversation-worker",
        )

        if CONVERSATION_WORKER_REALTIME_ENABLED:
            try:
                self._realtime_manager = RealtimeManager(
                    dal=self.dal,
                    holmes_id=self.holmes_id,
                    on_new_pending=self._notify_event.set,
                )
                self._realtime_manager.start()
            except Exception:
                logging.exception(
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
        logging.info(
            "ConversationWorker started (holmes_id=%s, account=%s, cluster=%s, realtime=%s)",
            self.holmes_id,
            self.dal.account_id,
            self.dal.cluster,
            self._realtime_manager is not None,
        )

    def stop(self) -> None:
        logging.info("Stopping ConversationWorker...")
        self._running = False
        self._notify_event.set()
        if self._realtime_manager:
            try:
                self._realtime_manager.stop()
            except Exception:
                logging.exception("Error stopping realtime manager", exc_info=True)
        # Acquire _dispatch_lock so any in-flight _dispatch_queued call
        # finishes before we shut down the executor — prevents RuntimeError
        # from submit() on a shut-down pool.
        with self._dispatch_lock:
            if self._executor:
                # shutdown(wait=False): prevent new tasks from being accepted,
                # but don't block on in-flight conversations. 
                self._executor.shutdown(wait=False)
        if self._claim_thread:
            # Bounded join: the claim loop wakes up once per notify or poll
            # interval and checks ``self._running``, so 5 seconds is plenty
            # for the common case. If it's somehow stuck we still return
            # promptly rather than hang the shutdown path.
            self._claim_thread.join(timeout=5)
        logging.info("ConversationWorker stopped")

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
            try:
                self._try_claim_and_dispatch()
            except Exception:
                logging.exception(
                    "Error in ConversationWorker claim loop (triggered=%s)",
                    triggered,
                    exc_info=True,
                )

    def _realtime_connected(self) -> bool:
        if self._realtime_manager is None:
            return False
        try:
            return bool(self._realtime_manager.is_connected())
        except Exception:
            return False

    def _try_claim_and_dispatch(self) -> None:
        # Claim ALL pending conversations — they transition to queued state.
        # There is no capacity check here: we claim eagerly so that no other
        # Holmes instance can grab them, and queue them locally until executor
        # slots open up.
        claimed = self.dal.claim_conversations(self.holmes_id)
        if claimed:
            logging.info("Claimed %d conversation(s)", len(claimed))
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
            with self._queued_lock:
                self._queued_tasks.append(task)

        # Dispatch as many queued tasks as executor capacity allows.
        self._dispatch_queued()

    def _dispatch_queued(self) -> None:
        """Move tasks from the queued pool to the executor, up to capacity.

        Holds ``_dispatch_lock`` for the entire sequence so the capacity check,
        DB transition, active-set update, and executor submit are atomic with
        respect to ``stop()`` and concurrent calls from other threads.
        """
        with self._dispatch_lock:
            while self._running:
                with self._active_lock:
                    active = len(self._active_conversation_ids)
                if active >= CONVERSATION_WORKER_MAX_CONCURRENT:
                    break

                with self._queued_lock:
                    if not self._queued_tasks:
                        break
                    task = self._queued_tasks.popleft()

                # Transition from queued → running in the DB. The RPC validates
                # that the assignee and request_sequence still match — if
                # stop_conversation or retry_conversation bumped the sequence
                # while the task was queued, this raises ConversationReassignedError.
                try:
                    ok = self.dal.update_conversation_status(
                        conversation_id=task.conversation_id,
                        request_sequence=task.request_sequence,
                        assignee=self.holmes_id,
                        status="running",
                    )
                    if not ok:
                        logging.warning(
                            "Failed to transition conversation %s to running — skipping",
                            task.conversation_id,
                        )
                        continue
                except ConversationReassignedError:
                    logging.warning(
                        "Conversation %s was reassigned while queued — skipping",
                        task.conversation_id,
                    )
                    continue
                except Exception:
                    logging.exception(
                        "Error transitioning conversation %s to running — requeuing",
                        task.conversation_id,
                        exc_info=True,
                    )
                    with self._queued_lock:
                        self._queued_tasks.appendleft(task)
                    break

                with self._active_lock:
                    self._active_conversation_ids.add(task.conversation_id)
                self._executor.submit(self._process_conversation_safe, task)

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
        self, task: ConversationTask, description: str, error_code: int = 5000
    ) -> None:
        """Post an error event to ConversationEvents so subscribers can see the failure reason."""
        try:
            self.dal.post_conversation_events(
                conversation_id=task.conversation_id,
                assignee=self.holmes_id,
                request_sequence=task.request_sequence,
                events=[
                    {
                        "event": "error",
                        "data": {
                            "description": description,
                            "error_code": error_code,
                            "msg": description,
                            "success": False,
                        },
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
        self, task: ConversationTask, description: str, error_code: int = 5000
    ) -> None:
        """Post an error event and then mark the conversation as failed."""
        self._post_error_event(task, description, error_code)
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
                self._active_conversation_ids.discard(task.conversation_id)
            # A slot freed up — try to dispatch the next queued task.
            try:
                self._dispatch_queued()
            except Exception:
                logging.exception(
                    "Error dispatching queued tasks after conversation %s",
                    task.conversation_id,
                    exc_info=True,
                )

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
                metadata={
                    "holmesgpt.investigation.question": chat_request.ask[:1024],
                    "holmesgpt.investigation.stream": True,
                    "holmesgpt.conversation_id": task.conversation_id,
                }
            )

            try:
                stream = request_ai.call_stream(
                    msgs=messages,
                    enable_tool_approval=chat_request.enable_tool_approval or False,
                    tool_decisions=chat_request.tool_decisions,
                    frontend_tool_results=chat_request.frontend_tool_results,
                    response_format=chat_request.response_format,
                    trace_span=trace_span,
                )

                terminal = publisher.consume(stream)
                status = self._terminal_to_status(terminal)
                if status in ("completed", "failed"):
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
                else:
                    logging.warning(
                        "Conversation %s ended without a terminal event",
                        task.conversation_id,
                    )
                    self._fail_conversation(
                        task, "Conversation ended without a terminal event"
                    )
            finally:
                trace_span.end()
        except ConversationReassignedError as e:
            logging.warning(
                "Conversation %s was reassigned: %s", task.conversation_id, e
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
        string status we pass to ``update_conversation_status`` (or a sentinel
        for non-completion states)."""
        if terminal == StreamEvents.ANSWER_END:
            return "completed"
        if terminal == StreamEvents.ERROR:
            return "failed"
        if terminal == StreamEvents.APPROVAL_REQUIRED:
            # Approval pauses the LLM but the current request_sequence is
            # done — the follow-up (with tool_decisions) will re-pend it.
            return "completed"
        return "unknown"

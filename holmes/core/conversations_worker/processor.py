"""Turning one claimed Conversations row into one conversation turn.

The processor is the part of the conversation worker that does the work:
hydrate the task from its events, run Holmes on it, stream the events back,
and write the row's terminal status. It knows nothing about executors,
threads or claiming; an executor calls ``run(task)`` on one of its threads.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from holmes.common.env_vars import CONVERSATION_WORKER_EVENT_BATCH_INTERVAL_SECONDS
from holmes.core.conversation_links import resolve_conversation_link
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
from holmes.core.models import ChatRequest
from holmes.core.prompt import PromptComponent
from holmes.core.relay_refusal import RELAY_REFUSAL_ERROR_CODES, RelayRefusal
from holmes.core.tools import PrerequisiteCacheMode, ToolsetTag
from holmes.core.tools_utils.filesystem_result_storage import tool_result_storage
from holmes.core.tools_utils.frontend_tools import (
    FrontendToolCollisionError,
    inject_frontend_tools,
)
from holmes.core.tracing import TracingFactory, langfuse_trace_attributes
from holmes.core.usage_recorder import (
    build_chat_recorder_state,
    stream_with_usage_recording,
)
from holmes.utils.stream import StreamEvents

if TYPE_CHECKING:
    from holmes.config import Config
    from holmes.core.supabase_dal import SupabaseDal

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
# A row naming an executor this agent cannot run (pool cap reached).
EXECUTOR_UNAVAILABLE_ERROR_CODE = 5206


class ConversationProcessor:
    """Runs one conversation turn and writes its outcome.

    ``run`` is the happy path. The other public methods are the outcome
    writers an executor or the runtime needs for a row that cannot run:
    ``fail`` / ``fail_row`` (error event + 'failed'), ``retire`` (shutdown
    error event + 'timeout') and ``timeout``.
    """

    def __init__(self, dal: "SupabaseDal", config: "Config", holmes_id: str):
        self.dal = dal
        self.config = config
        self.holmes_id = holmes_id

    # ---- the turn ----

    def run(self, task: ConversationTask) -> None:
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
            self.fail(task, "An internal error occurred while processing your request")

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
            self.fail(task, "No user question found in conversation events")
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
            self.fail(
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
                    self.fail(task, "Conversation ended without a terminal event")
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
            self.fail(
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
            self.fail(
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
            self.fail(task, str(e), error_code=4000)
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

    # ---- outcome writers ----

    def post_error_event(
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

    def fail(
        self,
        task: ConversationTask,
        description: str,
        error_code: int = 5000,
        raw_error: Optional[str] = None,
    ) -> None:
        """Post an error event and then mark the conversation as failed."""
        self.post_error_event(task, description, error_code, raw_error=raw_error)
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

    def fail_row(self, conv: Dict[str, Any], description: str) -> None:
        """``fail`` for a claimed row that did not parse into a task: the claim
        set it 'running' with our assignee, so it must still be closed out."""
        cid = conv.get("conversation_id")
        seq = conv.get("request_sequence")
        if not cid or seq is None:
            return
        try:
            task = ConversationTask(
                conversation_id=cid,
                account_id=conv.get("account_id", ""),
                cluster_id=conv.get("cluster_id", ""),
                origin=conv.get("origin", ""),
                request_sequence=int(seq),
                executor=conv.get("executor")
                or ConversationTask.model_fields["executor"].default,
            )
        except Exception:
            logging.exception(
                "Failed to mark unparseable conversation %s as failed",
                cid,
                exc_info=True,
            )
            return
        self.fail(task, description)

    def timeout(self, task: ConversationTask) -> None:
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

    def retire(self, task: ConversationTask) -> None:
        """Close out a turn this process will never finish (shutdown, or a
        claimed row whose pool is gone): the restart error event first (the
        events RPC requires status 'running'), then 'timeout'. Never raises."""
        try:
            self.post_error_event(
                task,
                SHUTDOWN_ERROR_DESCRIPTION,
                error_code=SHUTDOWN_ERROR_CODE,
                reason=SHUTDOWN_REASON,
            )
            self.timeout(task)
        except Exception:
            logging.warning(
                "Failed to retire conversation %s",
                task.conversation_id,
                exc_info=True,
            )

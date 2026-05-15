"""Shared helper for recording AI usage events to HolmesUsageEvents.

Used by every LLM-consuming entry point (server.py /api/chat, the
ConversationWorker, scheduled prompts, the AG-UI server, and
holmes/checks/checks_api.py) so usage tracking is consistent and there's
exactly one place to update if the recording shape changes.

The recorder is fire-and-forget: each call spawns a daemon thread to do
the DB write. Telemetry must never block or break the response path.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Generator, Optional

from holmes.core.llm_usage import RequestStats
from holmes.utils.stream import StreamEvents, StreamMessage

if TYPE_CHECKING:
    from holmes.core.models import ChatRequest
    from holmes.core.supabase_dal import SupabaseDal
    from holmes.core.tool_calling_llm import LLMResult


# Bounded thread pool for recorder DB writes. Caps concurrent supabase-py
# connection acquisitions from telemetry so a slow Supabase / connection
# leak in the recorder can't starve foreground writes (HolmesStatus,
# ToolStatus). max_workers=4 is enough for Holmes' single-pod-per-customer
# load — one Supabase write is ~50–200ms, so 4 workers handle ~80 events/sec
# sustained, well above current request rate. Process-exit semantics: the
# stdlib's atexit handler drains live executors, which is *better* than the
# previous daemon-thread fire-and-forget — we lose fewer rows on graceful
# shutdown. Threads are spawned lazily on first submit; importing this
# module doesn't start any.
_RECORDER_EXECUTOR = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="usage-recorder",
)


# Slack auto-detection: the Robusta runner's Slack handler currently prepends
# a fixed prefix to the user's message before POSTing /api/chat. Example:
#   "**@user_U0AKMP2CZ97** • 2026-05-04T05:10:04Z\n\nhigh cpu in pod alert"
# Extracted into a shared regex so both the direct /api/chat path (server.py)
# and the worker path (conversations_worker/worker.py) can run the same
# detection. Heuristic — fragile if the runner format changes.
_SLACK_ASK_PREFIX_RE = re.compile(
    r"^\*\*@user_(?P<slack_user_id>U[A-Z0-9]+)\*\*\s*•\s*"
    r"(?P<slack_triggered_at>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)",
)


def detect_slack_origin(ask: Optional[str]) -> Optional[Dict[str, Any]]:
    """If `ask` matches the runner's Slack-prefix shape, return parsed
    metadata (slack_user_id + slack_triggered_at). Otherwise None.
    """
    if not ask:
        return None
    m = _SLACK_ASK_PREFIX_RE.match(ask)
    if not m:
        return None
    return {
        "slack_user_id": m.group("slack_user_id"),
        "slack_triggered_at": m.group("slack_triggered_at"),
    }


def resolve_provider(model: Optional[str]) -> str:
    """Best-effort: return the canonical litellm provider for `model`.

    Falls back to splitting on the litellm prefix (`openai/...`,
    `anthropic/...`) if the helper raises (e.g. for unrecognized models).
    Importing litellm is deferred so this module stays import-light for
    consumers that only want the dataclass.
    """
    if not model:
        return "unknown"
    try:
        import litellm  # local import — keep usage_recorder cheap to import
        return litellm.get_llm_provider(model)[1] or "unknown"
    except Exception:
        return model.split("/")[0] if "/" in model else "unknown"


def build_chat_recorder_state(
    chat_request: "ChatRequest",
    request_ai: Any,
    *,
    dal: Any,
    is_streaming: bool,
) -> "UsageRecorderState":
    """Construct a UsageRecorderState from a ChatRequest.

    Used by every code path that consumes a ChatRequest and wraps a stream:
    server.py:chat() (direct /api/chat) and ConversationWorker._run_chat_and_publish
    (worker path). Centralizes the request_type / request_source / is_internal
    / Slack auto-detection logic so all entry points get identical behavior.
    """
    # Default conversation_source to 'chat_history' when conversation_id is set
    # but the caller didn't override (i.e. direct /api/chat). The worker passes
    # 'conversations' explicitly.
    conversation_source = chat_request.conversation_source
    if conversation_source is None and chat_request.conversation_id:
        conversation_source = "chat_history"

    model_name = (
        getattr(request_ai.llm, "model", None)
        or chat_request.model
        or "unknown"
    )

    # Internal calls (title generation, classification, summarization, etc.)
    # get filtered out of user-facing dashboards. FE sets is_internal=True
    # explicitly for those. Backwards compat: if FE didn't set it, fall back
    # to detecting the legacy 'internal_' prefix on request_source.
    if chat_request.is_internal is None:
        is_internal = bool(
            chat_request.request_source
            and chat_request.request_source.startswith("internal_")
        )
    else:
        is_internal = bool(chat_request.is_internal)

    # Slack auto-detection: tag both request_type='slack_chat' and
    # request_source='slack' as defaults that the caller can still override.
    slack_info = detect_slack_origin(chat_request.ask)
    if chat_request.request_type:
        request_type = chat_request.request_type
    elif slack_info is not None:
        request_type = "slack_chat"
    else:
        request_type = "user_chat"

    request_source = chat_request.request_source
    if request_source is None and slack_info is not None:
        request_source = "slack"

    # Merge meta: FE-supplied keys, then backend-derived keys (backend wins
    # on collision). Slack info goes under a 'slack' sub-key so it doesn't
    # clutter the top level.
    merged_meta: Dict[str, Any] = dict(chat_request.meta or {})
    if slack_info is not None:
        merged_meta["slack"] = slack_info

    return UsageRecorderState(
        dal=dal,
        request_type=request_type,
        request_source=request_source,
        source_ref=chat_request.source_ref,
        conversation_id=chat_request.conversation_id,
        conversation_source=conversation_source,
        user_id=chat_request.user_id,
        is_streaming=is_streaming,
        is_internal=is_internal,
        model=model_name,
        provider=resolve_provider(model_name),
        is_robusta_model=getattr(request_ai.llm, "is_robusta_model", False),
        meta=merged_meta,
    )


class RequestStatus(str, Enum):
    """Final outcome of a request, written to HolmesUsageEvents.status.

    Subclassing both ``str`` and ``Enum`` keeps the values transparent to
    JSON serializers (supabase-py sends them as plain strings) and
    backwards-compatible with anything that compares against the old
    string literals. Add a new variant here whenever the recorder needs
    to surface a new outcome — the DB column is plain ``text`` so no
    migration is required to widen the set.
    """

    SUCCESS = "success"  # terminal ANSWER_END event seen
    APPROVAL_REQUIRED = "approval_required"  # terminal APPROVAL_REQUIRED seen
    ERROR = "error"  # terminal ERROR event or unhandled exception
    RATE_LIMITED = "rate_limited"  # provider rate-limit detected by record_error
    ABORTED = "aborted"  # stream ended without any terminal event


@dataclass
class UsageRecorderState:
    """All the data needed to write one HolmesUsageEvents row.

    Fields fall into three groups:

    1. **Required identity / classification** — set at construction time
       by the entry point (server.chat, the worker, AG-UI, scheduled
       prompts, health checks). The recorder cannot run without these.

    2. **Optional identity / classification** — also set at construction.
       NULL/default values are written through to the DB column as-is.

    3. **Mutable runtime fields** — left at their defaults at construction
       and filled in by ``stream_with_usage_recording`` (streaming path)
       or ``record_from_llm_result`` / ``record_error`` (non-streaming
       path) just before the row is fired. Don't set these on the entry
       point side; they get clobbered.

    For each field below: the comment lists where the value comes from,
    what it means, and what gets written if the field is left at its
    default.
    """

    # ── Group 1: required identity / classification ─────────────────────

    # SupabaseDal handle. The recorder calls dal.record_usage_event(state)
    # on a daemon thread when the request finishes — passing this entire
    # state object positionally; the DAL reads the fields it needs.
    # Typed Any to avoid importing SupabaseDal at runtime (circular import
    # via ChatRequest).
    dal: Any

    # Backend taxonomy of which call surface initiated this chat. Stable
    # values dashboards group by:
    #   'user_chat'         — direct POST /api/chat (default)
    #   'slack_chat'        — auto-detected from the runner's Slack prefix
    #                         in chat_request.ask, OR set explicitly by a
    #                         future runner-side change
    #   'agui_chat'         — set by the AG-UI handler
    #   'scheduled_prompt'  — set by ScheduledPromptsExecutor
    #   'health_check'      — set by /api/checks/execute
    request_type: str

    # LLM model string after Holmes' model routing. Whatever litellm
    # accepts: 'anthropic/claude-sonnet-4-5', 'openai/gpt-4o', etc.
    # Sourced from request_ai.llm.model in build_chat_recorder_state.
    model: str

    # Canonical litellm provider derived from `model`. Use the helper
    # `resolve_provider(model)`. Examples: 'anthropic', 'openai', 'azure',
    # 'bedrock'. Falls back to 'unknown' if litellm can't classify.
    provider: str

    # True when the LLM call hit a Robusta-managed model (Robusta paid
    # the token bill). Read from request_ai.llm.is_robusta_model. SaaS
    # dashboards filter on this column to hide cost / show only input
    # tokens for managed-model rows.
    is_robusta_model: bool

    # ── Group 2: optional identity / classification ─────────────────────

    # FE/runner-supplied finer UI flow label. Free-form text — adding new
    # values doesn't need a Holmes migration. Examples:
    #   'freeform'              — user typed a question in the FE
    #   'alert_investigation'   — chat opened from an alert
    #   'followup_logs'         — user clicked a follow-up action button
    #   'resource_chat'         — chat opened from a Kubernetes resource
    #   'slack' / 'teams'       — auto-detected for messaging-platform
    #                             chats; runner can override with finer
    #                             values like 'slack_alert_investigation'
    #   'scheduler' / 'operator' — set by scheduled-prompt / health-check
    request_source: Optional[str] = None

    # Opaque pointer to the entity the chat is *about*. Meaning is
    # implied by `request_source`. Examples:
    #   request_source='alert_investigation' → source_ref=<issue id>
    #   request_source='resource_chat'       → source_ref=<deployment id>
    #   request_source='operator' (checks)   → source_ref=<check name>
    # Free-form text; not a foreign key — the table this points to varies.
    source_ref: Optional[str] = None

    # Stable id grouping multi-turn chats so dashboards can show per-
    # conversation cost / token totals. Soft reference (NOT a FK).
    # Either matches Conversations.conversation_id (worker path) or the
    # FE-owned ChatHistory id (legacy /api/chat path); the discriminator
    # below tells dashboards which table to LEFT JOIN. NULL for single-
    # turn / non-UI flows (CLI, scheduled prompts, health checks).
    conversation_id: Optional[str] = None

    # Discriminator telling dashboards which table `conversation_id`
    # targets when it's non-NULL:
    #   'conversations' — worker path (set explicitly by the worker)
    #   'chat_history'  — direct /api/chat path (defaulted by chat() when
    #                     conversation_id is set and no override given)
    #   None            — no conversation context
    conversation_source: Optional[str] = None

    # UUID of the human who started the chat. Sourced from the auth-
    # validated session token (server.chat) or the Conversations row
    # (worker fallback). NULL for system / scheduled flows where there
    # is no human user. Used for per-user analytics and (today) by the
    # feedback RPC's auth.uid() check.
    user_id: Optional[str] = None

    # Holmes' cluster id (env-var string, e.g. 'production-east'). Falls
    # back to dal.cluster inside record_usage_event when left at None
    # here. NULL for CLI / ad-hoc flows that have no cluster context.
    cluster_id: Optional[str] = None

    # Per-request UUID. Auto-generated; you don't set this manually. The
    # stream wrapper injects it into the terminal event's `metadata` so
    # the FE can read it from `ai_answer_end` and pass it to the
    # `record_feedback` Supabase RPC later when the user clicks 👍/👎.
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # True for streaming responses (SSE), False for non-streaming. The
    # worker is always streaming; /api/chat respects chat_request.stream;
    # health checks are always non-streaming. Set by the entry point.
    is_streaming: bool = False

    # Marks server-internal calls (title generation, classifier prompts,
    # summarization, etc.) so user-facing dashboards can filter them out
    # of activity / cost metrics. Defaults False; set True at the entry
    # point for those flows. build_chat_recorder_state also auto-sets
    # True when request_source starts with 'internal_' (legacy convention).
    is_internal: bool = False

    # Forward-compatibility metadata bag. JSONB-serializable dict that
    # gets shallow-merged with backend-derived keys (backend wins on
    # collision) and stored as-is in the row's `meta` column.
    # Conventions:
    #   - Slack auto-detect populates meta['slack'] with slack_user_id /
    #     slack_triggered_at when the regex matches.
    #   - FE can add provisional fields (experiment_id, etc.) that
    #     haven't earned a real column yet.
    #   - Don't put PII or large strings (prompts, completions, tool
    #     output) here — that's out of scope for v1.
    meta: Dict[str, Any] = field(default_factory=dict)

    # ── Group 3: mutable runtime fields — DO NOT set at construction ────

    # Wall-clock start of the request (time.monotonic seconds). Used to
    # compute `duration_ms` when the row is written. Defaults to
    # construction time, which is when the entry point starts the work.
    t_start: float = field(default_factory=time.monotonic)

    # Aggregate token / cost counters. Filled by `stream_with_usage_recording`
    # from the ANSWER_END event's `metadata.costs`, or by
    # `record_from_llm_result` from the LLMResult's RequestStats fields.
    # None means the request didn't reach a terminal event with stats —
    # the recorder still writes a zero-stats row with status='aborted'/'error'.
    stats: Optional[RequestStats] = None

    # Number of LLM round-trips in this request (1 for a simple chat, N
    # for an agentic loop with N-1 tool turns). Filled by the wrapper
    # from the terminal event's `num_llm_calls`. 0 means no LLM call
    # completed (aborted / errored before the first response).
    iterations: int = 0

    # Number of TOOL_RESULT events the wrapper saw flow past during the
    # stream. Filled by the wrapper. The non-streaming path
    # (record_from_llm_result) instead reads len(llm_result.tool_calls).
    tool_call_count: int = 0

    # Last LLM iteration's finish reason: 'stop', 'length', 'tool_calls',
    # 'content_filter', etc. Filled by the wrapper from the terminal
    # event's `metadata.finish_reason`. Earlier iterations always end in
    # 'tool_calls'; only the final iteration carries a meaningful value.
    finish_reason: Optional[str] = None

    # Final outcome of the request. Filled by the wrapper or the
    # record_error helper. See ``RequestStatus`` above for the full
    # set of values and their meanings. The default ``SUCCESS`` is
    # overwritten by the wrapper's finally-block to ``ABORTED`` if no
    # terminal event was ever observed.
    status: RequestStatus = RequestStatus.SUCCESS

    @property
    def duration_ms(self) -> int:
        """Wall-clock milliseconds since ``t_start``.

        Computed on read so the value reflects "now minus when the request
        started" at the moment the row is written. Used by
        ``SupabaseDal.record_usage_event`` to populate the duration column.
        """
        return int((time.monotonic() - self.t_start) * 1000)

    # ── private helpers — kept on the class because they only operate on
    # this state. The public entry points (stream_with_usage_recording,
    # record_from_llm_result, record_error) stay as module-level functions
    # — the stream wrapper in particular has the stream as its primary
    # input, so a "method on state" shape would invert its natural reading.

    def _capture_costs(self, data: Dict[str, Any]) -> None:
        """Replace ``self.stats`` from an event's ``metadata.costs``.

        Called for both terminal events (ANSWER_END / APPROVAL_REQUIRED /
        ERROR) and mid-stream TOKEN_COUNT events. Each event carries the
        cumulative cost up to that point, so the latest one always wins —
        which is what we want: ANSWER_END's costs == final TOKEN_COUNT's
        costs in the success case, and the last seen TOKEN_COUNT gives
        partial cost in the mid-loop-exception case.
        """
        metadata = data.get("metadata") or {}
        costs = metadata.get("costs") or {}
        if not costs:
            return
        try:
            self.stats = RequestStats(**costs)
        except Exception:
            logging.debug(
                "Failed to materialize RequestStats from event costs",
                exc_info=True,
            )

    def _capture_terminal(self, data: Dict[str, Any]) -> None:
        """Pull cost/iterations/finish_reason from a terminal event's data.

        Terminal events (ANSWER_END / APPROVAL_REQUIRED / ERROR) carry the
        full picture: costs, iteration count, finish reason. Mid-stream
        TOKEN_COUNT events carry only costs — those go through
        ``_capture_costs`` directly.
        """
        self._capture_costs(data)
        # Explicit None-check rather than `or` so a legitimate 0 (unlikely
        # but not impossible) is preserved instead of falling back to
        # self.iterations.
        raw_iterations = data.get("num_llm_calls")
        if raw_iterations is not None:
            self.iterations = raw_iterations
        metadata = data.get("metadata") or {}
        self.finish_reason = (
            metadata.get("finish_reason") or self.finish_reason
        )

    def _fire(self) -> None:
        """Submit the dal write to the shared recorder thread pool.

        Fire-and-forget — the response path never waits on this. The
        executor caps concurrent writes (see ``_RECORDER_EXECUTOR`` at
        module top); under burst load, additional submissions queue
        inside the executor rather than spawning unbounded fresh threads.

        ``executor.submit`` raises ``RuntimeError`` if the executor has
        already been shut down (process exiting). Treat that as accepted
        loss — same fate as in-flight rows on the previous daemon-thread
        fire-and-forget shape.
        """
        if self.dal is None or not getattr(self.dal, "enabled", False):
            return
        try:
            _RECORDER_EXECUTOR.submit(self.dal.record_usage_event, self)
        except RuntimeError:
            # Executor was shut down — accept the loss.
            logging.debug(
                "Usage recorder executor is shut down; dropping row",
                exc_info=True,
            )
        except Exception:
            # Defense in depth — record_usage_event has its own try/except too.
            logging.exception("Failed to submit usage recorder write")


def stream_with_usage_recording(
    stream: Generator[StreamMessage, None, None],
    state: UsageRecorderState,
) -> Generator[StreamMessage, None, None]:
    """Forward stream events; capture state; record on stream end.

    Used by chat() and AG-UI. Watches for terminal events (ANSWER_END,
    APPROVAL_REQUIRED, ERROR) to extract final stats / counts / reason,
    counts TOOL_RESULT events along the way, and fires the recorder in
    a `finally` block so the row is written even on exceptions or
    client disconnects.

    Also injects ``state.request_id`` into the terminal event's
    ``metadata`` dict so the SSE formatter ships it back to the FE. The
    FE saves it from ``ai_answer_end`` and passes it to the
    ``public.record_feedback()`` Supabase RPC when the user clicks
    thumbs up/down.
    """
    saw_terminal = False
    try:
        for msg in stream:
            if msg.event == StreamEvents.TOOL_RESULT:
                state.tool_call_count += 1
            elif msg.event == StreamEvents.TOKEN_COUNT:
                # Cumulative cost broadcast after each successful LLM iteration
                # (and after compaction). Capturing it here is the only way to
                # record partial cost when the agentic loop raises mid-loop —
                # call_stream's local `stats` accumulator gets GC'd along with
                # the function frame on exception, so we'd otherwise write a
                # zero-stats row even though earlier iterations burned real
                # tokens. Each TOKEN_COUNT carries the running total, so the
                # last one we see before the error tells us exactly how much
                # the failed turn cost up to that point.
                state._capture_costs(msg.data)
            elif msg.event == StreamEvents.ANSWER_END:
                state._capture_terminal(msg.data)
                _inject_request_id(msg.data, state.request_id)
                state.status = RequestStatus.SUCCESS
                saw_terminal = True
            elif msg.event == StreamEvents.APPROVAL_REQUIRED:
                state._capture_terminal(msg.data)
                _inject_request_id(msg.data, state.request_id)
                state.status = RequestStatus.APPROVAL_REQUIRED
                saw_terminal = True
            elif msg.event == StreamEvents.ERROR:
                state._capture_terminal(msg.data)
                _inject_request_id(msg.data, state.request_id)
                state.status = RequestStatus.ERROR
                saw_terminal = True
            yield msg
    except Exception:
        if not saw_terminal:
            state.status = RequestStatus.ERROR
        raise
    finally:
        # If the inner stream ended without yielding any terminal event
        # (client disconnected mid-stream, generator exhausted abnormally),
        # `state.status` would still be the constructor default SUCCESS.
        # That's wrong — mark such cases as ABORTED so dashboards can
        # filter incomplete runs out of "successful chat" metrics.
        if not saw_terminal and state.status == RequestStatus.SUCCESS:
            state.status = RequestStatus.ABORTED
        state._fire()


def _inject_request_id(data: Dict[str, Any], request_id: str) -> None:
    """Drop request_id into data['metadata'] so the SSE formatter ships it
    to the FE. Creates the metadata dict if missing or non-dict-shaped.

    Stays a module-level function (not a method on UsageRecorderState)
    because it operates on the stream event's data dict, not on state —
    the only state field it reads is request_id, which it takes as an arg.
    """
    md = data.get("metadata")
    if not isinstance(md, dict):
        md = {}
        data["metadata"] = md
    md["request_id"] = request_id


def record_from_llm_result(
    state: UsageRecorderState,
    llm_result: "LLMResult",
) -> None:
    """Record a usage event from a non-streaming `ai.call(...)` result.

    Used by `holmes/checks/checks.py:execute_check` and any other caller
    that gets back an LLMResult directly. LLMResult IS-A RequestStats
    (it inherits the cost / token fields), so we copy them out via
    model_dump.
    """
    try:
        # LLMResult inherits from RequestStats and adds extra fields
        # (tool_calls, messages, finish_reason, ...). Ask Pydantic to filter
        # the dump down to RequestStats's own model_fields so the extras get
        # dropped without us hardcoding the stats field set here — when
        # RequestStats grows a new column, this stays correct automatically.
        state.stats = RequestStats(
            **llm_result.model_dump(include=set(RequestStats.model_fields))
        )
    except Exception:
        logging.debug("Failed to extract stats from LLMResult", exc_info=True)
        state.stats = RequestStats()

    state.iterations = getattr(llm_result, "num_llm_calls", None) or 1
    state.tool_call_count = len(getattr(llm_result, "tool_calls", None) or [])
    state.finish_reason = getattr(llm_result, "finish_reason", None)
    state.status = RequestStatus.SUCCESS
    state._fire()


def record_error(state: UsageRecorderState, exc: Exception) -> None:
    """Record a failed call where an exception bubbled before getting a result."""
    msg = str(exc).lower()
    if "rate" in msg and "limit" in msg:
        state.status = RequestStatus.RATE_LIMITED
    else:
        state.status = RequestStatus.ERROR
    state._fire()


__all__ = [
    "RequestStatus",
    "UsageRecorderState",
    "build_chat_recorder_state",
    "detect_slack_origin",
    "record_error",
    "record_from_llm_result",
    "resolve_provider",
    "stream_with_usage_recording",
]

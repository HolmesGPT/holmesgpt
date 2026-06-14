"""
Remote tool-call worker — executes cross-cluster tool calls.

A Holmes instance in another cluster (the caller) asked relay's platform-mcp
to run a tool here. platform-mcp created a row in the "RemoteToolCalls" table
and broadcast a 'pending_tool_calls' event on holmes:submit:{account}:{cluster}.
This worker claims such rows (claim_tool_calls RPC), runs exactly one tool per
row — no LLM loop — and writes tool_response + terminal status in one atomic
UPDATE (post_remote_tool_call_result RPC).

Tool calls run in their own thread pool (TOOL_CALLER_MAX_CONCURRENT) so they
never compete with user chats for the conversation worker's pool.

Design: relay repo, docs/design/2026-06-10_remote-tool-execution.md.
"""

import base64
import gzip
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Dict, Optional

from holmes.common.env_vars import (
    CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITH_REALTIME,
    CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITHOUT_REALTIME,
    REMOTE_TOOL_RESULT_COMPRESS_THRESHOLD_CHARS,
    REMOTE_TOOL_RESULT_MAX_BYTES,
    TOOL_CALLER_MAX_CONCURRENT,
)
from holmes.core.conversations_worker.models import RemoteToolCallStatus
from holmes.core.tools import (
    PrerequisiteCacheMode,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
    ToolsetTag,
)
from holmes.version import get_version

if TYPE_CHECKING:
    from holmes.config import Config
    from holmes.core.supabase_dal import SupabaseDal


def _error_response(error: str, invocation: Optional[str] = None) -> Dict[str, Any]:
    return {
        "status": StructuredToolResultStatus.ERROR.value,
        "data": None,
        "compressed": False,
        "data_gz_b64": None,
        "error": error,
        "invocation": invocation,
        "executor_holmes_version": get_version(),
    }


def serialize_tool_response(
    result: StructuredToolResult,
    elapsed_seconds: float,
    max_bytes: int = REMOTE_TOOL_RESULT_MAX_BYTES,
    compress_threshold: int = REMOTE_TOOL_RESULT_COMPRESS_THRESHOLD_CHARS,
) -> Dict[str, Any]:
    """Serialize a StructuredToolResult into the tool_response payload.

    - Images are dropped (text results only in v1).
    - Uncompressed data larger than max_bytes (1MB) is rejected with a
      narrow-the-query error.
    - Data over compress_threshold chars is stored gzip+base64 so the DB row,
      WAL and realtime traffic stay small; relay inflates before replying —
      but only when the base64 of the gzip is actually smaller than the
      original text (incompressible data would otherwise grow ~33%).
    """
    data, _is_json = result.stringify_data(compact=False)
    data = data or ""

    payload: Dict[str, Any] = {
        "status": result.status.value
        if hasattr(result.status, "value")
        else str(result.status),
        "data": data,
        "compressed": False,
        "data_gz_b64": None,
        "error": result.error,
        "return_code": result.return_code,
        "invocation": result.invocation,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "executor_holmes_version": get_version(),
    }

    size = len(data.encode("utf-8", errors="replace"))
    if size > max_bytes:
        payload["status"] = StructuredToolResultStatus.ERROR.value
        payload["data"] = None
        payload["error"] = (
            f"result too large ({size} bytes > {max_bytes}); narrow the query "
            "(smaller time range, tighter filters, lower limit)"
        )
        return payload

    if len(data) > compress_threshold:
        gz_b64 = base64.b64encode(
            gzip.compress(data.encode("utf-8", errors="replace"))
        ).decode("ascii")
        if len(gz_b64) < len(data):
            payload["data_gz_b64"] = gz_b64
            payload["compressed"] = True
            payload["data"] = None

    return payload


class ToolCallWorker:
    """Claims and executes remote tool calls for this cluster.

    Lifecycle mirrors the conversation worker's claim loop, with its own
    notify event (woken by the 'pending_tool_calls' broadcast via
    RealtimeManager) and its own thread pool.
    """

    def __init__(self, dal: "SupabaseDal", config: "Config", holmes_id: str):
        self.dal = dal
        self.config = config
        self.holmes_id = holmes_id

        self._running = False
        self._notify_event = threading.Event()
        self._claim_thread: Optional[threading.Thread] = None
        self._pool: Optional[ThreadPoolExecutor] = None
        self._llm = None  # lazily created; used only for in-tool token counting
        self._realtime_connected = lambda: False

    # ---- lifecycle ----

    def start(self, realtime_connected_fn=None) -> None:
        if self._running:
            return
        self._running = True
        if realtime_connected_fn is not None:
            self._realtime_connected = realtime_connected_fn
        self._pool = ThreadPoolExecutor(
            max_workers=TOOL_CALLER_MAX_CONCURRENT,
            thread_name_prefix="tool-call-worker",
        )
        self._claim_thread = threading.Thread(
            target=self._claim_loop, daemon=True, name="tool-call-claim-loop"
        )
        self._claim_thread.start()
        logging.info(
            "ToolCallWorker started (holmes_id=%s, max_concurrent=%d)",
            self.holmes_id,
            TOOL_CALLER_MAX_CONCURRENT,
        )

    def stop(self) -> None:
        self._running = False
        self._notify_event.set()
        if self._claim_thread:
            self._claim_thread.join(timeout=5)
            self._claim_thread = None
        if self._pool:
            self._pool.shutdown(wait=False)
            self._pool = None

    def claim_pending_tool_calls(self) -> None:
        """Routing target for RealtimeWorker on 'pending_tool_calls'
        broadcasts. Non-blocking: wakes the claim loop."""
        self._notify_event.set()

    # ---- claim loop ----

    def _claim_loop(self) -> None:
        # Claim once on startup to drain anything pending before we
        # subscribed (or while we were down).
        self._try_claim_and_dispatch()
        while self._running:
            if self._realtime_connected():
                timeout = CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITH_REALTIME
            else:
                timeout = CONVERSATION_WORKER_POLL_INTERVAL_SECONDS_WITHOUT_REALTIME
            self._notify_event.wait(timeout=timeout)
            if not self._running:
                break
            self._notify_event.clear()
            try:
                self._try_claim_and_dispatch()
            except Exception:
                logging.exception("Error in ToolCallWorker claim loop", exc_info=True)

    def _try_claim_and_dispatch(self) -> None:
        claimed = self.dal.claim_tool_calls(self.holmes_id)
        if not claimed:
            return
        logging.info("ToolCallWorker: claimed %d tool call(s)", len(claimed))
        pool = self._pool
        if pool is None or not self._running:
            return
        for row in claimed:
            if not self._running:
                return
            try:
                pool.submit(self._execute_safe, row)
            except RuntimeError:
                # Pool shut down between the claim and here (stop() raced).
                # The row stays 'queued' and relay times it out → 'stopped'.
                logging.warning(
                    "ToolCallWorker: pool shut down; dropping claimed row %s",
                    row.get("id"),
                )
                return

    # ---- execution ----

    def _execute_safe(self, row: Dict[str, Any]) -> None:
        row_id = row.get("id")
        try:
            response = self._execute(row)
            status = RemoteToolCallStatus.COMPLETED
        except Exception as e:
            logging.exception(
                "ToolCallWorker: unexpected failure executing %s", row_id
            )
            response = _error_response(f"executor failure: {e}")
            status = RemoteToolCallStatus.FAILED
        ok = self.dal.post_remote_tool_call_result(
            tool_call_id=row_id,
            assignee=self.holmes_id,
            status=status.value,
            tool_response=response,
        )
        if not ok:
            # Row was reassigned or stopped (relay timed out) — log and drop.
            logging.warning(
                "ToolCallWorker: result for %s rejected (stale assignee or "
                "terminal row); dropping",
                row_id,
            )

    def _execute(self, row: Dict[str, Any]) -> Dict[str, Any]:
        tool_request = row.get("tool_request") or {}
        metadata = row.get("metadata") or {}
        tool_name = tool_request.get("tool_name")
        tool_params = dict(tool_request.get("tool_params") or {})
        instance = tool_request.get("instance")

        # 1. Version guard: identical caller/executor versions only.
        source_version = metadata.get("source_version")
        my_version = get_version()
        if source_version != my_version:
            return _error_response(
                f"version mismatch caller={source_version} executor={my_version}"
            )

        if not tool_name:
            return _error_response("tool_request.tool_name is missing")

        # 2. Resolve + exposure checks (defense in depth vs publish-time filtering).
        executor = self._get_tool_executor()
        tool = executor.tools_by_name.get(tool_name)
        toolset = executor._tool_to_toolset.get(tool_name)
        if tool is None or toolset is None:
            return _error_response(f"unknown tool '{tool_name}' on this cluster")
        if toolset.is_core:
            return _error_response(
                f"tool '{tool_name}' belongs to internal toolset '{toolset.name}' "
                "and cannot run remotely"
            )
        if not toolset.expose_remotely:
            return _error_response(
                f"toolset '{toolset.name}' is not exposed for remote execution"
            )
        if tool._is_restricted():
            return _error_response(f"tool '{tool_name}' is restricted")

        # Instance resolution: given -> must be exposed; omitted with exactly
        # one exposed instance -> default; omitted with several -> error.
        get_instances = getattr(toolset, "remote_exposed_instances", None)
        exposed = get_instances() if callable(get_instances) else None
        if exposed is not None:
            if instance:
                if instance not in exposed:
                    return _error_response(
                        f"instance '{instance}' is not exposed on this cluster; "
                        f"exposed instances: {sorted(exposed)}"
                    )
                tool_params["instance"] = instance
            elif len(exposed) == 1:
                tool_params.setdefault("instance", exposed[0])
            else:
                return _error_response(
                    "this toolset has several instances on this cluster; pass "
                    f"'instance' explicitly. Exposed instances: {sorted(exposed)}"
                )
        elif instance:
            return _error_response(
                f"tool '{tool_name}' does not support instances on this cluster"
            )

        # 3. Pre-approved mode only — no approval round-trip.
        context = ToolInvokeContext(
            tool_number=None,
            user_approved=False,
            llm=self._get_llm(),
            max_token_count=int(tool_request.get("max_token_count")),
            tool_call_id=str(tool_request.get("tool_call_id") or row.get("id") or ""),
            tool_name=tool_name,
            session_approved_prefixes=[],
            request_context={"user_id": row.get("user_id")},
        )
        approval = tool._get_approval_requirement(tool_params, context)
        if approval and approval.needs_approval:
            return _error_response(
                "command/tool requires approval; approvals are not supported "
                f"for remote execution ({approval.reason})"
            )

        started = time.monotonic()
        result = tool.invoke(tool_params, context)
        elapsed = time.monotonic() - started

        if result.status == StructuredToolResultStatus.APPROVAL_REQUIRED:
            return _error_response(
                "command/tool requires approval; approvals are not supported "
                "for remote execution"
            )

        # 4. Inline result, <=1MB uncompressed, gzip over 100k, no images, no files.
        result.images = None
        return serialize_tool_response(result, elapsed)

    # ---- helpers ----

    def _get_tool_executor(self):
        # reuse_executor=True returns the same cached executor the toolset
        # sync built at startup — no prerequisite re-checks per call.
        return self.config.create_tool_executor(
            self.dal,
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLUSTER],
            enable_all_toolsets_possible=False,
            prerequisite_cache=PrerequisiteCacheMode.ENABLED,
            reuse_executor=True,
        )

    def _get_llm(self):
        # The executor's own configured LLM: used only for in-tool token
        # counting (truncation heuristics). The caller-derived budget
        # (max_token_count) is wired separately through tool_request.
        if self._llm is None:
            self._llm = self.config._get_llm()
        return self._llm

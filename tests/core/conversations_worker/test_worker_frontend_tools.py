"""Tests for ConversationWorker frontend-tool injection."""
import threading
from collections import deque
from unittest.mock import MagicMock

from holmes.core.conversations_worker.models import ConversationTask
from holmes.core.conversations_worker.worker import ConversationWorker
from holmes.core.models import (
    ChatRequest,
    FrontendToolDefinition,
    FrontendToolMode,
)
from holmes.core.tools_utils.frontend_tools import (
    FrontendNoopTool,
    FrontendPauseTool,
)


def _bare_worker():
    w = ConversationWorker.__new__(ConversationWorker)
    w.dal = MagicMock()
    w.dal.enabled = True
    w.dal.update_conversation_status = MagicMock(return_value=True)
    w.config = MagicMock()
    w.chat_function = MagicMock()
    w.holmes_id = "h-test"
    w._running = True
    w._claim_thread = None
    w._notify_event = threading.Event()
    w._executor = MagicMock()
    w._active_conversation_ids = set()
    w._active_lock = threading.Lock()
    w._queued_tasks = deque()
    w._queued_lock = threading.Lock()
    w._dispatch_lock = threading.Lock()
    w._realtime_manager = None
    return w


def _task():
    return ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
    )


def _ai_with_backend_tools(*backend_tool_names):
    ai = MagicMock()
    ai.tool_executor.tools_by_name = {n: MagicMock(name=n) for n in backend_tool_names}

    cloned_executor = MagicMock(name="cloned_executor")
    ai.tool_executor.clone_with_extra_tools = MagicMock(return_value=cloned_executor)

    cloned_ai = MagicMock(name="cloned_ai")
    ai.with_executor = MagicMock(return_value=cloned_ai)
    return ai, cloned_ai, cloned_executor


def test_no_frontend_tools_returns_ai_unchanged():
    worker = _bare_worker()
    ai, _, _ = _ai_with_backend_tools("kubectl_get")
    chat_request = ChatRequest(ask="hi", frontend_tools=None)

    result = worker._inject_frontend_tools(ai, chat_request, _task())

    assert result is ai
    ai.tool_executor.clone_with_extra_tools.assert_not_called()
    ai.with_executor.assert_not_called()


def test_pause_mode_frontend_tool_is_built_and_executor_cloned():
    worker = _bare_worker()
    ai, cloned_ai, cloned_executor = _ai_with_backend_tools("kubectl_get")
    chat_request = ChatRequest(
        ask="hi",
        frontend_tools=[
            FrontendToolDefinition(
                name="create_dashboard",
                description="Create a dashboard in the user's browser",
                parameters={"type": "object", "properties": {}},
                mode=FrontendToolMode.PAUSE,
            )
        ],
    )

    result = worker._inject_frontend_tools(ai, chat_request, _task())

    assert result is cloned_ai
    ai.tool_executor.clone_with_extra_tools.assert_called_once()
    extras = ai.tool_executor.clone_with_extra_tools.call_args[0][0]
    assert len(extras) == 1
    assert isinstance(extras[0], FrontendPauseTool)
    assert extras[0].name == "create_dashboard"
    ai.with_executor.assert_called_once_with(cloned_executor)


def test_noop_mode_frontend_tool_is_built_with_canned_response():
    worker = _bare_worker()
    ai, cloned_ai, _ = _ai_with_backend_tools("kubectl_get")
    chat_request = ChatRequest(
        ask="hi",
        frontend_tools=[
            FrontendToolDefinition(
                name="emit_telemetry",
                description="Fire-and-forget telemetry event",
                parameters={"type": "object", "properties": {}},
                mode=FrontendToolMode.NOOP,
                noop_response="ack",
            )
        ],
    )

    result = worker._inject_frontend_tools(ai, chat_request, _task())

    assert result is cloned_ai
    extras = ai.tool_executor.clone_with_extra_tools.call_args[0][0]
    assert len(extras) == 1
    assert isinstance(extras[0], FrontendNoopTool)
    assert extras[0].canned_response == "ack"


def test_collision_with_backend_tool_fails_conversation():
    worker = _bare_worker()
    ai, _, _ = _ai_with_backend_tools("kubectl_get")
    chat_request = ChatRequest(
        ask="hi",
        frontend_tools=[
            FrontendToolDefinition(
                name="kubectl_get",
                description="conflicts on purpose",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )
    task = _task()

    result = worker._inject_frontend_tools(ai, chat_request, task)

    assert result is None
    ai.tool_executor.clone_with_extra_tools.assert_not_called()
    ai.with_executor.assert_not_called()
    post_calls = worker.dal.post_conversation_events.call_args_list
    assert post_calls
    err = post_calls[0].kwargs["events"][0]
    assert err["event"] == "error"
    assert "kubectl_get" in err["data"]["description"]
    failed_calls = [
        c for c in worker.dal.update_conversation_status.call_args_list
        if c.kwargs.get("status") == "failed"
    ]
    assert failed_calls


def test_duplicate_frontend_tool_names_fail_conversation():
    worker = _bare_worker()
    ai, _, _ = _ai_with_backend_tools("kubectl_get")
    chat_request = ChatRequest(
        ask="hi",
        frontend_tools=[
            FrontendToolDefinition(
                name="create_dashboard",
                description="first definition",
                mode=FrontendToolMode.PAUSE,
            ),
            FrontendToolDefinition(
                name="create_dashboard",
                description="duplicate — should be rejected",
                mode=FrontendToolMode.NOOP,
            ),
        ],
    )
    task = _task()

    result = worker._inject_frontend_tools(ai, chat_request, task)

    assert result is None
    ai.tool_executor.clone_with_extra_tools.assert_not_called()
    ai.with_executor.assert_not_called()
    post_calls = worker.dal.post_conversation_events.call_args_list
    assert post_calls
    err = post_calls[0].kwargs["events"][0]
    assert err["event"] == "error"
    assert "create_dashboard" in err["data"]["description"]


def test_mixed_pause_and_noop_tools_are_both_built():
    worker = _bare_worker()
    ai, _, _ = _ai_with_backend_tools("kubectl_get")
    chat_request = ChatRequest(
        ask="hi",
        frontend_tools=[
            FrontendToolDefinition(
                name="ask_user",
                description="pause and ask",
                mode=FrontendToolMode.PAUSE,
            ),
            FrontendToolDefinition(
                name="emit_telemetry",
                description="fire and forget",
                mode=FrontendToolMode.NOOP,
            ),
        ],
    )

    worker._inject_frontend_tools(ai, chat_request, _task())

    extras = ai.tool_executor.clone_with_extra_tools.call_args[0][0]
    assert [type(t) for t in extras] == [FrontendPauseTool, FrontendNoopTool]
    assert {t.name for t in extras} == {"ask_user", "emit_telemetry"}

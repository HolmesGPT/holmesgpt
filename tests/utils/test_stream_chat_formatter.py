import json

from holmes.utils.stream import StreamEvents, StreamMessage, stream_chat_formatter


def _events(*messages):
    out = []
    for frame in stream_chat_formatter(iter(messages)):
        event_line, data_line = frame.strip().splitlines()
        out.append(
            (event_line[len("event: ") :], json.loads(data_line[len("data: ") :]))
        )
    return out


def test_answer_end_carries_only_the_api_fields():
    ((name, data),) = _events(
        StreamMessage(
            event=StreamEvents.ANSWER_END,
            data={
                "content": "done",
                "messages": [{"role": "user"}],
                "metadata": {"k": 1},
                "costs": {},
            },
        )
    )
    assert name == "ai_answer_end"
    assert data == {
        "analysis": "done",
        "conversation_history": [{"role": "user"}],
        "metadata": {"k": 1},
    }


def test_approval_required_carries_only_the_api_fields():
    ((name, data),) = _events(
        StreamMessage(
            event=StreamEvents.APPROVAL_REQUIRED,
            data={
                "content": None,
                "messages": [],
                "pending_approvals": [{"tool_call_id": "t1"}],
            },
        )
    )
    assert name == "approval_required"
    assert data == {
        "analysis": None,
        "conversation_history": [],
        "requires_approval": True,
        "pending_approvals": [{"tool_call_id": "t1"}],
        "pending_frontend_tool_calls": [],
    }

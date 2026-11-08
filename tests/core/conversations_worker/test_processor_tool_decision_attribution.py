"""Tool decisions read from ConversationEvents count as approvals only when
they carry decided_by, which the database sets for the person (or the Slack
user relay maps) who made them (ROB-1671). The account API role every Holmes
signs in as can write conversation events too; its decisions arrive without
one and must not run anything."""

from unittest.mock import MagicMock, patch

import pytest

from holmes.core.conversations_worker.models import ConversationTask
from holmes.core.conversations_worker.processor import ConversationProcessor


def _chat_request(decisions):
    dal = MagicMock()
    dal.enabled = True
    dal.update_conversation_status = MagicMock(return_value=True)
    worker = ConversationProcessor(dal=dal, config=MagicMock(), holmes_id="h-test")
    task = ConversationTask(
        conversation_id="c1", account_id="a1", cluster_id="cl1", origin="chat", request_sequence=1
    )
    dal.get_conversation_events = MagicMock(
        return_value=[
            {"event": "user_message", "data": {"ask": "taint the node"}, "ts": "1"},
            {
                "event": "approval_required",
                "data": {
                    "messages": [
                        {"role": "system", "content": "s"},
                        {"role": "user", "content": "taint the node"},
                    ],
                    "pending_approvals": [],
                },
                "ts": "2",
            },
            {"event": "user_message", "data": {"tool_decisions": decisions}, "ts": "3"},
        ]
    )
    with patch.object(ConversationProcessor, "_run_chat_and_publish") as run_chat:
        worker._process_conversation(task)
    run_chat.assert_called_once()
    return run_chat.call_args.args[1]


def test_an_approval_with_a_decider_is_kept():
    request = _chat_request(
        [{"tool_call_id": "t1", "approved": True, "decided_by": "user-1", "save_prefixes": ["kubectl taint"]}]
    )

    [decision] = request.tool_decisions
    assert decision.approved is True
    assert decision.save_prefixes == ["kubectl taint"]


def test_an_approval_nobody_is_recorded_as_making_does_not_run():
    request = _chat_request(
        [{"tool_call_id": "t1", "approved": True, "save_prefixes": ["sudo"]}]
    )

    [decision] = request.tool_decisions
    assert decision.approved is False
    assert decision.verified is False
    assert not decision.save_prefixes
    assert "not recorded as made by a person" in decision.feedback


def test_a_blank_or_non_string_decider_does_not_approve():
    request = _chat_request(
        [
            {"tool_call_id": "t1", "approved": True, "decided_by": ""},
            {"tool_call_id": "t2", "approved": True, "decided_by": 7},
        ]
    )

    assert [d.approved for d in request.tool_decisions] == [False, False]


def test_a_denial_passes_through_with_its_feedback():
    request = _chat_request(
        [{"tool_call_id": "t1", "approved": False, "feedback": "use cordon instead"}]
    )

    [decision] = request.tool_decisions
    assert decision.approved is False
    assert decision.verified is True
    assert decision.feedback == "use cordon instead"


def test_each_decision_is_judged_on_its_own():
    request = _chat_request(
        [
            {"tool_call_id": "t1", "approved": True, "decided_by": "user-1"},
            {"tool_call_id": "t2", "approved": True},
        ]
    )

    assert [(d.tool_call_id, d.approved) for d in request.tool_decisions] == [
        ("t1", True),
        ("t2", False),
    ]


@pytest.mark.parametrize("approved", [1, "true", "yes"])
def test_a_truthy_non_boolean_approval_without_a_decider_does_not_run(approved):
    request = _chat_request([{"tool_call_id": "t1", "approved": approved}])

    [decision] = request.tool_decisions
    assert decision.approved is False
    assert decision.verified is False


@pytest.mark.parametrize("approved", [0, "false", "no", "off"])
def test_a_falsy_non_boolean_denial_stays_a_denial(approved):
    request = _chat_request(
        [{"tool_call_id": "t1", "approved": approved, "feedback": "use cordon instead"}]
    )

    [decision] = request.tool_decisions
    assert decision.approved is False
    assert decision.verified is True
    assert decision.feedback == "use cordon instead"

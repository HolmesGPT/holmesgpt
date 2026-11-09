"""Tool decisions read from ConversationEvents count as approvals only when
they carry decided_by, which the UI (the signed-in user) and relay (the Slack
user) write, and the account's confirmation mode lets that person approve
(ROB-1671). Any other approval must not run anything."""

import json
from unittest.mock import MagicMock, patch

import pytest

import holmes.utils.approval_tokens as approval_tokens
from holmes.core.conversations_worker.models import ConversationTask
from holmes.core.conversations_worker.processor import (
    APPROVALS_DISABLED_FEEDBACK,
    NOT_ADMIN_APPROVAL_FEEDBACK,
    NOT_OWNER_APPROVAL_FEEDBACK,
    UNATTRIBUTED_APPROVAL_FEEDBACK,
    ConversationProcessor,
)
from holmes.core.supabase_dal import ConfirmationMode
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.utils.stream import StreamEvents


def _chat_request(decisions, mode=ConfirmationMode.ENABLE_ALL, owner=None, admins=(), dal=None):
    dal = dal or MagicMock()
    dal.enabled = True
    dal.update_conversation_status = MagicMock(return_value=True)
    dal.get_confirmation_mode = MagicMock(return_value=mode)
    dal.is_account_admin = MagicMock(side_effect=lambda user_id: user_id in admins)
    worker = ConversationProcessor(dal=dal, config=MagicMock(), holmes_id="h-test")
    task = ConversationTask(
        conversation_id="c1",
        account_id="a1",
        cluster_id="cl1",
        origin="chat",
        request_sequence=1,
        user_id=owner,
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


@pytest.mark.parametrize(
    ("decided_by", "runs"), [(None, False), ("user-1", True)], ids=["no decider", "decider"]
)
def test_a_validly_signed_pending_call_runs_only_with_a_decider(decided_by, runs, monkeypatch):
    """The approval token proves Holmes proposed this exact call; it says
    nothing about who approved it. A forged decision for a genuine pending
    call carries a valid token, so attribution has to stop it on its own."""
    monkeypatch.setattr(approval_tokens, "SIGNING_KEY", b"\x42" * 32)
    args = json.dumps({"args": ["taint", "node", "n1", "k=v:NoSchedule"]})
    decision = {"tool_call_id": "tc1", "approved": True}
    if decided_by:
        decision["decided_by"] = decided_by
    [validated] = _chat_request([decision]).tool_decisions

    ai = ToolCallingLLM(tool_executor=MagicMock(), max_steps=5, llm=MagicMock(), tool_results_dir=None)
    ai._directly_invoke_tool_call = MagicMock(
        return_value=StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data="tainted")
    )
    messages = [
        {"role": "user", "content": "taint n1"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "run_kubectl_command", "arguments": args},
                    "pending_approval": True,
                    "approval_token": approval_tokens.mint_token("tc1", "run_kubectl_command", args),
                }
            ],
        },
    ]
    with patch("holmes.core.tool_calling_llm.spill_oversized_tool_result", return_value=0):
        _, events = ai._execute_tool_decisions(messages=messages, tool_decisions=[validated])

    [result] = [e.data for e in events if e.event == StreamEvents.TOOL_RESULT]
    assert ai._directly_invoke_tool_call.called is runs
    if not runs:
        assert result["result"]["status"] == StructuredToolResultStatus.ERROR.value
        assert UNATTRIBUTED_APPROVAL_FEEDBACK in result["result"]["error"]


def _approval(decided_by="user-1"):
    return [{"tool_call_id": "t1", "approved": True, "decided_by": decided_by, "save_prefixes": ["kubectl"]}]


@pytest.mark.parametrize(
    ("mode", "owner", "decided_by", "admins", "refusal"),
    [
        (ConfirmationMode.DISABLED, "user-1", "user-1", ("user-1",), APPROVALS_DISABLED_FEEDBACK),
        (ConfirmationMode.DISABLED, None, "slack:T1:U1", (), APPROVALS_DISABLED_FEEDBACK),
        (ConfirmationMode.ENABLE_ALL, "user-1", "user-1", (), None),
        (ConfirmationMode.ENABLE_ALL, "user-1", "user-2", ("user-2",), NOT_OWNER_APPROVAL_FEEDBACK),
        (ConfirmationMode.ENABLE_ALL, "user-1", "slack:T1:U1", (), NOT_OWNER_APPROVAL_FEEDBACK),
        (ConfirmationMode.ENABLE_ALL, None, "slack:T1:U1", (), None),
        (ConfirmationMode.ENABLE_ADMINS, "user-1", "user-1", ("user-1",), None),
        (ConfirmationMode.ENABLE_ADMINS, "user-1", "user-1", (), NOT_ADMIN_APPROVAL_FEEDBACK),
        (ConfirmationMode.ENABLE_ADMINS, "user-1", "user-2", ("user-2",), NOT_OWNER_APPROVAL_FEEDBACK),
        (ConfirmationMode.ENABLE_ADMINS, None, "user-1", ("user-1",), NOT_ADMIN_APPROVAL_FEEDBACK),
    ],
    ids=[
        "disabled: owner approving",
        "disabled: ownerless Slack approval",
        "enable-all: owner approving",
        "enable-all: another user approving an owned chat",
        "enable-all: Slack user approving an owned chat",
        "enable-all: ownerless chat",
        "enable-admins: admin owner",
        "enable-admins: non-admin owner",
        "enable-admins: admin approving someone else's chat",
        "enable-admins: ownerless chat",
    ],
)
def test_the_confirmation_mode_and_owner_decide_who_may_approve(mode, owner, decided_by, admins, refusal):
    [decision] = _chat_request(_approval(decided_by), mode=mode, owner=owner, admins=admins).tool_decisions

    if refusal is None:
        assert decision.approved is True
        assert decision.save_prefixes == ["kubectl"]
    else:
        assert decision.approved is False
        assert decision.verified is False
        assert not decision.save_prefixes
        assert decision.feedback == refusal


def test_a_denial_does_not_look_up_the_mode_or_the_approver():
    dal = MagicMock()
    _chat_request([{"tool_call_id": "t1", "approved": False}], owner="user-1", dal=dal)

    dal.get_confirmation_mode.assert_not_called()
    dal.is_account_admin.assert_not_called()


def test_the_admin_lookup_is_the_owner_not_the_decision_text():
    dal = MagicMock()
    _chat_request(_approval("user-1"), mode=ConfirmationMode.ENABLE_ADMINS, owner="user-1", admins=("user-1",), dal=dal)

    dal.is_account_admin.assert_called_once_with("user-1")

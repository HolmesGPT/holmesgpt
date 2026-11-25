from unittest.mock import MagicMock, patch

import pytest
from prompt_toolkit.styles import Style
from rich.console import Console

from holmes.core.models import PendingToolApproval
from holmes.interactive import decide_tool_approval, handle_tool_approval

FETCH = PendingToolApproval(
    tool_call_id="call-1",
    tool_name="fetch_webpage",
    description="Internet: Fetch Webpage https://docs.vendor.test/page",
    params={"url": "https://docs.vendor.test/page"},
)
BASH = PendingToolApproval(
    tool_call_id="call-2",
    tool_name="bash",
    description="docker ps",
    params={"command": "docker ps", "suggested_prefixes": ["docker ps"]},
)


def decide(pending, menu_choice, feedback=""):
    session = MagicMock()
    session.prompt.return_value = feedback
    with (
        patch("holmes.interactive._run_inline_menu", return_value=menu_choice) as menu,
        patch("holmes.interactive.PromptSession", return_value=session),
        patch("holmes.interactive._save_approved_prefixes") as save,
    ):
        decision = handle_tool_approval(pending, Style([]), Console())
    return decision, menu, save


def test_fetch_approval_offers_no_allow_list_option():
    decision, menu, save = decide(FETCH, 0)

    assert decision == (True, None)
    assert menu.call_args.args[0] == [
        "Yes",
        "No, and tell Holmes what to do differently",
    ]
    assert "operation" in menu.call_args.kwargs["header"].title
    save.assert_not_called()


@pytest.mark.parametrize(
    "feedback,expected", [("use the runbook", "use the runbook"), ("  ", None)]
)
def test_fetch_denial_returns_optional_feedback(feedback, expected):
    decision, _, save = decide(FETCH, 1, feedback)

    assert decision == (False, expected)
    save.assert_not_called()


def test_fetch_cancelled_menu_is_a_denial():
    decision, _, _ = decide(FETCH, None)

    assert decision == (False, None)


def test_bash_approval_keeps_allow_list_option():
    decision, menu, save = decide(BASH, 1)

    assert decision == (True, None)
    assert len(menu.call_args.args[0]) == 3
    assert "bash command" in menu.call_args.kwargs["header"].title
    save.assert_called_once_with(["docker ps"])


def test_bash_denial_is_the_third_option():
    decision, _, save = decide(BASH, 2, "try kubectl")

    assert decision == (False, "try kubectl")
    save.assert_not_called()


MCP_WITH_COMMAND = PendingToolApproval(
    tool_call_id="call-3",
    tool_name="run_job",
    description="run_job nightly",
    params={"command": "nightly"},
)
BASH_NO_PREFIX = PendingToolApproval(
    tool_call_id="call-4",
    tool_name="bash",
    description="docker ps",
    params={"command": "docker ps"},
)


@pytest.mark.parametrize("pending", [MCP_WITH_COMMAND, BASH_NO_PREFIX])
def test_no_allow_list_option_without_a_bash_prefix_to_save(pending):
    decision, menu, save = decide(pending, 0)

    assert decision == (True, None)
    assert len(menu.call_args.args[0]) == 2
    save.assert_not_called()


def test_command_param_does_not_make_a_tool_bash():
    _, menu, _ = decide(MCP_WITH_COMMAND, 0)

    assert "operation" in menu.call_args.kwargs["header"].title


REMOTE_BASH = PendingToolApproval(
    tool_call_id="call-5",
    tool_name="remote_bash",
    description="docker ps",
    params={"command": "docker ps", "suggested_prefixes": ["docker ps"]},
)


def test_remote_bash_prompt_matches_bash():
    decision, menu, save = decide(REMOTE_BASH, 1)

    assert decision == (True, None)
    assert len(menu.call_args.args[0]) == 3
    assert "bash command" in menu.call_args.kwargs["header"].title
    save.assert_called_once_with(["docker ps"])


@pytest.mark.parametrize("pending", [BASH, REMOTE_BASH])
def test_bash_always_allow_approves_bash_without_asking(pending):
    with patch("holmes.interactive.handle_tool_approval") as ask:
        decision = decide_tool_approval(pending, True, Style([]), Console())

    assert decision == (True, None)
    ask.assert_not_called()


@pytest.mark.parametrize("always_allow,pending", [(True, FETCH), (False, BASH)])
def test_other_approvals_ask_the_user(always_allow, pending):
    with patch(
        "holmes.interactive.handle_tool_approval", return_value=(False, "no")
    ) as ask:
        decision = decide_tool_approval(pending, always_allow, Style([]), Console())

    assert decision == (False, "no")
    ask.assert_called_once()

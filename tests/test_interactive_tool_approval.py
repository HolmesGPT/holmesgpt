from unittest.mock import MagicMock, patch

import pytest
from prompt_toolkit.styles import Style
from rich.console import Console

from holmes.core.models import PendingToolApproval
from holmes.interactive import handle_tool_approval

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

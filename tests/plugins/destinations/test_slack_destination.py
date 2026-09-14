"""Tests for Slack destination (thread targeting)."""

from unittest.mock import MagicMock, patch

import pytest

from holmes.core.issue import Issue, IssueStatus
from holmes.core.models import ToolCallResult
from holmes.core.tool_calling_llm import LLMResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.plugins.destinations.slack.plugin import SlackDestination


@pytest.fixture
def minimal_issue() -> Issue:
    return Issue(
        id="hc-1",
        name="Health Check Failed: x",
        source_instance_id="cluster",
        source_type="HealthCheck",
        presentation_status=IssueStatus.OPEN,
        presentation_key_metadata="*Check:* `x`",
        show_status_in_title=False,
    )


def test_chat_post_message_includes_thread_ts_when_configured(
    minimal_issue: Issue,
) -> None:
    with patch("holmes.plugins.destinations.slack.plugin.WebClient") as wc_cls:
        mock_client = MagicMock()
        wc_cls.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ts": "9999.8888"}

        dest = SlackDestination("xoxb-test", "C01234567", thread_ts="1111.2222")
        dest.send_issue(
            minimal_issue,
            LLMResult(result="failure analysis", tool_calls=[], messages=[]),
        )

        first = mock_client.chat_postMessage.call_args_list[0]
        assert first.kwargs["channel"] == "C01234567"
        assert first.kwargs["thread_ts"] == "1111.2222"


def test_follow_up_messages_use_root_thread_ts_when_replying_in_thread(
    minimal_issue: Issue,
) -> None:
    """Replies in an existing thread must keep using the parent thread_ts."""
    with patch("holmes.plugins.destinations.slack.plugin.WebClient") as wc_cls:
        mock_client = MagicMock()
        wc_cls.return_value = mock_client
        mock_client.chat_postMessage.return_value = {"ts": "reply.new.ts"}
        mock_client.files_upload_v2.return_value = {
            "file": {"permalink": "https://example.com/f"}
        }

        tool_calls = [
            ToolCallResult(
                tool_call_id="tc1",
                tool_name="k8s",
                description="kubectl get pods",
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data={"ok": True},
                ),
            )
        ]

        dest = SlackDestination("xoxb-test", "C01234567", thread_ts="1111.2222")
        dest.send_issue(
            minimal_issue,
            LLMResult(
                result="failure analysis",
                tool_calls=tool_calls,
                messages=[],
            ),
        )

        thread_posts = [
            c
            for c in mock_client.chat_postMessage.call_args_list
            if c.kwargs.get("thread_ts")
        ]
        assert thread_posts, "expected threaded follow-up messages"
        for c in thread_posts:
            assert c.kwargs["thread_ts"] == "1111.2222"

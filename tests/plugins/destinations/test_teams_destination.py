from unittest.mock import MagicMock, patch

import pytest

from holmes.core.issue import Issue, IssueStatus
from holmes.core.models import ToolCallResult
from holmes.core.tool_calling_llm import LLMResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.plugins.destinations.teams.plugin import TeamsDestination


@pytest.fixture
def teams_destination():
    return TeamsDestination(webhook_url="https://example.webhook.office.com/webhook/test")


@pytest.fixture
def sample_issue():
    return Issue(
        id="test-123",
        name="High CPU Usage Alert",
        source_type="prometheus",
        source_instance_id="prod-cluster",
        presentation_status=IssueStatus.OPEN,
        url="https://grafana.example.com/alert/123",
    )


@pytest.fixture
def sample_result():
    return LLMResult(
        result="The CPU usage is high due to a memory leak in the payment service.",
        tool_calls=[],
    )


class TestTeamsDestinationPayload:
    def test_build_payload_basic(self, teams_destination, sample_issue, sample_result):
        payload = teams_destination._build_payload(sample_issue, sample_result)

        assert payload["type"] == "message"
        assert len(payload["attachments"]) == 1

        attachment = payload["attachments"][0]
        assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"

        card = attachment["content"]
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.4"

        # Title block (show_status_in_title defaults to True)
        assert card["body"][0]["text"] == "High CPU Usage Alert - open"
        assert card["body"][0]["color"] == "attention"  # OPEN status

        # Result text
        assert "CPU usage is high" in card["body"][1]["text"]

        # Action link
        assert card["actions"][0]["url"] == "https://grafana.example.com/alert/123"

    def test_build_payload_resolved_issue(self, teams_destination, sample_result):
        resolved_issue = Issue(
            id="test-456",
            name="Resolved Alert",
            source_type="prometheus",
            source_instance_id="prod",
            presentation_status=IssueStatus.CLOSED,
        )
        payload = teams_destination._build_payload(resolved_issue, sample_result)
        card = payload["attachments"][0]["content"]

        assert card["body"][0]["color"] == "good"
        assert "actions" not in card  # No URL means no actions

    def test_build_payload_with_status_in_title(
        self, teams_destination, sample_result
    ):
        issue = Issue(
            id="test-789",
            name="Test Alert",
            source_type="prometheus",
            source_instance_id="prod",
            presentation_status=IssueStatus.OPEN,
            show_status_in_title=True,
        )
        payload = teams_destination._build_payload(issue, sample_result)
        card = payload["attachments"][0]["content"]
        assert "open" in card["body"][0]["text"].lower()

    def test_build_payload_with_tool_calls(self, teams_destination, sample_issue):
        tool_call = ToolCallResult(
            tool_call_id="call_1",
            tool_name="kubectl_get",
            description="kubectl get pods",
            result=StructuredToolResult(data="pod-1 Running", status=StructuredToolResultStatus.SUCCESS),
        )
        result = LLMResult(
            result="Found issue.",
            tool_calls=[tool_call],
        )
        payload = teams_destination._build_payload(sample_issue, result)
        card = payload["attachments"][0]["content"]

        # Should have a tools-used text block
        tools_block = [b for b in card["body"] if "Tools used" in b.get("text", "")]
        assert len(tools_block) == 1
        assert "kubectl get pods" in tools_block[0]["text"]


class TestTeamsDestinationSend:
    @patch("holmes.plugins.destinations.teams.plugin.requests.post")
    def test_send_issue_success(
        self, mock_post, teams_destination, sample_issue, sample_result
    ):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        teams_destination.send_issue(sample_issue, sample_result)

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://example.webhook.office.com/webhook/test"
        assert call_args[1]["headers"]["Content-Type"] == "application/json"

    @patch("holmes.plugins.destinations.teams.plugin.requests.post")
    def test_send_issue_failure_logs_error(
        self, mock_post, teams_destination, sample_issue, sample_result, caplog
    ):
        import requests as req

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = req.exceptions.HTTPError(
            "403 Forbidden"
        )
        mock_response.text = "Forbidden"
        mock_post.return_value = mock_response

        teams_destination.send_issue(sample_issue, sample_result)

        assert any("Failed to send issue to Microsoft Teams" in r.message for r in caplog.records)

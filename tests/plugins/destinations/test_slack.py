from unittest.mock import patch

from holmes.core.issue import Issue, IssueStatus
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.destinations.slack.plugin import SlackDestination


@patch("holmes.plugins.destinations.slack.plugin.WebClient")
def test_slack_alert_includes_investigation_cost(mock_web_client):
    mock_web_client.return_value.chat_postMessage.return_value = {"ts": "1"}
    destination = SlackDestination(token="token", channel="#alerts")
    issue = Issue(
        id="check-1",
        name="Health Check Failed: check-1",
        source_instance_id="dev",
        source_type="HealthCheck",
        presentation_status=IssueStatus.OPEN,
    )
    result = LLMResult(
        result="Deployment is unavailable.",
        tool_calls=[],
        total_cost=0.0425,
        total_tokens=15000,
        prompt_tokens=12000,
        completion_tokens=3000,
    )

    destination.send_issue(issue, result)

    blocks = mock_web_client.return_value.chat_postMessage.call_args.kwargs[
        "attachments"
    ][0]["blocks"]
    assert blocks[-1]["elements"][0]["text"] == (
        "*Investigation cost:* $0.0425 • *Tokens:* 12,000 input / 3,000 output"
    )

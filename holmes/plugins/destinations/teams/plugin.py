import logging

import requests  # type:ignore

from holmes.core.issue import Issue, IssueStatus
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.interfaces import DestinationPlugin


class TeamsDestination(DestinationPlugin):
    """Microsoft Teams destination plugin using incoming webhooks (Adaptive Cards)."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send_issue(self, issue: Issue, result: LLMResult) -> None:
        try:
            payload = self._build_payload(issue, result)
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            logging.info(f"Successfully sent issue to Microsoft Teams: {issue.name}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send issue to Microsoft Teams: {e}")
            if hasattr(e, "response") and e.response is not None:
                logging.error(f"Teams error response: {e.response.text}")

    def _build_payload(self, issue: Issue, result: LLMResult) -> dict:
        color = (
            "attention" if issue.presentation_status == IssueStatus.OPEN else "good"
        )

        if issue.presentation_status and issue.show_status_in_title:
            title = f"{issue.name} - {issue.presentation_status.value}"
        else:
            title = f"{issue.name}"

        body: list[dict] = [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": title,
                "color": color,
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": result.result or "",
                "wrap": True,
            },
        ]

        if issue.presentation_key_metadata:
            body.append(
                {
                    "type": "TextBlock",
                    "text": issue.presentation_key_metadata,
                    "isSubtle": True,
                    "wrap": True,
                    "size": "Small",
                }
            )

        if result.tool_calls:
            tools_text = "**Tools used:** " + ", ".join(
                tool.description for tool in result.tool_calls
            )
            body.append(
                {
                    "type": "TextBlock",
                    "text": tools_text,
                    "isSubtle": True,
                    "wrap": True,
                    "size": "Small",
                }
            )

        actions: list[dict] = []
        if issue.url:
            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": "View Issue",
                    "url": issue.url,
                }
            )

        card = {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": body,
        }
        if actions:
            card["actions"] = actions

        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": card,
                }
            ],
        }

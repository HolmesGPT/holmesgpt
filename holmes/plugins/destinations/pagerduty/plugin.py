import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests  # type:ignore

from holmes.core.issue import Issue
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.interfaces import DestinationPlugin

DEFAULT_PAGERDUTY_API_URL = "https://events.pagerduty.com/v2/enqueue"
PAGERDUTY_API_HOSTS = {"events.pagerduty.com", "events.eu.pagerduty.com"}


class PagerDutyDestination(DestinationPlugin):
    """PagerDuty destination plugin for sending alerts."""

    def __init__(
        self,
        integration_key: str,
        api_url: str = "https://events.pagerduty.com/v2/enqueue",
    ):
        """
        Initialize PagerDuty destination.

        Args:
            integration_key: PagerDuty Events API v2 integration key
            api_url: PagerDuty Events API endpoint (default: production URL)
        """
        self.integration_key = integration_key
        parsed_url = urlparse(api_url)
        if parsed_url.scheme.lower() != "https":
            raise ValueError("PagerDuty API URL must use HTTPS")
        if (
            parsed_url.hostname not in PAGERDUTY_API_HOSTS
            or parsed_url.username
            or parsed_url.password
            or parsed_url.port not in (None, 443)
        ):
            raise ValueError("PagerDuty API URL must use an approved PagerDuty hostname")
        self.api_url = api_url

    def send_issue(self, issue: Issue, result: LLMResult) -> bool:
        """
        Send an issue to PagerDuty as an incident.

        Args:
            issue: The issue to send
            result: The LLM analysis result
        """
        try:
            # Create PagerDuty event payload
            payload = self._create_event_payload(issue, result)

            # Send to PagerDuty
            response = requests.post(
                self.api_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
                allow_redirects=False,
            )

            response.raise_for_status()
            if not 200 <= response.status_code < 300:
                logging.error(
                    "PagerDuty API returned a non-success HTTP status: %s",
                    response.status_code,
                )
                return False

            result_data = response.json()
            if result_data.get("status") == "success":
                logging.info(
                    f"Successfully sent issue to PagerDuty. "
                    f"Dedup key: {result_data.get('dedup_key')}"
                )
                return True
            else:
                logging.error("PagerDuty API returned a non-success status")
                return False

        except requests.exceptions.RequestException as e:
            logging.error(
                "Failed to send issue to PagerDuty: %s", type(e).__name__
            )
            return False

    def _create_event_payload(self, issue: Issue, result: LLMResult) -> dict:
        """
        Create PagerDuty Events API v2 payload.

        Args:
            issue: The issue to convert
            result: The LLM analysis result

        Returns:
            PagerDuty event payload
        """
        # Extract summary and details
        cluster_name = issue.source_instance_id or "unknown"
        summary = f"Holmes Check Failed [{cluster_name}]: {issue.name}"

        # Build custom details
        custom_details: dict = {
            "holmes_analysis": result.result,
            "source_type": issue.source_type,
            "source_instance": issue.source_instance_id,
        }

        # Add raw issue data if available
        if issue.raw:
            check_details = (
                issue.raw if isinstance(issue.raw, dict) else {"data": issue.raw}
            )
            custom_details["check_details"] = check_details

        # Add tool calls if present
        if result.tool_calls:
            tools_used = [
                {
                    "tool": tool.tool_name,
                    "description": tool.description,
                }
                for tool in result.tool_calls
            ]
            custom_details["tools_used"] = tools_used

        # Create the payload
        payload = {
            "routing_key": self.integration_key,
            "event_action": "trigger",
            "dedup_key": f"holmes-check-{issue.id}",
            "payload": {
                "summary": summary,
                "severity": self._get_severity(issue),
                "source": cluster_name,
                "component": issue.source_type,
                "group": "health-checks",
                "class": "health-check-failure",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "custom_details": custom_details,
            },
        }

        # Add links if URL is available
        if issue.url:
            links = [{"href": issue.url, "text": "View in source system"}]
            payload["links"] = links  # type: ignore[assignment]

        return payload

    def _get_severity(self, issue: Issue) -> str:
        """
        Determine PagerDuty severity from issue.

        Args:
            issue: The issue to evaluate

        Returns:
            PagerDuty severity level (critical, error, warning, info)
        """
        # Check for severity hints in raw data
        if issue.raw:
            # Look for tags that might indicate severity
            tags = issue.raw.get("tags", [])
            if "critical" in tags:
                return "critical"
            elif "error" in tags:
                return "error"
            elif "warning" in tags:
                return "warning"

            # Check for explicit severity field
            if "severity" in issue.raw:
                severity = issue.raw["severity"].lower()
                if severity in ["critical", "error", "warning", "info"]:
                    return severity

        # Default to error for health check failures
        return "error"

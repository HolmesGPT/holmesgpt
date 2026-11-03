import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import urllib.parse

import requests  # type:ignore

from holmes.core.issue import Issue, IssueStatus
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.interfaces import DestinationPlugin

# Bump when the payload shape changes in a way consumers must detect.
WEBHOOK_SCHEMA_VERSION = 1
WEBHOOK_EVENT_TYPE = "holmes.check.result"

# The exact key set of every payload. All keys are always present (null when
# unknown) so consumers can read the body without existence checks.
WEBHOOK_PAYLOAD_KEYS = (
    "schema_version",
    "event_type",
    "timestamp",
    "issue_id",
    "check_name",
    "namespace",
    "result",
    "message",
    "rationale",
    "model_used",
    "duration_seconds",
    "status_url",
    "error",
)

# A health check alerts when it is failing, so an open issue means "fail".
_PRESENTATION_STATUS_TO_RESULT = {
    IssueStatus.OPEN: "fail",
    IssueStatus.CLOSED: "pass",
}


class WebhookDestination(DestinationPlugin):
    """Webhook destination plugin for sending alerts."""

    def __init__(
        self,
        method: str,
        webhook_url: str,
        headers: Optional[Dict[str, Any]] = None,
    ):      
        self.method = method
        self.webhook_url = webhook_url
        self.headers: Dict[str, Any] = dict(headers) if headers else {}

        if not any(k.lower() == "content-type" for k in self.headers):
            self.headers["Content-Type"] = "application/json"

    def send_issue(self, issue: Issue, result: LLMResult) -> None:
        """
        Send an issue to Webhook as an incident.

        Args:
            issue: The issue to send
            result: The LLM analysis result
        """
        try:
            parsed = urllib.parse.urlparse(self.webhook_url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                logging.error(
                    "WebhookDestination has invalid webhook URL"
                )
                raise Exception("WebhookDestination has invalid webhook URL")
                
      
            payload = self._create_event_payload(issue, result)

            response = requests.request(
                self.method.upper(),
                self.webhook_url,
                json=payload,
                headers=self.headers,
                timeout=30,
                allow_redirects=False,
            )
            
            # Do not follow redirects to potentially unsafe destinations.
            if response.is_redirect or response.is_permanent_redirect:
                raise requests.exceptions.RequestException(
                    "Webhook redirects are not allowed",
                    response=response,
                )

            response.raise_for_status()
            logging.info(f"Successfully sent issue to Webhook {parsed.scheme}://{parsed.hostname}")

        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send issue to Webhook: {e}")
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_details = e.response.json()
                    logging.error(f"Webhook error response: {error_details}")
                except json.JSONDecodeError:
                    logging.error(f"Webhook error response: {e.response.text}")
            raise
        
    def _create_event_payload(self, issue: Issue, result: LLMResult) -> dict:
        """
        Build the versioned webhook event payload.

        Check execution context is passed on ``issue.raw`` by the caller with
        the keys ``check_name``, ``namespace``, ``status``, ``message``,
        ``rationale``, ``model_used``, ``duration_seconds`` and ``error``.
        Legacy keys are read as fallbacks (``check`` for the name, ``result``
        for the message), and ``issue.url`` supplies the deep link. Values the
        caller does not provide are emitted as null rather than omitted, so the
        key set is identical on every event.

        Args:
            issue: The issue to convert
            result: The LLM analysis result

        Returns:
            Webhook event payload (see ``WEBHOOK_PAYLOAD_KEYS``)
        """
        raw = issue.raw or {}

        return {
            "schema_version": WEBHOOK_SCHEMA_VERSION,
            "event_type": WEBHOOK_EVENT_TYPE,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "issue_id": issue.id,
            "check_name": raw.get("check_name") or raw.get("check") or issue.name,
            "namespace": raw.get("namespace"),
            "result": self._result(issue, raw),
            "message": raw.get("message") or raw.get("result") or result.result,
            "rationale": raw.get("rationale"),
            "model_used": raw.get("model_used"),
            "duration_seconds": raw.get("duration_seconds"),
            "status_url": issue.url or raw.get("status_url"),
            "error": raw.get("error"),
        }

    @staticmethod
    def _result(issue: Issue, raw: dict) -> Optional[str]:
        """Check status: explicit ``raw['status']`` first, else inferred from
        the issue's presentation status."""
        status = raw.get("status")
        if status is not None:
            # Accept a plain string or a CheckStatus-style enum.
            return str(getattr(status, "value", status))
        if issue.presentation_status is not None:
            return _PRESENTATION_STATUS_TO_RESULT.get(issue.presentation_status)
        return None

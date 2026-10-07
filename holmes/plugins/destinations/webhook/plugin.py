import logging
from datetime import datetime, timezone
from typing import Dict, Optional

import requests  # type:ignore

from holmes.core.issue import Issue
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.interfaces import DestinationPlugin
from holmes.utils.header_rendering import render_header_templates

DEFAULT_TIMEOUT_SECONDS = 10
PAYLOAD_VERSION = 1


class WebhookDestination(DestinationPlugin):
    """Generic HTTP webhook destination for health check alerts.

    Sends the check result as a JSON body to any HTTP endpoint (WeCom,
    DingTalk, Feishu, custom incident buses, etc.).

    Header values support ``{{ env.VAR }}`` templating (same convention as
    modelList / toolset headers) so secrets can be injected from environment
    variables, typically sourced from a Kubernetes Secret.
    """

    def __init__(
        self,
        url: str,
        method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        if not url:
            raise ValueError("Webhook destination requires a 'url'")
        self.url = url
        self.method = (method or "POST").upper()
        self.headers = render_header_templates(
            dict(headers or {}), source_name="webhook destination"
        )
        self.timeout = timeout

    def send_issue(self, issue: Issue, result: LLMResult) -> None:
        """Send the check result to the webhook endpoint.

        Raises on connection errors and non-2xx responses so the caller can
        surface the failure in the check's notification status. No retries.
        """
        payload = self._create_payload(issue, result)
        response = requests.request(
            method=self.method,
            url=self.url,
            json=payload,
            headers={"Content-Type": "application/json", **self.headers},
            timeout=self.timeout,
        )
        if response.status_code >= 300:
            raise requests.exceptions.HTTPError(
                f"Webhook endpoint returned HTTP {response.status_code}: {response.text[:500]}",
                response=response,
            )
        logging.info(
            f"Webhook destination delivered alert for issue '{issue.name}' (HTTP {response.status_code})"
        )

    def _create_payload(self, issue: Issue, result: LLMResult) -> dict:
        """Build the JSON body from the check details stored on issue.raw."""
        check = issue.raw or {}
        name = check.get("check_name") or issue.name
        namespace: Optional[str] = None
        if "/" in name:
            # The operator passes check names as "<namespace>/<name>".
            namespace, name = name.split("/", 1)
        return {
            "version": PAYLOAD_VERSION,
            "name": name,
            "namespace": namespace,
            "status": check.get("status"),
            "message": check.get("message"),
            "query": check.get("query"),
            "rationale": check.get("rationale") or result.result,
            "model_used": check.get("model_used"),
            "duration": check.get("duration"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

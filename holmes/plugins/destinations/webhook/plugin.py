import copy
import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from holmes.core.issue import Issue
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.interfaces import DestinationPlugin


def _substitute(value: Any, context: dict) -> Any:
    """Recursively replace {{key}} placeholders in strings within a dict/list/str."""
    if isinstance(value, str):
        for k, v in context.items():
            value = re.sub(r"\{\{" + re.escape(k) + r"\}\}", lambda _m, _v=str(v): _v, value)
        return value
    elif isinstance(value, dict):
        return {k: _substitute(v, context) for k, v in value.items()}
    elif isinstance(value, list):
        return [_substitute(item, context) for item in value]
    return value


def _host(url: str) -> str:
    """Return just the scheme+host of a URL, omitting path (which may contain secrets)."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


class WebhookDestination(DestinationPlugin):
    def __init__(
        self,
        url: str,
        headers: Optional[dict] = None,
        payload_template: Optional[dict] = None,
    ):
        self.url = url
        self.headers = headers or {}
        self.payload_template = payload_template

    def send_issue(self, issue: Issue, result: LLMResult) -> None:
        status = (
            issue.presentation_status.value
            if issue.presentation_status
            else "unknown"
        )

        context = {
            "check_name": issue.name,
            "analysis": result.result or "",
            "status": status,
            "source_type": issue.source_type,
            "url": issue.url or "",
        }

        if self.payload_template is not None:
            payload = _substitute(copy.deepcopy(self.payload_template), context)
        else:
            payload = {
                "check_name": issue.name,
                "status": status,
                "analysis": result.result or "",
                "source_type": issue.source_type,
                "url": issue.url or "",
            }

        http_headers = {"Content-Type": "application/json"}
        http_headers.update(self.headers)

        try:
            response = requests.post(self.url, json=payload, headers=http_headers, timeout=30)
            response.raise_for_status()
            logging.info(f"Webhook notification sent to {_host(self.url)} (status {response.status_code})")
        except requests.exceptions.HTTPError as e:
            logging.error(
                f"Webhook POST to {_host(self.url)} failed with HTTP {e.response.status_code}: {e.response.text}"
            )
            raise
        except requests.exceptions.RequestException as e:
            logging.error(f"Webhook POST to {_host(self.url)} failed: {e}")
            raise

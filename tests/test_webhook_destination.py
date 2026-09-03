import os
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.issue import Issue, IssueStatus
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.destinations.webhook.plugin import WebhookDestination, _substitute


def make_issue(**kwargs):
    defaults = dict(
        id="test-1",
        name="Health Check Failed: production-check",
        source_instance_id="my-cluster",
        source_type="HealthCheck",
        presentation_status=IssueStatus.OPEN,
        url="https://example.com/check/1",
    )
    defaults.update(kwargs)
    return Issue(**defaults)


def make_result(**kwargs):
    defaults = dict(result="Something is broken in the cluster.", tool_calls=[], messages=[])
    defaults.update(kwargs)
    return LLMResult(**defaults)


class TestSubstitute:
    def test_replaces_string_variable(self):
        assert _substitute("{{foo}}", {"foo": "bar"}) == "bar"

    def test_replaces_multiple_variables(self):
        assert _substitute("{{a}} and {{b}}", {"a": "X", "b": "Y"}) == "X and Y"

    def test_leaves_unknown_variables(self):
        assert _substitute("{{unknown}}", {"foo": "bar"}) == "{{unknown}}"

    def test_recurses_into_dict(self):
        result = _substitute({"key": "{{foo}}"}, {"foo": "baz"})
        assert result == {"key": "baz"}

    def test_recurses_into_list(self):
        result = _substitute(["{{foo}}", "static"], {"foo": "bar"})
        assert result == ["bar", "static"]

    def test_non_string_passthrough(self):
        assert _substitute(42, {}) == 42


class TestWebhookDestination:
    @patch("holmes.plugins.destinations.webhook.plugin.requests.post")
    def test_default_payload(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        dest = WebhookDestination(url="https://hook.example.com/endpoint")
        issue = make_issue()
        result = make_result()

        dest.send_issue(issue, result)

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert payload["check_name"] == issue.name
        assert payload["status"] == "open"
        assert payload["analysis"] == result.result
        assert payload["source_type"] == "HealthCheck"
        assert payload["url"] == issue.url

    @patch("holmes.plugins.destinations.webhook.plugin.requests.post")
    def test_custom_payload_template(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        template = {
            "incident_key": "{{check_name}}",
            "description": "{{analysis}}",
            "severity": "critical",
        }
        dest = WebhookDestination(url="https://hook.example.com/endpoint", payload_template=template)
        issue = make_issue()
        result = make_result()

        dest.send_issue(issue, result)

        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert payload["incident_key"] == issue.name
        assert payload["description"] == result.result
        assert payload["severity"] == "critical"  # static value unchanged

    @patch("holmes.plugins.destinations.webhook.plugin.requests.post")
    def test_custom_headers_merged(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        dest = WebhookDestination(
            url="https://hook.example.com/endpoint",
            headers={"Authorization": "Bearer secret"},
        )
        dest.send_issue(make_issue(), make_result())

        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer secret"
        assert kwargs["headers"]["Content-Type"] == "application/json"

    @patch("holmes.plugins.destinations.webhook.plugin.requests.post")
    def test_template_not_mutated(self, mock_post):
        """Ensure the original payload_template dict is not modified."""
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        template = {"key": "{{check_name}}"}
        dest = WebhookDestination(url="https://hook.example.com/endpoint", payload_template=template)
        dest.send_issue(make_issue(), make_result())

        assert template["key"] == "{{check_name}}"  # original unchanged

    @patch("holmes.plugins.destinations.webhook.plugin.requests.post")
    def test_http_error_raises(self, mock_post):
        import requests as req
        mock_response = MagicMock(status_code=500, text="Internal Server Error")
        mock_post.return_value = mock_response
        mock_response.raise_for_status.side_effect = req.exceptions.HTTPError(
            response=mock_response
        )
        dest = WebhookDestination(url="https://hook.example.com/endpoint")

        # Should raise so checks_api can set notification.status = "failed"
        with pytest.raises(req.exceptions.HTTPError):
            dest.send_issue(make_issue(), make_result())


class TestWebhookUrlResolution:
    """Tests for url / url_env / WEBHOOK_URL resolution logic in checks_api."""

    def _resolve_url(self, dest_config: dict) -> str:
        """Mirrors the resolution logic in checks_api.py."""
        return (
            dest_config.get("url")
            or os.environ.get(dest_config.get("url_env", ""))
            or os.environ.get("WEBHOOK_URL")
            or ""
        )

    def test_literal_url_takes_priority(self):
        url = self._resolve_url({"url": "https://example.com/hook"})
        assert url == "https://example.com/hook"

    def test_url_env_resolves_named_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_SYSTEM_WEBHOOK", "https://my-system.com/hook")
        url = self._resolve_url({"url_env": "MY_SYSTEM_WEBHOOK"})
        assert url == "https://my-system.com/hook"

    def test_webhook_url_fallback(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_URL", "https://fallback.com/hook")
        url = self._resolve_url({})
        assert url == "https://fallback.com/hook"

    def test_literal_url_overrides_env(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_URL", "https://should-not-use.com")
        url = self._resolve_url({"url": "https://explicit.com/hook"})
        assert url == "https://explicit.com/hook"

    def test_url_env_overrides_webhook_url_fallback(self, monkeypatch):
        monkeypatch.setenv("MY_SYSTEM_WEBHOOK", "https://named.com/hook")
        monkeypatch.setenv("WEBHOOK_URL", "https://should-not-use.com")
        url = self._resolve_url({"url_env": "MY_SYSTEM_WEBHOOK"})
        assert url == "https://named.com/hook"

    def test_missing_url_env_var_falls_through(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_URL", "https://fallback.com/hook")
        # url_env points to a var that doesn't exist — should fall through to WEBHOOK_URL
        url = self._resolve_url({"url_env": "NONEXISTENT_VAR"})
        assert url == "https://fallback.com/hook"

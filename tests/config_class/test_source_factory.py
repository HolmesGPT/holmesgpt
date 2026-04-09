from typing import ForwardRef, get_args
from unittest.mock import patch

import pytest

from holmes.config import SourceFactory, SupportedTicketSources, TicketSource


class TestTicketSourceModelRebuild:
    def test_ticket_source_resolves_forward_references(self) -> None:
        """TicketSource uses Union['JiraServiceManagementSource', 'PagerDutySource']
        as a forward reference. Without model_rebuild(), Pydantic v2 raises
        PydanticUserError at instantiation time."""
        from holmes.plugins.sources.jira import JiraServiceManagementSource  # noqa: F401
        from holmes.plugins.sources.pagerduty import PagerDutySource  # noqa: F401

        TicketSource.model_rebuild()
        annotation = TicketSource.model_fields["source"].annotation
        for arg in get_args(annotation):
            assert not isinstance(arg, ForwardRef), f"Unresolved forward ref: {arg}"


class TestSourceFactoryModelForwarding:
    @patch("holmes.core.llm.MODEL_LIST_FILE_LOCATION", "")
    def test_create_source_forwards_model_to_pagerduty_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When --model is passed to 'investigate ticket --source pagerduty',
        the model must be forwarded to Config so that LLMModelRegistry registers
        it instead of falling back to gpt-4.1."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("MODEL", raising=False)

        test_model = "bedrock/anthropic.claude-sonnet-4-20250514-v1:0"
        ticket_source = SourceFactory.create_source(
            source=SupportedTicketSources.PAGERDUTY,
            config_file=None,
            ticket_url=None,
            ticket_username="test@example.com",
            ticket_api_key="test-api-key",
            ticket_id="Q1234567",
            model=test_model,
        )

        assert ticket_source.config.model == test_model
        assert test_model in ticket_source.config.get_models_list()

    @patch("holmes.core.llm.MODEL_LIST_FILE_LOCATION", "")
    def test_create_source_forwards_model_to_jira_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same forwarding must work for the jira-service-management source."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("MODEL", raising=False)

        test_model = "bedrock/anthropic.claude-sonnet-4-20250514-v1:0"
        ticket_source = SourceFactory.create_source(
            source=SupportedTicketSources.JIRA_SERVICE_MANAGEMENT,
            config_file=None,
            ticket_url="https://test.atlassian.net",
            ticket_username="test@example.com",
            ticket_api_key="test-api-key",
            ticket_id="KAN-1",
            model=test_model,
        )

        assert ticket_source.config.model == test_model
        assert test_model in ticket_source.config.get_models_list()

    @patch("holmes.core.llm.MODEL_LIST_FILE_LOCATION", "")
    def test_create_source_without_model_defaults_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When model is omitted, backward-compatible behavior is preserved."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("MODEL", raising=False)

        ticket_source = SourceFactory.create_source(
            source=SupportedTicketSources.PAGERDUTY,
            config_file=None,
            ticket_url=None,
            ticket_username="test@example.com",
            ticket_api_key="test-api-key",
            ticket_id="Q1234567",
        )

        assert ticket_source.config.model is None

    @patch("holmes.core.llm.MODEL_LIST_FILE_LOCATION", "")
    def test_create_source_model_overrides_openai_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When OPENAI_API_KEY is set but --model is also provided, the explicit
        model must take priority over the gpt-4.1 fallback."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy")
        monkeypatch.delenv("MODEL", raising=False)

        test_model = "anthropic/claude-sonnet-4-6"
        ticket_source = SourceFactory.create_source(
            source=SupportedTicketSources.PAGERDUTY,
            config_file=None,
            ticket_url=None,
            ticket_username="test@example.com",
            ticket_api_key="test-api-key",
            ticket_id="Q1234567",
            model=test_model,
        )

        assert ticket_source.config.model == test_model
        assert "gpt-4.1" not in ticket_source.config.get_models_list()

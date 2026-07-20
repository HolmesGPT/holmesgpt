"""Tests for the Freshservice toolset."""

import json
import os
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.freshservice.freshservice import (
    OBJECT_REGISTRY,
    FreshserviceConfig,
    FreshserviceToolset,
)

API_URL = "https://example.freshservice.com"

# Data patterns match real Freshservice API v2 responses
SAMPLE_TICKET = {
    "id": 21,
    "subject": "payment-db-01: PostgreSQL connection limit reached - new connections rejected",
    "status": 2,
    "priority": 4,
    "requester_id": 41000356458,
    "group_id": None,
    "created_at": "2026-07-14T18:59:21Z",
    "updated_at": "2026-07-15T14:00:58Z",
}

SAMPLE_AGENT = {
    "id": 41000356457,
    "email": "ops@example.com",
    "first_name": "Ops",
    "active": True,
}


@pytest.fixture
def toolset():
    ts = FreshserviceToolset()
    ts.config = FreshserviceConfig(api_url=API_URL, api_key="test-key")
    return ts


def _tool(toolset, name):
    return next(t for t in toolset.tools if t.name == name)


def _request_params(rsps, index=0):
    return parse_qs(urlparse(rsps.calls[index].request.url).query)


class TestFreshserviceConfig:
    def test_minimal_config(self):
        config = FreshserviceConfig(api_url=API_URL, api_key="abc")
        assert config.api_url == API_URL
        assert config.api_key == "abc"
        assert config.default_page_size == 30
        assert config.timeout_seconds == 30
        assert config.health_check_object == "tickets"

    def test_missing_api_key_rejected(self):
        with pytest.raises(ValueError):
            FreshserviceConfig(api_url=API_URL)

    def test_missing_api_url_rejected(self):
        with pytest.raises(ValueError):
            FreshserviceConfig(api_key="abc")


READ_TOOL_NAMES = {
    "freshservice_list_object_types",
    "freshservice_list_objects",
    "freshservice_get_object",
    "freshservice_search_objects",
    "freshservice_list_related_objects",
}

WRITE_TOOL_NAMES = {
    "freshservice_create_object",
    "freshservice_update_object",
    "freshservice_delete_object",
    "freshservice_create_related_object",
    "freshservice_update_related_object",
    "freshservice_delete_related_object",
}


class TestFreshserviceToolsetInit:
    def test_toolset_has_expected_tools(self, toolset):
        names = {t.name for t in toolset.tools}
        assert names == READ_TOOL_NAMES | WRITE_TOOL_NAMES

    def test_write_tools_removed_by_default(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": []},
                status=200,
            )
            ok, _ = toolset.prerequisites_callable(
                {"api_url": API_URL, "api_key": "test-key"}
            )
            assert ok is True
        assert {t.name for t in toolset.tools} == READ_TOOL_NAMES

    def test_write_tools_exposed_when_enabled(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": []},
                status=200,
            )
            ok, _ = toolset.prerequisites_callable(
                {
                    "api_url": API_URL,
                    "api_key": "test-key",
                    "enable_write_tools": True,
                }
            )
            assert ok is True
        assert {t.name for t in toolset.tools} == READ_TOOL_NAMES | WRITE_TOOL_NAMES
        assert "Writing data" in toolset.llm_instructions

    def test_instructions_loaded(self, toolset):
        assert toolset.llm_instructions
        assert "freshservice_search_objects" in toolset.llm_instructions

    def test_prerequisites_missing_config(self, toolset):
        ok, msg = toolset.prerequisites_callable({})
        assert ok is False
        assert "missing" in msg.lower()

    def test_prerequisites_invalid_config(self, toolset):
        ok, msg = toolset.prerequisites_callable({"api_url": API_URL})
        assert ok is False
        assert "Failed to validate" in msg


class TestHealthCheck:
    def test_health_check_success(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": [SAMPLE_TICKET]},
                status=200,
            )
            ok, msg = toolset.prerequisites_callable(
                {"api_url": API_URL, "api_key": "test-key"}
            )
            assert ok is True
            assert API_URL in msg

    def test_health_check_sends_basic_auth(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": []},
                status=200,
            )
            ok, _ = toolset.prerequisites_callable(
                {"api_url": API_URL, "api_key": "test-key"}
            )
            assert ok is True
            auth_header = rsps.calls[0].request.headers["Authorization"]
            # base64("test-key:X")
            assert auth_header == "Basic dGVzdC1rZXk6WA=="

    def test_health_check_custom_object(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/agents",
                json={"agents": [SAMPLE_AGENT]},
                status=200,
            )
            ok, msg = toolset.prerequisites_callable(
                {
                    "api_url": API_URL,
                    "api_key": "test-key",
                    "health_check_object": "agents",
                }
            )
            assert ok is True
            assert "agents" in msg

    def test_health_check_invalid_object_type(self, toolset):
        ok, msg = toolset.prerequisites_callable(
            {
                "api_url": API_URL,
                "api_key": "test-key",
                "health_check_object": "not_a_real_object",
            }
        )
        assert ok is False
        assert "Invalid health_check_object" in msg

    def test_health_check_authentication_failure(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"code": "invalid_credentials"},
                status=401,
            )
            ok, msg = toolset.prerequisites_callable(
                {"api_url": API_URL, "api_key": "bad-key"}
            )
            assert ok is False
            assert "401" in msg
            assert "API key" in msg

    def test_health_check_permission_denied(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={
                    "code": "access_denied",
                    "message": "You are not authorized to perform this action.",
                },
                status=403,
            )
            ok, msg = toolset.prerequisites_callable(
                {"api_url": API_URL, "api_key": "test-key"}
            )
            assert ok is False
            assert "403" in msg
            assert "health_check_object" in msg

    def test_health_check_connection_error(self, toolset):
        with responses.RequestsMock():
            # No registered response -> ConnectionError
            ok, msg = toolset.prerequisites_callable(
                {"api_url": "https://nonexistent.invalid", "api_key": "test-key"}
            )
            assert ok is False
            assert "connect" in msg.lower()


class TestListObjectTypes:
    def test_returns_registry(self, toolset):
        tool = _tool(toolset, "freshservice_list_object_types")
        result = tool._invoke({}, MagicMock())
        assert result.status == StructuredToolResultStatus.SUCCESS
        object_types = result.data["object_types"]
        assert set(object_types.keys()) == set(OBJECT_REGISTRY.keys())
        assert object_types["tickets"]["searchable"] is True
        assert "conversations" in object_types["tickets"]["sub_resources"]
        assert object_types["agent_groups"]["searchable"] is False


class TestListObjects:
    def test_list_tickets(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": [SAMPLE_TICKET]},
                status=200,
            )
            tool = _tool(toolset, "freshservice_list_objects")
            result = tool._invoke({"object_type": "tickets"}, MagicMock())
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data["tickets"][0]["id"] == 21
            params = _request_params(rsps)
            assert params["per_page"] == ["30"]

    def test_pagination_params_and_link_header(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": [SAMPLE_TICKET]},
                status=200,
                headers={
                    "Link": f'<{API_URL}/api/v2/tickets?per_page=1&page=3>; rel="next"'
                },
            )
            tool = _tool(toolset, "freshservice_list_objects")
            result = tool._invoke(
                {"object_type": "tickets", "page": 2, "per_page": 1}, MagicMock()
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data["_pagination"]["has_more_pages"] is True
            params = _request_params(rsps)
            assert params["page"] == ["2"]
            assert params["per_page"] == ["1"]

    def test_per_page_capped_at_max(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": []},
                status=200,
            )
            tool = _tool(toolset, "freshservice_list_objects")
            tool._invoke({"object_type": "tickets", "per_page": 5000}, MagicMock())
            params = _request_params(rsps)
            assert params["per_page"] == ["100"]

    def test_unknown_object_type(self, toolset):
        tool = _tool(toolset, "freshservice_list_objects")
        result = tool._invoke({"object_type": "wombats"}, MagicMock())
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Unknown object_type 'wombats'" in result.error
        assert "tickets" in result.error  # error lists valid types

    def test_updated_since_rfc3339_passthrough(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": []},
                status=200,
            )
            tool = _tool(toolset, "freshservice_list_objects")
            result = tool._invoke(
                {"object_type": "tickets", "updated_since": "2026-07-01T00:00:00Z"},
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            params = _request_params(rsps)
            assert params["updated_since"] == ["2026-07-01T00:00:00Z"]

    def test_updated_since_relative_seconds(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": []},
                status=200,
            )
            tool = _tool(toolset, "freshservice_list_objects")
            result = tool._invoke(
                {"object_type": "tickets", "updated_since": "-3600"}, MagicMock()
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            params = _request_params(rsps)
            # Converted to an RFC3339 timestamp, not passed through as-is
            assert params["updated_since"][0].endswith("Z")
            assert params["updated_since"][0] != "-3600"

    def test_updated_since_invalid_value(self, toolset):
        tool = _tool(toolset, "freshservice_list_objects")
        result = tool._invoke(
            {"object_type": "tickets", "updated_since": "not-a-date !!!"}, MagicMock()
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid updated_since" in result.error

    def test_updated_since_unsupported_object_type(self, toolset):
        tool = _tool(toolset, "freshservice_list_objects")
        result = tool._invoke(
            {"object_type": "agents", "updated_since": "2026-07-01T00:00:00Z"},
            MagicMock(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "does not support the updated_since parameter" in result.error

    def test_additional_query_params(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/solutions/folders",
                json={"folders": [{"id": 41000003831, "name": "FAQ"}]},
                status=200,
            )
            tool = _tool(toolset, "freshservice_list_objects")
            result = tool._invoke(
                {
                    "object_type": "solution_folders",
                    "additional_query_params": "category_id=41000003502",
                },
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            params = _request_params(rsps)
            assert params["category_id"] == ["41000003502"]

    def test_additional_query_params_invalid(self, toolset):
        tool = _tool(toolset, "freshservice_list_objects")
        result = tool._invoke(
            {"object_type": "tickets", "additional_query_params": "&&&"}, MagicMock()
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid additional_query_params" in result.error

    def test_jq_projection(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": [SAMPLE_TICKET]},
                status=200,
            )
            tool = _tool(toolset, "freshservice_list_objects")
            result = tool._invoke(
                {"object_type": "tickets", "jq": ".tickets[] | {id, subject}"},
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data == {
                "id": 21,
                "subject": SAMPLE_TICKET["subject"],
            }

    def test_permission_denied_includes_api_error(self, toolset):
        # Real error returned by Freshservice for plan-gated endpoints
        api_error = {
            "code": "require_feature",
            "message": "The Cmdb feature(s) is/are not supported in your plan. Please upgrade your account to use it.",
        }
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/assets",
                json=api_error,
                status=403,
            )
            tool = _tool(toolset, "freshservice_list_objects")
            result = tool._invoke({"object_type": "assets"}, MagicMock())
            assert result.status == StructuredToolResultStatus.ERROR
            assert "403" in result.error
            assert "require_feature" in result.error
            assert "GET /api/v2/assets" in result.error

    def test_rate_limit_includes_retry_after(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets",
                json={"message": "You have exceeded the limit of requests per hour"},
                status=429,
                headers={"Retry-After": "37"},
            )
            tool = _tool(toolset, "freshservice_list_objects")
            result = tool._invoke({"object_type": "tickets"}, MagicMock())
            assert result.status == StructuredToolResultStatus.ERROR
            assert "Rate limit" in result.error
            assert "37" in result.error

    def test_not_configured(self):
        ts = FreshserviceToolset()
        tool = _tool(ts, "freshservice_list_objects")
        result = tool._invoke({"object_type": "tickets"}, MagicMock())
        assert result.status == StructuredToolResultStatus.ERROR
        assert "config" in result.error.lower()


class TestGetObject:
    def test_get_ticket_with_include(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets/21",
                json={
                    "ticket": {**SAMPLE_TICKET, "stats": {"agent_responded_at": None}}
                },
                status=200,
            )
            tool = _tool(toolset, "freshservice_get_object")
            result = tool._invoke(
                {"object_type": "tickets", "object_id": 21, "include": "stats"},
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data["ticket"]["id"] == 21
            params = _request_params(rsps)
            assert params["include"] == ["stats"]

    def test_get_not_found(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets/999999",
                body="",
                status=404,
            )
            tool = _tool(toolset, "freshservice_get_object")
            result = tool._invoke(
                {"object_type": "tickets", "object_id": 999999}, MagicMock()
            )
            assert result.status == StructuredToolResultStatus.ERROR
            assert "404" in result.error
            assert "GET /api/v2/tickets/999999" in result.error


class TestSearchObjects:
    def test_search_tickets_uses_filter_endpoint(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets/filter",
                json={"tickets": [SAMPLE_TICKET], "total": 1},
                status=200,
            )
            tool = _tool(toolset, "freshservice_search_objects")
            result = tool._invoke(
                {"object_type": "tickets", "query": "status:2 AND priority:4"},
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data["tickets"][0]["id"] == 21
            params = _request_params(rsps)
            # Query is wrapped in double quotes as required by the API
            assert params["query"] == ['"status:2 AND priority:4"']

    def test_search_query_already_quoted(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets/filter",
                json={"tickets": []},
                status=200,
            )
            tool = _tool(toolset, "freshservice_search_objects")
            tool._invoke({"object_type": "tickets", "query": '"status:2"'}, MagicMock())
            params = _request_params(rsps)
            assert params["query"] == ['"status:2"']

    def test_search_requesters_uses_query_param(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/requesters",
                json={"requesters": []},
                status=200,
            )
            tool = _tool(toolset, "freshservice_search_objects")
            result = tool._invoke(
                {
                    "object_type": "requesters",
                    "query": "primary_email:'jane@example.com'",
                },
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            params = _request_params(rsps)
            assert params["query"] == ["\"primary_email:'jane@example.com'\""]

    def test_search_assets_uses_filter_param(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/assets",
                json={"assets": []},
                status=200,
            )
            tool = _tool(toolset, "freshservice_search_objects")
            result = tool._invoke(
                {"object_type": "assets", "query": "asset_state:'IN USE'"},
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            params = _request_params(rsps)
            assert params["filter"] == ["\"asset_state:'IN USE'\""]

    def test_search_unsupported_object_type(self, toolset):
        tool = _tool(toolset, "freshservice_search_objects")
        result = tool._invoke(
            {"object_type": "products", "query": "name:'x'"}, MagicMock()
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "does not support server-side search" in result.error


class TestListRelatedObjects:
    def test_ticket_conversations(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/api/v2/tickets/21/conversations",
                json={"conversations": [], "meta": {"count": 0, "has_more": False}},
                status=200,
            )
            tool = _tool(toolset, "freshservice_list_related_objects")
            result = tool._invoke(
                {
                    "object_type": "tickets",
                    "object_id": 21,
                    "relation": "conversations",
                },
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS
            assert result.data["conversations"] == []

    def test_invalid_relation(self, toolset):
        tool = _tool(toolset, "freshservice_list_related_objects")
        result = tool._invoke(
            {"object_type": "tickets", "object_id": 21, "relation": "licenses"},
            MagicMock(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "has no 'licenses' sub-resource" in result.error
        assert "conversations" in result.error  # lists available relations

    def test_object_type_without_sub_resources(self, toolset):
        tool = _tool(toolset, "freshservice_list_related_objects")
        result = tool._invoke(
            {"object_type": "agents", "object_id": 1, "relation": "tasks"},
            MagicMock(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "none" in result.error


class TestWriteTools:
    def test_create_ticket(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/v2/tickets",
                json={"ticket": {**SAMPLE_TICKET, "id": 42}},
                status=201,
            )
            tool = _tool(toolset, "freshservice_create_object")
            result = tool._invoke(
                {
                    "object_type": "tickets",
                    "object_data": '{"subject": "DB errors", "description": "x", "email": "a@b.com", "status": 2, "priority": 3}',
                },
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS, result.error
            assert result.data["http_status"] == 201
            assert result.data["ticket"]["id"] == 42
            sent = json.loads(rsps.calls[0].request.body)
            assert sent["subject"] == "DB errors"
            assert sent["priority"] == 3

    def test_update_ticket(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.PUT,
                f"{API_URL}/api/v2/tickets/21",
                json={"ticket": {**SAMPLE_TICKET, "status": 4}},
                status=200,
            )
            tool = _tool(toolset, "freshservice_update_object")
            result = tool._invoke(
                {
                    "object_type": "tickets",
                    "object_id": 21,
                    "object_data": '{"status": 4}',
                },
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS, result.error
            assert result.data["ticket"]["status"] == 4

    def test_delete_ticket_returns_204(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.DELETE,
                f"{API_URL}/api/v2/tickets/21",
                body="",
                status=204,
            )
            tool = _tool(toolset, "freshservice_delete_object")
            result = tool._invoke(
                {"object_type": "tickets", "object_id": 21}, MagicMock()
            )
            assert result.status == StructuredToolResultStatus.SUCCESS, result.error
            assert result.data["http_status"] == 204

    def test_create_ticket_note(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/v2/tickets/21/notes",
                json={"conversation": {"id": 7, "body": "<div>found it</div>"}},
                status=201,
            )
            tool = _tool(toolset, "freshservice_create_related_object")
            result = tool._invoke(
                {
                    "object_type": "tickets",
                    "object_id": 21,
                    "relation": "notes",
                    "object_data": '{"body": "found it", "private": true}',
                },
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS, result.error
            assert result.data["conversation"]["id"] == 7

    def test_update_related_task(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.PUT,
                f"{API_URL}/api/v2/tickets/21/tasks/3",
                json={"task": {"id": 3, "status": 2}},
                status=200,
            )
            tool = _tool(toolset, "freshservice_update_related_object")
            result = tool._invoke(
                {
                    "object_type": "tickets",
                    "object_id": 21,
                    "relation": "tasks",
                    "related_object_id": 3,
                    "object_data": '{"status": 2}',
                },
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS, result.error

    def test_delete_related_task(self, toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.DELETE,
                f"{API_URL}/api/v2/tickets/21/tasks/3",
                body="",
                status=204,
            )
            tool = _tool(toolset, "freshservice_delete_related_object")
            result = tool._invoke(
                {
                    "object_type": "tickets",
                    "object_id": 21,
                    "relation": "tasks",
                    "related_object_id": 3,
                },
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.SUCCESS, result.error

    def test_readonly_object_type_rejected(self, toolset):
        tool = _tool(toolset, "freshservice_create_object")
        result = tool._invoke(
            {"object_type": "roles", "object_data": '{"name": "x"}'}, MagicMock()
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "read-only" in result.error

    def test_non_writable_relation_rejected(self, toolset):
        tool = _tool(toolset, "freshservice_create_related_object")
        result = tool._invoke(
            {
                "object_type": "tickets",
                "object_id": 21,
                "relation": "licenses",
                "object_data": "{}",
            },
            MagicMock(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "no writable 'licenses' sub-resource" in result.error

    def test_invalid_json_rejected(self, toolset):
        tool = _tool(toolset, "freshservice_create_object")
        result = tool._invoke(
            {"object_type": "tickets", "object_data": "{not json"}, MagicMock()
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not valid JSON" in result.error

    def test_non_dict_json_rejected(self, toolset):
        tool = _tool(toolset, "freshservice_create_object")
        result = tool._invoke(
            {"object_type": "tickets", "object_data": "[1, 2]"}, MagicMock()
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "must be a JSON object" in result.error

    def test_validation_error_surfaced(self, toolset):
        api_error = {
            "description": "Validation failed",
            "errors": [
                {
                    "field": "priority",
                    "message": "It should be one of these values: '1,2,3,4'",
                    "code": "invalid_value",
                }
            ],
        }
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/api/v2/tickets",
                json=api_error,
                status=400,
            )
            tool = _tool(toolset, "freshservice_create_object")
            result = tool._invoke(
                {"object_type": "tickets", "object_data": '{"priority": 9}'},
                MagicMock(),
            )
            assert result.status == StructuredToolResultStatus.ERROR
            assert "400" in result.error
            assert "invalid_value" in result.error
            assert "POST /api/v2/tickets" in result.error

    def test_writes_require_approval_by_default(self, toolset):
        tool = _tool(toolset, "freshservice_create_object")
        requirement = tool.requires_approval(
            {"object_type": "tickets", "object_data": "{}"}, MagicMock()
        )
        assert requirement is not None
        assert requirement.needs_approval is True

    def test_approval_can_be_disabled(self, toolset):
        toolset.config = FreshserviceConfig(
            api_url=API_URL,
            api_key="test-key",
            enable_write_tools=True,
            require_approval_for_writes=False,
        )
        tool = _tool(toolset, "freshservice_create_object")
        requirement = tool.requires_approval(
            {"object_type": "tickets", "object_data": "{}"}, MagicMock()
        )
        assert requirement is None

    def test_read_tools_never_require_approval(self, toolset):
        tool = _tool(toolset, "freshservice_list_objects")
        assert tool.requires_approval({"object_type": "tickets"}, MagicMock()) is None


class TestOneLiners:
    def test_one_liners(self, toolset):
        cases = [
            ("freshservice_list_object_types", {}),
            ("freshservice_list_objects", {"object_type": "tickets"}),
            ("freshservice_get_object", {"object_type": "tickets", "object_id": 21}),
            (
                "freshservice_search_objects",
                {"object_type": "tickets", "query": "status:2"},
            ),
            (
                "freshservice_list_related_objects",
                {"object_type": "tickets", "object_id": 21, "relation": "tasks"},
            ),
        ]
        for name, params in cases:
            one_liner = _tool(toolset, name).get_parameterized_one_liner(params)
            assert one_liner.startswith("Freshservice: ")


# ---------------------------------------------------------------------------
# Live tests (only run when FRESHSERVICE_URL/FRESHWORK_URL and the API key
# are set). These exercise a real Freshservice instance.
# ---------------------------------------------------------------------------

LIVE_URL = os.environ.get("FRESHSERVICE_URL") or os.environ.get("FRESHWORK_URL")
LIVE_API_KEY = os.environ.get("FRESHSERVICE_API_KEY") or os.environ.get(
    "FRESHWORK_API_KEY"
)


@pytest.mark.skipif(
    not (LIVE_URL and LIVE_API_KEY),
    reason="FRESHSERVICE_URL/FRESHSERVICE_API_KEY (or FRESHWORK_*) env vars not set",
)
class TestLiveFreshservice:
    """Live tests against a real Freshservice instance."""

    @pytest.fixture
    def live_toolset(self):
        ts = FreshserviceToolset()
        ok, msg = ts.prerequisites_callable(
            {"api_url": LIVE_URL, "api_key": LIVE_API_KEY}
        )
        assert ok, f"Health check failed: {msg}"
        return ts

    def test_health_check(self, live_toolset):
        # prerequisites_callable already ran in the fixture; verify config was stored
        assert isinstance(live_toolset.config, FreshserviceConfig)

    def test_list_tickets(self, live_toolset):
        tool = _tool(live_toolset, "freshservice_list_objects")
        result = tool._invoke({"object_type": "tickets", "per_page": 2}, MagicMock())
        assert result.status == StructuredToolResultStatus.SUCCESS, result.error
        tickets = result.data["tickets"]
        assert isinstance(tickets, list)
        if tickets:
            assert "id" in tickets[0]
            assert "subject" in tickets[0]
            assert "status" in tickets[0]

    def test_get_ticket_roundtrip(self, live_toolset):
        list_tool = _tool(live_toolset, "freshservice_list_objects")
        listing = list_tool._invoke(
            {"object_type": "tickets", "per_page": 1}, MagicMock()
        )
        assert listing.status == StructuredToolResultStatus.SUCCESS, listing.error
        if not listing.data["tickets"]:
            pytest.skip("No tickets in the live instance")
        ticket_id = listing.data["tickets"][0]["id"]

        get_tool = _tool(live_toolset, "freshservice_get_object")
        result = get_tool._invoke(
            {"object_type": "tickets", "object_id": ticket_id, "include": "stats"},
            MagicMock(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS, result.error
        assert result.data["ticket"]["id"] == ticket_id
        assert "stats" in result.data["ticket"]

    def test_search_tickets(self, live_toolset):
        tool = _tool(live_toolset, "freshservice_search_objects")
        result = tool._invoke(
            {"object_type": "tickets", "query": "status:2 OR status:5"}, MagicMock()
        )
        assert result.status == StructuredToolResultStatus.SUCCESS, result.error
        assert "tickets" in result.data

    def test_list_agents(self, live_toolset):
        tool = _tool(live_toolset, "freshservice_list_objects")
        result = tool._invoke({"object_type": "agents", "per_page": 2}, MagicMock())
        assert result.status == StructuredToolResultStatus.SUCCESS, result.error
        agents = result.data["agents"]
        assert isinstance(agents, list)
        if agents:
            assert "id" in agents[0]
            assert "email" in agents[0]

    def test_ticket_conversations(self, live_toolset):
        list_tool = _tool(live_toolset, "freshservice_list_objects")
        listing = list_tool._invoke(
            {"object_type": "tickets", "per_page": 1}, MagicMock()
        )
        assert listing.status == StructuredToolResultStatus.SUCCESS, listing.error
        if not listing.data["tickets"]:
            pytest.skip("No tickets in the live instance")
        ticket_id = listing.data["tickets"][0]["id"]

        tool = _tool(live_toolset, "freshservice_list_related_objects")
        result = tool._invoke(
            {
                "object_type": "tickets",
                "object_id": ticket_id,
                "relation": "conversations",
            },
            MagicMock(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS, result.error
        assert isinstance(result.data["conversations"], list)

    def test_updated_since_filters(self, live_toolset):
        tool = _tool(live_toolset, "freshservice_list_objects")
        # A timestamp far in the future must return an empty list if the
        # server actually applies the filter
        result = tool._invoke(
            {"object_type": "tickets", "updated_since": "2999-01-01T00:00:00Z"},
            MagicMock(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS, result.error
        assert result.data["tickets"] == []


@pytest.mark.skipif(
    not (
        LIVE_URL
        and LIVE_API_KEY
        and os.environ.get("FRESHSERVICE_TEST_WRITES", "").lower() == "true"
    ),
    reason="Live write tests require FRESHSERVICE_TEST_WRITES=true in addition to the URL/API key env vars (they create and delete a test ticket)",
)
class TestLiveFreshserviceWrites:
    """Live write tests: create -> update -> add note -> delete a marked test ticket.

    Opt-in via FRESHSERVICE_TEST_WRITES=true so CI never writes to a real
    instance accidentally. The ticket is deleted (trashed) at the end.
    """

    @pytest.fixture
    def live_toolset(self):
        ts = FreshserviceToolset()
        ok, msg = ts.prerequisites_callable(
            {
                "api_url": LIVE_URL,
                "api_key": LIVE_API_KEY,
                "enable_write_tools": True,
                "require_approval_for_writes": False,
            }
        )
        assert ok, f"Health check failed: {msg}"
        return ts

    def test_full_write_lifecycle(self, live_toolset):
        create_tool = _tool(live_toolset, "freshservice_create_object")
        created = create_tool._invoke(
            {
                "object_type": "tickets",
                "object_data": json.dumps(
                    {
                        "subject": "[HOLMES-TOOLSET-TEST] temporary ticket - safe to ignore",
                        "description": "Created by the HolmesGPT Freshservice live write test. Deleted automatically.",
                        "email": "holmes-toolset-test@example.com",
                        "status": 2,
                        "priority": 1,
                    }
                ),
            },
            MagicMock(),
        )
        assert created.status == StructuredToolResultStatus.SUCCESS, created.error
        ticket_id = created.data["ticket"]["id"]

        try:
            update_tool = _tool(live_toolset, "freshservice_update_object")
            updated = update_tool._invoke(
                {
                    "object_type": "tickets",
                    "object_id": ticket_id,
                    "object_data": '{"priority": 2}',
                },
                MagicMock(),
            )
            assert updated.status == StructuredToolResultStatus.SUCCESS, updated.error
            assert updated.data["ticket"]["priority"] == 2

            note_tool = _tool(live_toolset, "freshservice_create_related_object")
            note = note_tool._invoke(
                {
                    "object_type": "tickets",
                    "object_id": ticket_id,
                    "relation": "notes",
                    "object_data": '{"body": "<div>live write test note</div>", "private": true}',
                },
                MagicMock(),
            )
            assert note.status == StructuredToolResultStatus.SUCCESS, note.error
        finally:
            delete_tool = _tool(live_toolset, "freshservice_delete_object")
            deleted = delete_tool._invoke(
                {"object_type": "tickets", "object_id": ticket_id}, MagicMock()
            )
            assert deleted.status == StructuredToolResultStatus.SUCCESS, deleted.error

        get_tool = _tool(live_toolset, "freshservice_get_object")
        after = get_tool._invoke(
            {"object_type": "tickets", "object_id": ticket_id}, MagicMock()
        )
        if after.status == StructuredToolResultStatus.SUCCESS:
            assert after.data["ticket"].get("deleted") is True

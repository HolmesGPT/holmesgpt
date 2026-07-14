"""Unit tests for the Freshservice toolset (HTTP calls mocked with responses)."""

import json

import pytest
import responses as responses_

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.freshservice.freshservice import (
    OBJECT_TYPES,
    FreshserviceConfig,
    FreshserviceToolset,
)
from tests.conftest import create_mock_tool_invoke_context

API_URL = "https://demo.freshservice.example.com"


@pytest.fixture()
def toolset():
    ts = FreshserviceToolset()
    ts.config = FreshserviceConfig(api_url=API_URL, api_key="test-key")
    return ts


@pytest.fixture()
def tools(toolset):
    return {t.name: t for t in toolset.tools}


@pytest.fixture()
def context():
    return create_mock_tool_invoke_context()


class TestConfigAndHealthCheck:
    def test_missing_api_key_fails_validation(self):
        ts = FreshserviceToolset()
        ok, msg = ts.prerequisites_callable({"api_url": API_URL})
        assert ok is False
        assert "api_key" in msg

    def test_health_check_success(self):
        ts = FreshserviceToolset()
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": []},
                status=200,
            )
            ok, msg = ts.prerequisites_callable(
                {"api_url": API_URL, "api_key": "test-key"}
            )
        assert ok is True
        assert "accessible" in msg

    def test_health_check_auth_failure_includes_api_error(self):
        ts = FreshserviceToolset()
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/tickets",
                json={"code": "invalid_credentials", "message": "bad key"},
                status=401,
            )
            ok, msg = ts.prerequisites_callable(
                {"api_url": API_URL, "api_key": "wrong-key"}
            )
        assert ok is False
        assert "401" in msg
        assert "invalid_credentials" in msg

    def test_trailing_slash_in_api_url_is_handled(self):
        ts = FreshserviceToolset()
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": []},
                status=200,
            )
            ok, _ = ts.prerequisites_callable(
                {"api_url": API_URL + "/", "api_key": "test-key"}
            )
        assert ok is True


class TestListRecords:
    def test_list_tickets_with_pagination(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": [{"id": 1, "subject": "Checkout 502"}]},
                status=200,
                match=[
                    responses_.matchers.query_param_matcher(
                        {"page": "2", "per_page": "10"}
                    )
                ],
            )
            result = tools["freshservice_list_records"]._invoke(
                {"object_type": "tickets", "page": 2, "per_page": 10}, context
            )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["tickets"][0]["subject"] == "Checkout 502"

    def test_per_page_is_capped_at_api_maximum(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/changes",
                json={"changes": []},
                status=200,
                match=[
                    responses_.matchers.query_param_matcher(
                        {"page": "1", "per_page": "100"}
                    )
                ],
            )
            result = tools["freshservice_list_records"]._invoke(
                {"object_type": "changes", "per_page": 5000}, context
            )
        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_extra_query_params_are_forwarded(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/solutions/articles",
                json={"articles": []},
                status=200,
                match=[
                    responses_.matchers.query_param_matcher(
                        {"folder_id": "42", "page": "1", "per_page": "30"}
                    )
                ],
            )
            result = tools["freshservice_list_records"]._invoke(
                {"object_type": "solution_articles", "query_params": "folder_id=42"},
                context,
            )
        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_unknown_object_type_raises(self, tools, context):
        with pytest.raises(ValueError, match="Unknown object_type"):
            tools["freshservice_list_records"]._invoke(
                {"object_type": "nonexistent"}, context
            )

    def test_plan_restriction_error_is_passed_through(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/assets",
                json={"code": "require_feature", "message": "Cmdb not supported"},
                status=403,
            )
            result = tools["freshservice_list_records"]._invoke(
                {"object_type": "assets"}, context
            )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "403" in result.error
        assert "require_feature" in result.error
        assert "/api/v2/assets" in result.error


class TestGetRecord:
    def test_get_change(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/changes/3",
                json={"change": {"id": 3, "subject": "DB tuning"}},
                status=200,
            )
            result = tools["freshservice_get_record"]._invoke(
                {"object_type": "changes", "record_id": 3}, context
            )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["change"]["id"] == 3

    def test_get_ticket_with_include(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/tickets/1",
                json={"ticket": {"id": 1, "stats": {"agent_responded_at": None}}},
                status=200,
                match=[
                    responses_.matchers.query_param_matcher({"include": "stats"})
                ],
            )
            result = tools["freshservice_get_record"]._invoke(
                {"object_type": "tickets", "record_id": 1, "include": "stats"},
                context,
            )
        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_not_found_error_is_detailed(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/problems/999",
                json={"message": "Record not found"},
                status=404,
            )
            result = tools["freshservice_get_record"]._invoke(
                {"object_type": "problems", "record_id": 999}, context
            )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "404" in result.error
        assert "/api/v2/problems/999" in result.error


class TestFilterTickets:
    def test_query_is_wrapped_in_quotes(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/tickets/filter",
                json={"tickets": [{"id": 1}], "total": 1},
                status=200,
                match=[
                    responses_.matchers.query_param_matcher(
                        {"query": '"priority:4 AND status:2"', "page": "1"}
                    )
                ],
            )
            result = tools["freshservice_filter_tickets"]._invoke(
                {"query": "priority:4 AND status:2"}, context
            )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["total"] == 1

    def test_pre_quoted_query_is_not_double_quoted(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/tickets/filter",
                json={"tickets": []},
                status=200,
                match=[
                    responses_.matchers.query_param_matcher(
                        {"query": '"status:2"', "page": "1"}
                    )
                ],
            )
            result = tools["freshservice_filter_tickets"]._invoke(
                {"query": '"status:2"'}, context
            )
        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_invalid_filter_error_is_passed_through(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/tickets/filter",
                json={
                    "description": "Validation failed",
                    "errors": [{"field": "bogus", "code": "invalid_field"}],
                },
                status=400,
            )
            result = tools["freshservice_filter_tickets"]._invoke(
                {"query": "bogus:1"}, context
            )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "invalid_field" in result.error


class TestTicketConversations:
    def test_get_conversations(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/tickets/1/conversations",
                json={
                    "conversations": [
                        {"id": 10, "body_text": "FATAL: remaining connection slots"}
                    ]
                },
                status=200,
            )
            result = tools["freshservice_get_ticket_conversations"]._invoke(
                {"ticket_id": 1}, context
            )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "connection slots" in result.data["conversations"][0]["body_text"]


class TestSearchSolutionArticles:
    def test_search(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/solutions/articles/search",
                json={"articles": [{"id": 5, "title": "Payment platform runbook"}]},
                status=200,
                match=[
                    responses_.matchers.query_param_matcher(
                        {"search_term": "connection slots"}
                    )
                ],
            )
            result = tools["freshservice_search_solution_articles"]._invoke(
                {"search_term": "connection slots"}, context
            )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["articles"][0]["title"] == "Payment platform runbook"


class TestCreateRecord:
    def test_create_ticket_posts_json_payload(self, tools, context):
        payload = {"subject": "New incident", "email": "a@b.co", "status": 2}
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.POST,
                f"{API_URL}/api/v2/tickets",
                json={"ticket": {"id": 99, "subject": "New incident"}},
                status=201,
                match=[responses_.matchers.json_params_matcher(payload)],
            )
            result = tools["freshservice_create_record"]._invoke(
                {"object_type": "tickets", "data": json.dumps(payload)}, context
            )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["ticket"]["id"] == 99

    def test_invalid_json_returns_error_without_api_call(self, tools, context):
        with responses_.RequestsMock():
            result = tools["freshservice_create_record"]._invoke(
                {"object_type": "tickets", "data": "{not json"}, context
            )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid JSON" in result.error

    def test_validation_error_is_passed_through(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.POST,
                f"{API_URL}/api/v2/problems",
                json={
                    "description": "Validation failed",
                    "errors": [{"field": "due_by", "code": "missing_field"}],
                },
                status=400,
            )
            result = tools["freshservice_create_record"]._invoke(
                {"object_type": "problems", "data": json.dumps({"subject": "x"})},
                context,
            )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "due_by" in result.error

    def test_read_only_object_type_is_rejected(self, tools, context):
        result = tools["freshservice_create_record"]._invoke(
            {"object_type": "service_catalog_items", "data": "{}"}, context
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "does not support creating" in result.error


class TestUpdateRecord:
    def test_update_ticket_status(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.PUT,
                f"{API_URL}/api/v2/tickets/4",
                json={"ticket": {"id": 4, "status": 4}},
                status=200,
                match=[responses_.matchers.json_params_matcher({"status": 4})],
            )
            result = tools["freshservice_update_record"]._invoke(
                {
                    "object_type": "tickets",
                    "record_id": 4,
                    "data": json.dumps({"status": 4}),
                },
                context,
            )
        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_stateflow_violation_is_passed_through(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.PUT,
                f"{API_URL}/api/v2/changes/5",
                json={
                    "description": "Validation failed",
                    "errors": [
                        {
                            "field": "status",
                            "message": "Status change is not applicable as per stateflow",
                        }
                    ],
                },
                status=400,
            )
            result = tools["freshservice_update_record"]._invoke(
                {
                    "object_type": "changes",
                    "record_id": 5,
                    "data": json.dumps({"status": 3}),
                },
                context,
            )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "stateflow" in result.error


class TestAddNote:
    def test_add_private_ticket_note_by_default(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.POST,
                f"{API_URL}/api/v2/tickets/1/notes",
                json={"note": {"id": 7}},
                status=201,
                match=[
                    responses_.matchers.json_params_matcher(
                        {"body": "<p>finding</p>", "private": True}
                    )
                ],
            )
            result = tools["freshservice_add_note"]._invoke(
                {"object_type": "tickets", "record_id": 1, "body": "<p>finding</p>"},
                context,
            )
        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_add_public_ticket_note(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.POST,
                f"{API_URL}/api/v2/tickets/1/notes",
                json={"note": {"id": 8}},
                status=201,
                match=[
                    responses_.matchers.json_params_matcher(
                        {"body": "update", "private": False}
                    )
                ],
            )
            result = tools["freshservice_add_note"]._invoke(
                {
                    "object_type": "tickets",
                    "record_id": 1,
                    "body": "update",
                    "private": False,
                },
                context,
            )
        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_problem_note_has_no_private_field(self, tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.POST,
                f"{API_URL}/api/v2/problems/2/notes",
                json={"note": {"id": 9}},
                status=201,
                match=[
                    responses_.matchers.json_params_matcher({"body": "root cause"})
                ],
            )
            result = tools["freshservice_add_note"]._invoke(
                {"object_type": "problems", "record_id": 2, "body": "root cause"},
                context,
            )
        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_unsupported_object_type_rejected(self, tools, context):
        result = tools["freshservice_add_note"]._invoke(
            {"object_type": "requesters", "record_id": 1, "body": "x"}, context
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Notes are only supported" in result.error


class TestReadonlyMode:
    @pytest.fixture()
    def readonly_tools(self):
        ts = FreshserviceToolset()
        ts.config = FreshserviceConfig(
            api_url=API_URL, api_key="test-key", readonly=True
        )
        return {t.name: t for t in ts.tools}

    @pytest.mark.parametrize(
        "tool_name,params",
        [
            ("freshservice_create_record", {"object_type": "tickets", "data": "{}"}),
            (
                "freshservice_update_record",
                {"object_type": "tickets", "record_id": 1, "data": "{}"},
            ),
            (
                "freshservice_add_note",
                {"object_type": "tickets", "record_id": 1, "body": "x"},
            ),
        ],
    )
    def test_write_tools_blocked(self, readonly_tools, context, tool_name, params):
        result = readonly_tools[tool_name]._invoke(params, context)
        assert result.status == StructuredToolResultStatus.ERROR
        assert "read-only" in result.error

    def test_read_tools_still_work(self, readonly_tools, context):
        with responses_.RequestsMock() as rsps:
            rsps.add(
                responses_.GET,
                f"{API_URL}/api/v2/tickets",
                json={"tickets": []},
                status=200,
            )
            result = readonly_tools["freshservice_list_records"]._invoke(
                {"object_type": "tickets"}, context
            )
        assert result.status == StructuredToolResultStatus.SUCCESS


class TestToolsetDefinition:
    def test_all_object_types_have_unique_paths(self):
        paths = [obj.path for obj in OBJECT_TYPES.values()]
        assert len(paths) == len(set(paths))

    def test_one_liners_render(self, tools):
        for tool in tools.values():
            line = tool.get_parameterized_one_liner(
                {"object_type": "tickets", "record_id": 1, "query": "status:2"}
            )
            assert isinstance(line, str) and line

"""Live tests for the Freshservice toolset.

These tests run against a real Freshservice instance and are skipped unless
credentials are provided via environment variables:

    FRESHSERVICE_URL / FRESHSERVICE_API_KEY
    (or FRESHWORK_URL / FRESHWORK_API_KEY)

The instance is expected to contain the demo dataset created by
scripts/seed_freshservice_demo.py (tickets, changes, problems and knowledge
base articles for the "failure caused by a change" scenario).

Write tests create records tagged 'holmes-live-test' and delete them afterwards.
"""

import json
import os

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.freshservice.freshservice import FreshserviceToolset
from tests.conftest import create_mock_tool_invoke_context

FRESHSERVICE_URL = os.environ.get("FRESHSERVICE_URL") or os.environ.get(
    "FRESHWORK_URL"
)
FRESHSERVICE_API_KEY = os.environ.get("FRESHSERVICE_API_KEY") or os.environ.get(
    "FRESHWORK_API_KEY"
)

pytestmark = pytest.mark.skipif(
    not FRESHSERVICE_URL or not FRESHSERVICE_API_KEY,
    reason="FRESHSERVICE_URL / FRESHSERVICE_API_KEY (or FRESHWORK_*) not set",
)


@pytest.fixture(scope="module")
def toolset():
    ts = FreshserviceToolset()
    ok, msg = ts.prerequisites_callable(
        {"api_url": FRESHSERVICE_URL, "api_key": FRESHSERVICE_API_KEY}
    )
    assert ok, f"Freshservice health check failed: {msg}"
    return ts


@pytest.fixture(scope="module")
def tools(toolset):
    return {t.name: t for t in toolset.tools}


@pytest.fixture()
def context():
    return create_mock_tool_invoke_context()


class TestLiveReads:
    def test_list_tickets(self, tools, context):
        result = tools["freshservice_list_records"]._invoke(
            {"object_type": "tickets", "per_page": 50}, context
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert len(result.data["tickets"]) > 0

    def test_filter_urgent_open_tickets(self, tools, context):
        result = tools["freshservice_filter_tickets"]._invoke(
            {"query": "priority:4 AND status:2"}, context
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        subjects = [t["subject"] for t in result.data["tickets"]]
        assert any("Checkout" in s for s in subjects), subjects

    def test_list_changes_and_read_culprit_change(self, tools, context):
        result = tools["freshservice_list_records"]._invoke(
            {"object_type": "changes", "per_page": 50}, context
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        culprit = next(
            (
                c
                for c in result.data["changes"]
                if "PostgreSQL" in (c.get("subject") or "")
            ),
            None,
        )
        assert culprit, "seeded culprit change not found"

        detail = tools["freshservice_get_record"]._invoke(
            {"object_type": "changes", "record_id": culprit["id"]}, context
        )
        assert detail.status == StructuredToolResultStatus.SUCCESS
        rollout = detail.data["change"]["planning_fields"]["rollout_plan"][
            "description_text"
        ]
        assert "max_connections=20" in rollout

    def test_ticket_conversations_contain_agent_triage_notes(self, tools, context):
        filtered = tools["freshservice_filter_tickets"]._invoke(
            {"query": "priority:4 AND status:2"}, context
        )
        ticket_id = filtered.data["tickets"][0]["id"]
        result = tools["freshservice_get_ticket_conversations"]._invoke(
            {"ticket_id": ticket_id}, context
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        bodies = " ".join(
            c.get("body_text") or "" for c in result.data["conversations"]
        )
        assert "connection slots" in bodies

    def test_search_kb_finds_runbook(self, tools, context):
        result = tools["freshservice_search_solution_articles"]._invoke(
            {"search_term": "connection slots"}, context
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        titles = [a["title"] for a in result.data["articles"]]
        assert any("runbook" in t.lower() for t in titles), titles

    def test_list_problems(self, tools, context):
        result = tools["freshservice_list_records"]._invoke(
            {"object_type": "problems"}, context
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        subjects = [p["subject"] for p in result.data["problems"]]
        assert any("payment" in s.lower() for s in subjects), subjects

    def test_unavailable_module_returns_detailed_error(self, tools, context):
        # The demo plan does not include classic CMDB modules like vendors; if
        # the target plan does, the call succeeds and the test is a no-op.
        result = tools["freshservice_list_records"]._invoke(
            {"object_type": "vendors"}, context
        )
        if result.status == StructuredToolResultStatus.ERROR:
            assert "403" in result.error
            assert "/api/v2/vendors" in result.error

    def test_list_assets_finds_payment_db(self, tools, context):
        result = tools["freshservice_list_records"]._invoke(
            {"object_type": "assets", "per_page": 100}, context
        )
        assert result.status == StructuredToolResultStatus.SUCCESS, result.error
        db = next(
            (a for a in result.data["assets"] if a["name"] == "payment-db-01"),
            None,
        )
        assert db, "seeded asset payment-db-01 not found"
        assert db["type"] == "Server"

        detail = tools["freshservice_get_record"]._invoke(
            {"object_type": "assets", "record_id": db["asset_id"]}, context
        )
        assert detail.status == StructuredToolResultStatus.SUCCESS, detail.error
        assert "PostgreSQL" in detail.data["notes"]

    def test_list_devices(self, tools, context):
        result = tools["freshservice_list_records"]._invoke(
            {"object_type": "devices", "per_page": 100}, context
        )
        assert result.status == StructuredToolResultStatus.SUCCESS, result.error
        names = [d["name"] for d in result.data["devices"]]
        assert "payment-db-01" in names, names


class TestLiveWrites:
    def test_create_note_and_update_roundtrip(self, toolset, tools, context):
        created = tools["freshservice_create_record"]._invoke(
            {
                "object_type": "tickets",
                "data": json.dumps(
                    {
                        "subject": "Holmes live test ticket (safe to delete)",
                        "description": "<p>Created by the Freshservice toolset live test.</p>",
                        "email": "holmes-live-test@example.com",
                        "status": 2,
                        "priority": 1,
                        "tags": ["holmes-live-test"],
                    }
                ),
            },
            context,
        )
        assert created.status == StructuredToolResultStatus.SUCCESS, created.error
        ticket_id = created.data["ticket"]["id"]

        try:
            noted = tools["freshservice_add_note"]._invoke(
                {
                    "object_type": "tickets",
                    "record_id": ticket_id,
                    "body": "<p>Live test note.</p>",
                },
                context,
            )
            assert noted.status == StructuredToolResultStatus.SUCCESS, noted.error

            updated = tools["freshservice_update_record"]._invoke(
                {
                    "object_type": "tickets",
                    "record_id": ticket_id,
                    "data": json.dumps({"status": 3}),
                },
                context,
            )
            assert updated.status == StructuredToolResultStatus.SUCCESS, updated.error
            assert updated.data["ticket"]["status"] == 3
        finally:
            # Clean up: delete the test ticket (delete is deliberately not a tool)
            response = toolset._request("DELETE", f"tickets/{ticket_id}")
            assert response.ok, response.text

    def test_asset_create_update_roundtrip(self, toolset, tools, context):
        created = tools["freshservice_create_record"]._invoke(
            {
                "object_type": "assets",
                "data": json.dumps(
                    {
                        "name": "holmes-live-test-asset",
                        "type": "Server",
                        "notes": "Created by the Freshservice toolset live test - safe to delete.",
                        "state": "In Stock",
                    }
                ),
            },
            context,
        )
        assert created.status == StructuredToolResultStatus.SUCCESS, created.error
        # itam create response: {"code": 0, "msg": ["asset added/edited.", <id>, ...]}
        asset_id = created.data["msg"][1]

        try:
            updated = tools["freshservice_update_record"]._invoke(
                {
                    "object_type": "assets",
                    "record_id": asset_id,
                    "data": json.dumps({"impact": "Medium"}),
                },
                context,
            )
            assert updated.status == StructuredToolResultStatus.SUCCESS, updated.error

            detail = tools["freshservice_get_record"]._invoke(
                {"object_type": "assets", "record_id": asset_id}, context
            )
            assert detail.status == StructuredToolResultStatus.SUCCESS, detail.error
            assert detail.data["impact"] == "medium"
        finally:
            response = toolset._request("DELETE", f"itam/assets/{asset_id}/")
            assert response.ok, response.text

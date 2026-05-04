"""Unit tests for the PagerDuty toolset."""

import json
from unittest.mock import patch, MagicMock

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.pagerduty.toolset_pagerduty import (
    PagerDutyConfig,
    PagerDutyToolset,
)
from tests.conftest import create_mock_tool_invoke_context


class TestPagerDutyConfig:
    def test_old_style_config_still_works(self):
        """Existing configs without filter fields must continue to load."""
        cfg = PagerDutyConfig(api_key="secret-key")
        assert cfg.api_key == "secret-key"
        assert cfg.default_limit == 25
        assert cfg.team_ids is None
        assert cfg.service_ids is None
        assert cfg.api_url == "https://api.pagerduty.com"

    def test_new_style_config_with_filters(self):
        cfg = PagerDutyConfig(
            api_key="k",
            team_ids=["PTEAM1"],
            service_ids=["PSVC1", "PSVC2"],
        )
        assert cfg.team_ids == ["PTEAM1"]
        assert cfg.service_ids == ["PSVC1", "PSVC2"]

    def test_api_url_override(self):
        cfg = PagerDutyConfig(api_key="k", api_url="http://localhost:9999")
        assert cfg.api_url == "http://localhost:9999"


class TestApplyScopeFilters:
    def _toolset(self, **cfg_kwargs) -> PagerDutyToolset:
        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k", **cfg_kwargs)
        return ts

    def test_no_instance_filters_no_user_params(self):
        ts = self._toolset()
        query, note = ts._apply_scope_filters({}, {})
        assert "service_ids[]" not in query
        assert "team_ids[]" not in query
        assert note is None

    def test_instance_service_ids_no_user_params(self):
        ts = self._toolset(service_ids=["P1", "P2"])
        query, note = ts._apply_scope_filters({}, {})
        assert query["service_ids[]"] == ["P1", "P2"]
        assert note is None

    def test_instance_and_user_service_ids_intersect(self):
        ts = self._toolset(service_ids=["P1", "P2"])
        query, note = ts._apply_scope_filters({}, {"service_ids": "P1"})
        assert query["service_ids[]"] == ["P1"]
        assert note is None

    def test_user_service_ids_outside_scope_dropped(self):
        ts = self._toolset(service_ids=["P1"])
        query, note = ts._apply_scope_filters({}, {"service_ids": "P2,P3"})
        assert query["service_ids[]"] == []
        assert note is not None
        assert "narrowed" in note.lower()
        assert "P1" in note

    def test_team_ids_and_service_ids_both_applied(self):
        ts = self._toolset(team_ids=["T1"], service_ids=["P1"])
        query, note = ts._apply_scope_filters({}, {})
        assert query["team_ids[]"] == ["T1"]
        assert query["service_ids[]"] == ["P1"]

    def test_no_instance_filters_user_passes_service_ids(self):
        """When instance has no scope, user-supplied filters pass through unchanged."""
        ts = self._toolset()
        query, note = ts._apply_scope_filters({}, {"service_ids": "PX,PY"})
        assert query["service_ids[]"] == ["PX", "PY"]
        assert note is None


def _mock_ok(json_body: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = json_body
    m.raise_for_status = MagicMock()
    return m


class TestListToolsWithScope:
    def _toolset(self, **cfg_kwargs) -> PagerDutyToolset:
        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_list_incidents_applies_instance_service_filter(self, mock_get):
        mock_get.return_value = _mock_ok({"incidents": []})
        ts = self._toolset(service_ids=["PSVC_ALPHA"])
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_incidents")

        result = tool._invoke({}, create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["service_ids[]"] == ["PSVC_ALPHA"]

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_list_services_applies_team_filter(self, mock_get):
        mock_get.return_value = _mock_ok({"services": []})
        ts = self._toolset(team_ids=["PTEAM_A"])
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_services")

        result = tool._invoke({}, create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["team_ids[]"] == ["PTEAM_A"]

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_get_oncall_applies_team_filter_but_not_service_filter(self, mock_get):
        # /oncalls does not support service_ids — confirm it is NOT sent.
        mock_get.return_value = _mock_ok({"oncalls": []})
        ts = self._toolset(team_ids=["PTEAM_A"], service_ids=["PSVC1"])
        tool = next(t for t in ts.tools if t.name == "get_pagerduty_oncall")

        result = tool._invoke({}, create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["team_ids[]"] == ["PTEAM_A"]
        assert "service_ids[]" not in kwargs["params"]

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_list_incidents_notes_narrowed_filter_in_result_data(self, mock_get):
        mock_get.return_value = _mock_ok({"incidents": []})
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_incidents")

        result = tool._invoke(
            {"service_ids": "P2,P3"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        # Note must be present AND result.data must be valid JSON.
        parsed = json.loads(result.data)
        assert "_scope_note" in parsed
        assert "narrowed" in parsed["_scope_note"].lower()

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_list_incidents_user_filter_passes_through_when_no_instance_scope(
        self, mock_get
    ):
        mock_get.return_value = _mock_ok({"incidents": []})
        ts = self._toolset()  # no instance scope
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_incidents")

        result = tool._invoke(
            {"service_ids": "PX,PY"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["service_ids[]"] == ["PX", "PY"]


class TestGetIncidentScopeGuard:
    def _toolset(self, **cfg_kwargs) -> PagerDutyToolset:
        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_in_scope_incident_returns_success(self, mock_get):
        mock_get.return_value = _mock_ok(
            {"incident": {"id": "PINC1", "service": {"id": "P1"}, "html_url": "http://x"}}
        )
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "get_pagerduty_incident")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_out_of_scope_incident_returns_error(self, mock_get):
        mock_get.return_value = _mock_ok(
            {"incident": {"id": "PINC1", "service": {"id": "P99"}}}
        )
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "get_pagerduty_incident")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in this project's scope" in result.error

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_no_scope_set_returns_success(self, mock_get):
        """When instance has no service_ids, any incident is returned."""
        mock_get.return_value = _mock_ok(
            {"incident": {"id": "PINC1", "service": {"id": "P99"}}}
        )
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_pagerduty_incident")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_null_service_on_incident_treated_as_out_of_scope(self, mock_get):
        mock_get.return_value = _mock_ok(
            {"incident": {"id": "PINC1", "service": None}}
        )
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "get_pagerduty_incident")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in this project's scope" in result.error


class TestListAlertsScopeGuard:
    def _toolset(self, **cfg_kwargs) -> PagerDutyToolset:
        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_out_of_scope_parent_blocks_alerts_call(self, mock_get):
        # First GET returns the parent incident (out-of-scope).
        mock_get.return_value = _mock_ok(
            {"incident": {"id": "PINC1", "service": {"id": "P99"}}}
        )
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_alerts")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in this project's scope" in result.error
        # Exactly one GET should have been made — the parent lookup.
        assert mock_get.call_count == 1

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_in_scope_parent_allows_alerts_call(self, mock_get):
        mock_get.side_effect = [
            _mock_ok({"incident": {"id": "PINC1", "service": {"id": "P1"}}}),
            _mock_ok({"alerts": []}),
        ]
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_alerts")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert mock_get.call_count == 2

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_no_scope_skips_parent_check(self, mock_get):
        """When no service_ids configured, no extra parent-lookup round-trip."""
        mock_get.return_value = _mock_ok({"alerts": []})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_alerts")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert mock_get.call_count == 1

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_null_service_on_parent_treated_as_out_of_scope(self, mock_get):
        """Defensive: if parent incident has service=null, block rather than crash."""
        mock_get.return_value = _mock_ok(
            {"incident": {"id": "PINC1", "service": None}}
        )
        ts = self._toolset(service_ids=["P1"])
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_alerts")

        result = tool._invoke(
            {"incident_id": "PINC1"}, create_mock_tool_invoke_context()
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in this project's scope" in result.error


class TestHealthCheck:
    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_health_check_401_returns_clear_error(self, mock_get):
        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        mock_get.return_value = resp
        ts = PagerDutyToolset()
        ok, msg = ts.prerequisites_callable({"api_key": "bad"})
        assert ok is False
        assert "invalid or expired" in msg

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_health_check_includes_scope_filters(self, mock_get):
        mock_get.return_value = _mock_ok({"services": []})
        ts = PagerDutyToolset()
        ok, msg = ts.prerequisites_callable(
            {"api_key": "k", "service_ids": ["P1"], "team_ids": ["T1"]}
        )
        assert ok is True
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["service_ids[]"] == ["P1"]
        assert kwargs["params"]["team_ids[]"] == ["T1"]

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_health_check_no_filters_no_filter_params(self, mock_get):
        mock_get.return_value = _mock_ok({"services": []})
        ts = PagerDutyToolset()
        ok, msg = ts.prerequisites_callable({"api_key": "k"})
        assert ok is True
        _, kwargs = mock_get.call_args
        assert "service_ids[]" not in kwargs["params"]
        assert "team_ids[]" not in kwargs["params"]


import requests as _requests


class TestRuntimeErrorMessages:
    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_401_at_tool_call_returns_clear_error(self, mock_get):
        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        resp.raise_for_status.side_effect = _requests.HTTPError(response=resp)
        mock_get.return_value = resp

        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k")
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_incidents")
        result = tool._invoke({}, create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.ERROR
        assert "rejected" in result.error.lower() or "401" in result.error

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    def test_429_at_tool_call_returns_rate_limit_error(self, mock_get):
        resp = MagicMock()
        resp.status_code = 429
        resp.text = "Too Many Requests"
        resp.headers = {"Retry-After": "30"}
        resp.raise_for_status.side_effect = _requests.HTTPError(response=resp)
        mock_get.return_value = resp

        ts = PagerDutyToolset()
        ts.pd_config = PagerDutyConfig(api_key="k")
        tool = next(t for t in ts.tools if t.name == "list_pagerduty_incidents")
        result = tool._invoke({}, create_mock_tool_invoke_context())

        assert result.status == StructuredToolResultStatus.ERROR
        assert "rate limit" in result.error.lower()
        assert "30" in result.error

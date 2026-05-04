"""Unit tests for the PagerDuty toolset."""

from holmes.plugins.toolsets.pagerduty.toolset_pagerduty import (
    PagerDutyConfig,
    PagerDutyToolset,
)


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

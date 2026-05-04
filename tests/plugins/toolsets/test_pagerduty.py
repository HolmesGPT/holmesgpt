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

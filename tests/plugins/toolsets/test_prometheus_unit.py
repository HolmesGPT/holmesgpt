import logging

import pytest

from holmes.plugins.toolsets.prometheus.prometheus import (
    AzurePrometheusConfig,
    PrometheusConfig,
    PrometheusToolset,
    adjust_step_for_max_points,
    get_config_field,
)


@pytest.mark.parametrize(
    "start_timestamp, end_timestamp, step, max_points_value, expected_step",
    [
        # Test case 1: Points within limit, no adjustment needed
        (
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",  # 1 hour = 3600 seconds
            60,  # 60 second step = 60 points (within 300 limit)
            300,
            60,  # No adjustment needed
        ),
        # Test case 2: Points exceed limit, adjustment needed
        (
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",  # 1 hour = 3600 seconds
            10,  # 10 second step = 360 points (exceeds 300 limit)
            300,
            12.0,  # Adjusted to 3600/300 = 12 seconds
        ),
        # Test case 3: Exactly at limit
        (
            "2024-01-01T00:00:00Z",
            "2024-01-01T05:00:00Z",  # 5 hours = 18000 seconds
            60,  # 60 second step = 300 points (exactly at limit)
            300,
            60,  # No adjustment needed
        ),
        # Test case 4: Large time range requiring significant adjustment
        (
            "2024-01-01T00:00:00Z",
            "2024-01-02T00:00:00Z",  # 24 hours = 86400 seconds
            60,  # 60 second step = 1440 points (way over 300 limit)
            300,
            288.0,  # Adjusted to 86400/300 = 288 seconds
        ),
        # Test case 5: Custom max_points limit
        (
            "2024-01-01T00:00:00Z",
            "2024-01-01T00:30:00Z",  # 30 minutes = 1800 seconds
            10,  # 10 second step = 180 points
            100,  # Lower max_points limit
            18.0,  # Adjusted to 1800/100 = 18 seconds
        ),
    ],
)
def test_adjust_step_for_max_points(
    monkeypatch, start_timestamp, end_timestamp, step, max_points_value, expected_step
):
    # Mock the MAX_GRAPH_POINTS constant directly in the prometheus module
    import holmes.plugins.toolsets.prometheus.prometheus as prom_module

    monkeypatch.setattr(prom_module, "MAX_GRAPH_POINTS", max_points_value)

    result = adjust_step_for_max_points(start_timestamp, end_timestamp, step)
    assert result == expected_step


@pytest.mark.parametrize(
    "start_timestamp, end_timestamp, max_graph_points, expected_step",
    [
        # Default step targets max_points data points
        # 1 hour range, MAX_GRAPH_POINTS=500 -> step = 3600/500 = 7.2
        (
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            500,
            7.2,
        ),
        # 6 hour range, MAX_GRAPH_POINTS=500 -> step = 21600/500 = 43.2
        (
            "2024-01-01T00:00:00Z",
            "2024-01-01T06:00:00Z",
            500,
            43.2,
        ),
        # 24 hour range, MAX_GRAPH_POINTS=500 -> step = 86400/500 = 172.8
        (
            "2024-01-01T00:00:00Z",
            "2024-01-02T00:00:00Z",
            500,
            172.8,
        ),
        # 1 hour range, MAX_GRAPH_POINTS=100 (old default) -> step = 3600/100 = 36
        (
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            100,
            36.0,
        ),
    ],
)
def test_default_step_targets_max_points(
    monkeypatch, start_timestamp, end_timestamp, max_graph_points, expected_step
):
    """When no step is provided, default step should target max_points data points."""
    import holmes.plugins.toolsets.prometheus.prometheus as prom_module

    monkeypatch.setattr(prom_module, "MAX_GRAPH_POINTS", max_graph_points)

    result = adjust_step_for_max_points(start_timestamp, end_timestamp, step=None)
    assert result == expected_step


class TestMaxPointsOverride:
    """Tests for LLM max_points override behavior."""

    def test_override_above_default_is_allowed(self, monkeypatch):
        """LLM can request more points than MAX_GRAPH_POINTS for higher resolution."""
        import holmes.plugins.toolsets.prometheus.prometheus as prom_module

        monkeypatch.setattr(prom_module, "MAX_GRAPH_POINTS", 500.0)
        monkeypatch.setattr(prom_module, "MAX_GRAPH_POINTS_HARD_LIMIT", 1000.0)

        # 1 hour range, requesting 1000 points -> step = 3600/1000 = 3.6
        result = adjust_step_for_max_points(
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            step=None,
            max_points_override=1000,
        )
        assert result == 3.6

    def test_override_capped_at_hard_limit(self, monkeypatch):
        """Override cannot exceed MAX_GRAPH_POINTS_HARD_LIMIT."""
        import holmes.plugins.toolsets.prometheus.prometheus as prom_module

        monkeypatch.setattr(prom_module, "MAX_GRAPH_POINTS", 500.0)
        monkeypatch.setattr(prom_module, "MAX_GRAPH_POINTS_HARD_LIMIT", 1000.0)

        # Hard limit is 500 * 2 = 1000
        # Requesting 2000 should be capped at 1000
        # 1 hour range with 1000 points -> step = 3600/1000 = 3.6
        result = adjust_step_for_max_points(
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            step=None,
            max_points_override=2000,
        )
        assert result == 3600 / 1000

    def test_override_below_default_is_allowed(self, monkeypatch):
        """LLM can request fewer points for simpler graphs."""
        import holmes.plugins.toolsets.prometheus.prometheus as prom_module

        monkeypatch.setattr(prom_module, "MAX_GRAPH_POINTS", 500.0)

        # 1 hour range, requesting only 50 points -> step = 3600/50 = 72
        result = adjust_step_for_max_points(
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            step=None,
            max_points_override=50,
        )
        assert result == 72.0

    def test_override_invalid_value_uses_default(self, monkeypatch):
        """Invalid override (< 1) falls back to default."""
        import holmes.plugins.toolsets.prometheus.prometheus as prom_module

        monkeypatch.setattr(prom_module, "MAX_GRAPH_POINTS", 500.0)

        # Invalid override, should use default of 500
        # 1 hour range with 500 points -> step = 3600/500 = 7.2
        result = adjust_step_for_max_points(
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            step=None,
            max_points_override=0,
        )
        assert result == 7.2

    def test_override_with_explicit_step_adjusts_if_needed(self, monkeypatch):
        """When both step and override are provided, step is adjusted if it exceeds override."""
        import holmes.plugins.toolsets.prometheus.prometheus as prom_module

        monkeypatch.setattr(prom_module, "MAX_GRAPH_POINTS", 500.0)
        monkeypatch.setattr(prom_module, "MAX_GRAPH_POINTS_HARD_LIMIT", 1000.0)

        # 1 hour range, step=1 (would give 3600 points), max_points=1000
        # 3600 > 1000, so adjusted_step = 3600/1000 = 3.6
        result = adjust_step_for_max_points(
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            step=1,
            max_points_override=1000,
        )
        assert result == 3.6


AZURE_ENV_VARS = (
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_USE_MANAGED_ID",
)


@pytest.fixture
def clean_azure_env(monkeypatch):
    for name in AZURE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestGetConfigField:
    """get_config_field reads ``field`` from the config dict, falling back to
    the upper-cased env var (e.g. ``AZURE_CLIENT_ID``)."""

    def test_returns_config_value_when_present(self, clean_azure_env):
        assert (
            get_config_field({"azure_client_id": "from-config"}, "azure_client_id")
            == "from-config"
        )

    def test_falls_back_to_env_var(self, clean_azure_env):
        clean_azure_env.setenv("AZURE_CLIENT_ID", "from-env")
        assert get_config_field({}, "azure_client_id") == "from-env"

    def test_config_takes_precedence_over_env_var(self, clean_azure_env):
        clean_azure_env.setenv("AZURE_CLIENT_ID", "from-env")
        assert (
            get_config_field({"azure_client_id": "from-config"}, "azure_client_id")
            == "from-config"
        )

    def test_empty_config_value_falls_through_to_env(self, clean_azure_env):
        """Empty string in YAML (e.g. ``azure_client_id: ""``) should not
        suppress the env-var fallback — the empty value is no value."""
        clean_azure_env.setenv("AZURE_CLIENT_ID", "from-env")
        assert (
            get_config_field({"azure_client_id": ""}, "azure_client_id") == "from-env"
        )

    def test_missing_returns_none(self, clean_azure_env):
        assert get_config_field({}, "azure_client_id") is None

    def test_empty_env_var_returns_none(self, clean_azure_env):
        clean_azure_env.setenv("AZURE_CLIENT_ID", "")
        assert get_config_field({}, "azure_client_id") is None

    def test_non_string_config_value_passes_through(self, clean_azure_env):
        """Bool/int config values (e.g. ``azure_use_managed_id: true``) must
        not be coerced — callers handle the type-specific normalization."""
        assert (
            get_config_field({"azure_use_managed_id": True}, "azure_use_managed_id")
            is True
        )


class TestIsAzureConfig:
    """is_azure_config must match the completeness check inside
    AzurePrometheusConfig.__init__ — a partial signal (e.g. AZURE_CLIENT_ID
    alone, common when the LLM uses Azure AI Foundry) should NOT select the
    Azure variant, and the user should be warned why."""

    def test_no_signals_returns_false(self, clean_azure_env, caplog):
        with caplog.at_level(logging.WARNING):
            assert AzurePrometheusConfig.is_azure_config({}) is False
        assert "Azure" not in caplog.text

    def test_partial_env_vars_warns_and_returns_false(self, clean_azure_env, caplog):
        """Regression: user had AZURE_CLIENT_ID + AZURE_TENANT_ID from their
        Azure AI Foundry LLM setup, and an internal prometheus_url. Holmes
        must NOT hijack into Azure auth."""
        clean_azure_env.setenv("AZURE_CLIENT_ID", "from-llm")
        clean_azure_env.setenv("AZURE_TENANT_ID", "from-llm")
        with caplog.at_level(logging.WARNING):
            assert AzurePrometheusConfig.is_azure_config({}) is False
        assert "Partial Azure" in caplog.text
        assert "azure_client_secret" in caplog.text

    def test_partial_config_field_warns_and_returns_false(
        self, clean_azure_env, caplog
    ):
        with caplog.at_level(logging.WARNING):
            assert (
                AzurePrometheusConfig.is_azure_config({"azure_client_id": "x"})
                is False
            )
        assert "Partial Azure" in caplog.text

    def test_full_env_vars_returns_true_no_warning(self, clean_azure_env, caplog):
        clean_azure_env.setenv("AZURE_CLIENT_ID", "x")
        clean_azure_env.setenv("AZURE_TENANT_ID", "y")
        clean_azure_env.setenv("AZURE_CLIENT_SECRET", "z")
        with caplog.at_level(logging.WARNING):
            assert AzurePrometheusConfig.is_azure_config({}) is True
        assert "Partial Azure" not in caplog.text

    def test_managed_id_in_config_returns_true(self, clean_azure_env, caplog):
        with caplog.at_level(logging.WARNING):
            assert (
                AzurePrometheusConfig.is_azure_config({"azure_use_managed_id": True})
                is True
            )
        assert "Partial Azure" not in caplog.text

    def test_managed_id_env_var_returns_true(self, clean_azure_env, caplog):
        clean_azure_env.setenv("AZURE_USE_MANAGED_ID", "true")
        with caplog.at_level(logging.WARNING):
            assert AzurePrometheusConfig.is_azure_config({}) is True
        assert "Partial Azure" not in caplog.text

    def test_determine_prometheus_class_with_partial_azure_env(
        self, clean_azure_env, caplog
    ):
        """End-to-end: the user's exact scenario — partial Azure env vars on
        the pod plus a plain prometheus_url config — must resolve to the
        generic PrometheusConfig, not AzurePrometheusConfig."""
        clean_azure_env.setenv("AZURE_CLIENT_ID", "from-llm")
        clean_azure_env.setenv("AZURE_TENANT_ID", "from-llm")
        toolset = PrometheusToolset()
        with caplog.at_level(logging.WARNING):
            cls = toolset.determine_prometheus_class(
                {"prometheus_url": "https://internal.example:8030", "verify_ssl": True}
            )
        assert cls is PrometheusConfig
        assert "Partial Azure" in caplog.text

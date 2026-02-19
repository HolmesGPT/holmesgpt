import pytest

from holmes.plugins.toolsets.prometheus.prometheus import (
    adjust_step_for_max_points,
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

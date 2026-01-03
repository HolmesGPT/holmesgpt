import pytest

from holmes.plugins.toolsets.utils import (
    adjust_step_for_max_points,
    duration_string_to_seconds,
    seconds_to_duration_string,
)


class TestSecondsToDateString:
    """Test seconds to duration string conversion."""

    def test_zero_seconds(self):
        assert seconds_to_duration_string(0) == "0s"

    def test_seconds_only(self):
        assert seconds_to_duration_string(45) == "45s"
        assert seconds_to_duration_string(59) == "59s"

    def test_minutes_and_seconds(self):
        assert seconds_to_duration_string(60) == "1m"
        assert seconds_to_duration_string(61) == "1m1s"
        assert seconds_to_duration_string(90) == "1m30s"
        assert seconds_to_duration_string(119) == "1m59s"

    def test_hours_minutes_seconds(self):
        assert seconds_to_duration_string(3600) == "1h"
        assert seconds_to_duration_string(3661) == "1h1m1s"
        assert seconds_to_duration_string(3660) == "1h1m"
        assert seconds_to_duration_string(7200) == "2h"
        assert seconds_to_duration_string(7261) == "2h1m1s"

    def test_days(self):
        assert seconds_to_duration_string(86400) == "1d"
        assert seconds_to_duration_string(86461) == "1d1m1s"
        assert seconds_to_duration_string(90000) == "1d1h"
        assert seconds_to_duration_string(172800) == "2d"

    def test_weeks(self):
        assert seconds_to_duration_string(604800) == "1w"
        assert seconds_to_duration_string(604861) == "1w1m1s"
        assert seconds_to_duration_string(1209600) == "2w"

    def test_complex_duration(self):
        # 1 week, 2 days, 3 hours, 4 minutes, 5 seconds
        seconds = 1 * 604800 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5
        assert seconds_to_duration_string(seconds) == "1w2d3h4m5s"

    def test_negative_seconds_raises_error(self):
        with pytest.raises(ValueError, match="seconds must be non-negative"):
            seconds_to_duration_string(-1)


class TestDurationStringToSeconds:
    """Test duration string to seconds conversion."""

    def test_bare_number(self):
        assert duration_string_to_seconds("0") == 0
        assert duration_string_to_seconds("45") == 45
        assert duration_string_to_seconds("3600") == 3600

    def test_seconds_unit(self):
        assert duration_string_to_seconds("45s") == 45
        assert duration_string_to_seconds("1s") == 1

    def test_minutes(self):
        assert duration_string_to_seconds("1m") == 60
        assert duration_string_to_seconds("5m") == 300
        assert duration_string_to_seconds("90m") == 5400

    def test_hours(self):
        assert duration_string_to_seconds("1h") == 3600
        assert duration_string_to_seconds("2h") == 7200
        assert duration_string_to_seconds("24h") == 86400

    def test_days(self):
        assert duration_string_to_seconds("1d") == 86400
        assert duration_string_to_seconds("7d") == 604800

    def test_weeks(self):
        assert duration_string_to_seconds("1w") == 604800
        assert duration_string_to_seconds("2w") == 1209600

    def test_compound_durations(self):
        assert duration_string_to_seconds("1h30m") == 5400
        assert duration_string_to_seconds("2h30m45s") == 9045
        assert duration_string_to_seconds("1d12h") == 129600
        assert duration_string_to_seconds("1w2d3h4m5s") == 788645

    def test_order_doesnt_matter(self):
        # These should all parse to the same value
        assert duration_string_to_seconds("1h30m") == duration_string_to_seconds(
            "30m1h"
        )
        assert duration_string_to_seconds("1m30s") == duration_string_to_seconds(
            "30s1m"
        )

    def test_empty_string_raises_error(self):
        with pytest.raises(ValueError, match="duration_string cannot be empty"):
            duration_string_to_seconds("")

    def test_invalid_format_raises_error(self):
        with pytest.raises(ValueError, match="Invalid duration string"):
            duration_string_to_seconds("abc")
        with pytest.raises(ValueError, match="Invalid duration string"):
            duration_string_to_seconds("h")
        with pytest.raises(ValueError, match="Invalid duration string"):
            duration_string_to_seconds("@#$")


class TestRoundTripConversion:
    """Test that converting seconds to string and back gives the same value."""

    def test_round_trip_conversions(self):
        test_values = [0, 1, 59, 60, 61, 3600, 3661, 86400, 604800, 788645]

        for seconds in test_values:
            duration_str = seconds_to_duration_string(seconds)
            converted_back = duration_string_to_seconds(duration_str)
            assert (
                converted_back == seconds
            ), f"Round trip failed for {seconds}: {duration_str} -> {converted_back}"


class TestAdjustStepForMaxPoints:
    """Test step adjustment logic."""

    def test_no_step_provided_uses_default(self):
        # When no step is provided, should return minimum step to stay under max_points
        # For 3600 seconds with 300 max points, min step is ceil(3600/300) = 12
        assert adjust_step_for_max_points(3600, 300, None) == 12

    def test_step_too_small_gets_adjusted(self):
        # 3600 seconds / 300 points = 12 seconds minimum step
        assert adjust_step_for_max_points(3600, 300, 5) == 12
        assert adjust_step_for_max_points(3600, 300, 10) == 12

    def test_step_large_enough_unchanged(self):
        # If step is already large enough, keep it
        assert adjust_step_for_max_points(3600, 300, 15) == 15
        assert adjust_step_for_max_points(3600, 300, 60) == 60

    def test_exact_boundary(self):
        # 3600 / 300 = 12 exactly
        assert adjust_step_for_max_points(3600, 300, 12) == 12

    def test_various_time_ranges(self):
        # 1 hour with 60 points max
        assert adjust_step_for_max_points(3600, 60, None) == 60
        assert adjust_step_for_max_points(3600, 60, 30) == 60
        assert adjust_step_for_max_points(3600, 60, 120) == 120

        # 1 day with 300 points max
        assert adjust_step_for_max_points(86400, 300, None) == 288
        assert adjust_step_for_max_points(86400, 300, 100) == 288
        assert adjust_step_for_max_points(86400, 300, 500) == 500

        # 1 week with 300 points max
        assert adjust_step_for_max_points(604800, 300, None) == 2016
        assert adjust_step_for_max_points(604800, 300, 1000) == 2016
        assert adjust_step_for_max_points(604800, 300, 3000) == 3000

    def test_small_time_ranges(self):
        # 5 minutes with 300 points max
        assert adjust_step_for_max_points(300, 300, None) == 1
        assert adjust_step_for_max_points(300, 300, 1) == 1

        # 1 minute with 60 points max
        assert adjust_step_for_max_points(60, 60, None) == 1
        assert adjust_step_for_max_points(60, 60, 1) == 1

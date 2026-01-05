import pytest

from holmes.plugins.toolsets.prometheus.utils import parse_duration_to_seconds


class TestParseDurationToSeconds:
    @pytest.mark.parametrize(
        "input_value,expected",
        [
            # None case
            (None, None),
            # Numeric inputs (int and float)
            (42, 42.0),
            (3.14, 3.14),
            (0, 0.0),
            # String numeric inputs
            ("123", 123.0),
            ("0", 0.0),
            ("  456  ", 456.0),  # with whitespace
            # Time unit strings
            ("30s", 30.0),
            ("5m", 300.0),
            ("2h", 7200.0),
            ("1d", 86400.0),
            # Decimal values with units
            ("2.5s", 2.0),  # int(float(2.5) * 1) = 2
            ("1.5m", 90.0),  # int(float(1.5) * 60) = 90
            ("0.5h", 1800.0),  # int(float(0.5) * 3600) = 1800
            ("0.25d", 21600.0),  # int(float(0.25) * 86400) = 21600
            # Case insensitive and whitespace handling
            ("30S", 30.0),
            ("5M", 300.0),
            ("2H", 7200.0),
            ("1D", 86400.0),
            ("  30s  ", 30.0),
            # Fallback to float seconds
            ("123.45", 123.45),
            ("0.5", 0.5),
            # Edge cases
            ("10", 10.0),  # pure digit string
            ("0s", 0.0),
            ("0m", 0.0),
            ("0h", 0.0),
            ("0d", 0.0),
            # Partial time formats
            ("1h30m", 5400.0),  # 1*3600 + 30*60 = 5400
            ("2h45m", 9900.0),  # 2*3600 + 45*60 = 9900
            ("5m12s", 312.0),  # 5*60 + 12 = 312
            ("1m30s", 90.0),  # 1*60 + 30 = 90
            ("3h15m45s", 11745.0),  # 3*3600 + 15*60 + 45 = 11745
            ("1d2h30m", 95400.0),  # 1*86400 + 2*3600 + 30*60 = 95400
            ("2d1h", 176400.0),  # 2*86400 + 1*3600 = 176400
            ("30m15s", 1815.0),  # 30*60 + 15 = 1815
            ("1h0m30s", 3630.0),  # 1*3600 + 0*60 + 30 = 3630
            ("0h5m", 300.0),  # 0*3600 + 5*60 = 300
            # Case insensitive partial times
            ("1H30M", 5400.0),
            ("5M12S", 312.0),
            ("  1h30m  ", 5400.0),  # with whitespace
        ],
    )
    def test_parse_duration_to_seconds(self, input_value, expected):
        result = parse_duration_to_seconds(input_value)
        assert result == expected

    @pytest.mark.parametrize(
        "invalid_input",
        [
            "invalid",
            "10x",  # unsupported unit
            "abc123",
            "",
        ],
    )
    def test_parse_duration_to_seconds_invalid_fallback(self, invalid_input):
        # These should raise ValueError when trying to convert to float
        with pytest.raises(ValueError):
            parse_duration_to_seconds(invalid_input)

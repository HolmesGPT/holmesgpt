"""
Tests for cut CLI command parsing, validation, and stringification.

This module tests the cut CLI integration in the bash toolset, ensuring:
1. Safe cut commands are allowed and properly parsed
2. Unsafe cut commands are blocked with appropriate error messages
3. Cut command options are validated correctly
4. Commands are properly stringified back to safe command strings
"""

import pytest
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestCutCliSafeCommands:
    """Test cut CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Basic field extraction
            ("cut -f1", "cut -f1"),
            ("cut -f1,3,5", "cut -f1,3,5"),
            ("cut -f1-3", "cut -f1-3"),
            ("cut -f1,3-5,7", "cut -f1,3-5,7"),
            ("cut --fields=1,2,3", "cut --fields=1,2,3"),
            # Character and byte extraction
            ("cut -c1-10", "cut -c1-10"),
            ("cut -c1,3,5-10", "cut -c1,3,5-10"),
            ("cut --characters=1-10", "cut --characters=1-10"),
            ("cut -b1-10", "cut -b1-10"),
            ("cut --bytes=1-10", "cut --bytes=1-10"),
            # Delimiter options
            ("cut -d:", "cut -d:"),
            ("cut -d',' -f1,2", "cut -d, -f1,2"),
            ("cut --delimiter=: -f1", "cut --delimiter=: -f1"),
            ("cut -f1 --output-delimiter=|", "cut -f1 --output-delimiter=|"),
            # Additional options
            ("cut -s -f1", "cut -s -f1"),
            ("cut --only-delimited -f1", "cut --only-delimited -f1"),
            ("cut --complement -f1", "cut --complement -f1"),
            ("cut -z -f1", "cut -z -f1"),
            ("cut --zero-terminated -f1", "cut --zero-terminated -f1"),
            # Combined options
            ("cut -d: -f1,2 -s", "cut -d: -f1,2 -s"),
            (
                "cut -f1-3 --complement --only-delimited",
                "cut -f1-3 --complement --only-delimited",
            ),
            # Help and version
            ("cut --help", "cut --help"),
            ("cut --version", "cut --version"),
        ],
    )
    def test_safe_cut_commands(self, input_command, expected_output):
        """Test that safe cut commands are parsed and stringified correctly."""
        result = make_command_safe(input_command, None)
        assert result == expected_output


class TestCutCliEdgeCases:
    """Test edge cases and complex scenarios for cut CLI commands."""

    def test_cut_with_numeric_short_forms(self):
        """Test cut with numeric shorthand forms like -1, -10, etc."""
        # These are valid cut options (shorthand for -f1, -f10, etc.)
        result = make_command_safe("cut -1", None)
        assert result == "cut -1"

        result = make_command_safe("cut -1-3", None)
        assert result == "cut -1-3"

    def test_cut_with_complex_field_ranges(self):
        """Test cut with complex field range specifications."""
        result = make_command_safe("cut -f1,3-5,7,9-", None)
        assert result == "cut -f1,3-5,7,9-"

        result = make_command_safe("cut -f-3,5-", None)
        assert result == "cut -f-3,5-"

    def test_cut_with_special_delimiters(self):
        """Test cut with special delimiter characters."""
        result = make_command_safe("cut -d$'\\t' -f1", None)
        assert result == "cut -d$\\t -f1"

        result = make_command_safe("cut -d' ' -f1,2", None)
        assert result == "cut -d  -f1,2"

"""
Tests for uniq CLI command parsing, validation, and stringification.
"""

import pytest
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestUniqCliSafeCommands:
    """Test uniq CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Basic usage
            ("uniq", "uniq"),
            ("uniq -c", "uniq -c"),
            ("uniq --count", "uniq --count"),
            # Show only duplicates or unique lines
            ("uniq -d", "uniq -d"),
            ("uniq --repeated", "uniq --repeated"),
            ("uniq -D", "uniq -D"),
            ("uniq --all-repeated", "uniq --all-repeated"),
            ("uniq -u", "uniq -u"),
            ("uniq --unique", "uniq --unique"),
            # Case sensitivity
            ("uniq -i", "uniq -i"),
            ("uniq --ignore-case", "uniq --ignore-case"),
            # Field and character skipping
            ("uniq -f2", "uniq -f2"),
            ("uniq --skip-fields=2", "uniq --skip-fields=2"),
            ("uniq -s5", "uniq -s5"),
            ("uniq --skip-chars=5", "uniq --skip-chars=5"),
            ("uniq -w10", "uniq -w10"),
            ("uniq --check-chars=10", "uniq --check-chars=10"),
            # Line termination
            ("uniq -z", "uniq -z"),
            ("uniq --zero-terminated", "uniq --zero-terminated"),
            # Combined options
            ("uniq -ci", "uniq -ci"),
            ("uniq -f1 -s2 -w5", "uniq -f1 -s2 -w5"),
            (
                "uniq --count --ignore-case --repeated",
                "uniq --count --ignore-case --repeated",
            ),
            # Help and version
            ("uniq --help", "uniq --help"),
            ("uniq --version", "uniq --version"),
        ],
    )
    def test_safe_uniq_commands(self, input_command, expected_output):
        """Test that safe uniq commands are parsed and stringified correctly."""
        result = make_command_safe(input_command, None)
        assert result == expected_output

"""
Tests for head CLI command parsing, validation, and stringification.
"""

import pytest
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestHeadCliSafeCommands:
    """Test head CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Basic usage
            ("head", "head"),
            ("head -10", "head -10"),
            ("head -n 10", "head -n 10"),
            ("head --lines=10", "head --lines=10"),
            # Byte counting
            ("head -c 100", "head -c 100"),
            ("head --bytes=100", "head --bytes=100"),
            # Quiet/verbose modes
            ("head -q", "head -q"),
            ("head --quiet", "head --quiet"),
            ("head --silent", "head --silent"),
            ("head -v", "head -v"),
            ("head --verbose", "head --verbose"),
            # Zero termination
            ("head -z", "head -z"),
            ("head --zero-terminated", "head --zero-terminated"),
            # Combined options
            ("head -n 5 -v", "head -n 5 -v"),
            ("head --lines=20 --quiet", "head --lines=20 --quiet"),
            # Help and version
            ("head --help", "head --help"),
            ("head --version", "head --version"),
        ],
    )
    def test_safe_head_commands(self, input_command, expected_output):
        """Test that safe head commands are parsed and stringified correctly."""
        result = make_command_safe(input_command, None)
        assert result == expected_output

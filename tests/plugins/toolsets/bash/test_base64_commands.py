"""
Tests for base64 CLI command parsing, validation, and stringification.
"""

import pytest
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestBase64CliSafeCommands:
    """Test base64 CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Basic encoding (default)
            ("base64", "base64"),
            # Decoding
            ("base64 -d", "base64 -d"),
            ("base64 --decode", "base64 --decode"),
            # Ignore garbage characters
            ("base64 -i", "base64 -i"),
            ("base64 --ignore-garbage", "base64 --ignore-garbage"),
            # Line wrapping
            ("base64 -w 0", "base64 -w 0"),
            ("base64 --wrap=76", "base64 --wrap=76"),
            ("base64 -w 64", "base64 -w 64"),
            # Combined options
            ("base64 -d -i", "base64 -d -i"),
            ("base64 --decode --ignore-garbage", "base64 --decode --ignore-garbage"),
            # Help and version
            ("base64 --help", "base64 --help"),
            ("base64 --version", "base64 --version"),
        ],
    )
    def test_safe_base64_commands(self, input_command, expected_output):
        """Test that safe base64 commands are parsed and stringified correctly."""
        result = make_command_safe(input_command, None)
        assert result == expected_output

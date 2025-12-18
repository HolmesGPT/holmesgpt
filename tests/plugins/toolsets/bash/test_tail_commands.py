"""
Tests for tail CLI command parsing, validation, and stringification.
"""

import pytest
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestTailCliSafeCommands:
    """Test tail CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Basic usage
            ("tail", "tail"),
            ("tail -10", "tail -10"),
            ("tail -n 10", "tail -n 10"),
            ("tail --lines=10", "tail --lines=10"),
            ("tail +5", "tail '+5'"),
            ("tail -n +5", "tail -n '+5'"),
            # Byte counting
            ("tail -c 100", "tail -c 100"),
            ("tail --bytes=100", "tail --bytes=100"),
            # Follow modes
            ("tail -f", "tail -f"),
            ("tail --follow", "tail --follow"),
            ("tail -F", "tail -F"),
            ("tail --follow=name", "tail --follow=name"),
            ("tail --retry", "tail --retry"),
            # Sleep interval and PID
            ("tail -f -s 2", "tail -f -s 2"),
            ("tail --follow --sleep-interval=2", "tail --follow --sleep-interval=2"),
            ("tail -f --pid=1234", "tail -f --pid=1234"),
            # Quiet/verbose modes
            ("tail -q", "tail -q"),
            ("tail --quiet", "tail --quiet"),
            ("tail --silent", "tail --silent"),
            ("tail -v", "tail -v"),
            ("tail --verbose", "tail --verbose"),
            # Zero termination
            ("tail -z", "tail -z"),
            ("tail --zero-terminated", "tail --zero-terminated"),
            # Combined options
            ("tail -f -n 20", "tail -f -n 20"),
            (
                "tail --follow --lines=50 --sleep-interval=1",
                "tail --follow --lines=50 --sleep-interval=1",
            ),
            # Help and version
            ("tail --help", "tail --help"),
            ("tail --version", "tail --version"),
        ],
    )
    def test_safe_tail_commands(self, input_command, expected_output):
        """Test that safe tail commands are parsed and stringified correctly."""
        result = make_command_safe(input_command, None)
        assert result == expected_output

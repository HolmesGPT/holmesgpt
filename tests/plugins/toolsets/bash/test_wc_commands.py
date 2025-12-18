"""
Tests for wc CLI command parsing, validation, and stringification.
"""

import pytest
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestWcCliSafeCommands:
    """Test wc CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Basic usage
            ("wc", "wc"),
            # Count types - all options should be allowed since allowed_options is empty
            ("wc -l", "wc -l"),
            ("wc --lines", "wc --lines"),
            ("wc -w", "wc -w"),
            ("wc --words", "wc --words"),
            ("wc -c", "wc -c"),
            ("wc --bytes", "wc --bytes"),
            ("wc -m", "wc -m"),
            ("wc --chars", "wc --chars"),
            ("wc -L", "wc -L"),
            ("wc --max-line-length", "wc --max-line-length"),
            # Combined count types
            ("wc -lwc", "wc -lwc"),
            ("wc --lines --words --chars", "wc --lines --words --chars"),
            # Help and version
            ("wc --help", "wc --help"),
            ("wc --version", "wc --version"),
        ],
    )
    def test_safe_wc_commands(self, input_command, expected_output):
        """Test that safe wc commands are parsed and stringified correctly."""
        result = make_command_safe(input_command, None)
        assert result == expected_output


class TestWcCliUnsafeCommands:
    """Test wc CLI unsafe commands that should be blocked."""

    @pytest.mark.parametrize(
        "input_command,expected_error_message",
        [
            # Explicitly denied option
            (
                "wc --files0-from /dev/stdin",
                "Option --files0-from is not allowed for security reasons",
            ),
        ],
    )
    def test_unsafe_wc_commands(self, input_command, expected_error_message):
        """Test that unsafe wc commands are blocked with appropriate error messages."""
        with pytest.raises(ValueError, match=expected_error_message):
            make_command_safe(input_command, None)

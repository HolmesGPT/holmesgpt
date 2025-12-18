"""
Tests for jq CLI command parsing, validation, and stringification.
"""

import pytest
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestJqCliSafeCommands:
    """Test jq CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Basic filters
            ("jq .", "jq ."),
            ("jq '.name'", "jq .name"),
            ("jq '.items[]'", "jq '.items[]'"),
            ("jq '.[] | .name'", "jq '.[] | .name'"),
            # Output formatting
            ("jq -c .", "jq -c ."),
            ("jq --compact-output .", "jq --compact-output ."),
            ("jq -r .name", "jq -r .name"),
            ("jq --raw-output .name", "jq --raw-output .name"),
            ("jq -R .", "jq -R ."),
            ("jq --raw-input .", "jq --raw-input ."),
            # Input processing
            ("jq -s .", "jq -s ."),
            ("jq --slurp .", "jq --slurp ."),
            ("jq -n '{}'", "jq -n '{}'"),
            ("jq --null-input '{}'", "jq --null-input '{}'"),
            # Sorting and colors
            ("jq -S .", "jq -S ."),
            ("jq --sort-keys .", "jq --sort-keys ."),
            ("jq -C .", "jq -C ."),
            ("jq --color-output .", "jq --color-output ."),
            ("jq -M .", "jq -M ."),
            ("jq --monochrome-output .", "jq --monochrome-output ."),
            # Variables and arguments
            (
                "jq --arg name value '.name = $name'",
                "jq --arg name value '.name = $name'",
            ),
            (
                "jq --argjson count 42 '.count = $count'",
                "jq --argjson count 42 '.count = $count'",
            ),
            # Other options
            ("jq -a .", "jq -a ."),
            ("jq --ascii-output .", "jq --ascii-output ."),
            ("jq -j .", "jq -j ."),
            ("jq --join-output .", "jq --join-output ."),
            ("jq --tab .", "jq --tab ."),
            ("jq --unbuffered .", "jq --unbuffered ."),
            # Complex filters
            ("jq 'map(select(.active == true))'", "jq 'map(select(.active == true))'"),
            (
                "jq '.users | group_by(.department)'",
                "jq '.users | group_by(.department)'",
            ),
            # Help and version
            ("jq --help", "jq --help"),
            ("jq --version", "jq --version"),
        ],
    )
    def test_safe_jq_commands(self, input_command, expected_output):
        """Test that safe jq commands are parsed and stringified correctly."""
        result = make_command_safe(input_command, None)
        assert result == expected_output


class TestJqCliUnsafeCommands:
    """Test jq CLI unsafe commands that should be blocked."""

    @pytest.mark.parametrize(
        "input_command,expected_error_message",
        [
            # File reading options are blocked
            (
                "jq --slurpfile data.json '.'",
                "Option --slurpfile is not allowed for security reasons",
            ),
            (
                "jq --rawfile content.txt '.'",
                "Option --rawfile is not allowed for security reasons",
            ),
        ],
    )
    def test_unsafe_jq_commands(self, input_command, expected_error_message):
        """Test that unsafe jq commands are blocked with appropriate error messages."""
        with pytest.raises(ValueError, match=expected_error_message):
            make_command_safe(input_command, None)

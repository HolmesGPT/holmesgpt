"""
Tests for sed CLI command parsing, validation, and stringification.
"""

import pytest
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestSedCliSafeCommands:
    """Test sed CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Basic substitution
            ("sed 's/old/new/'", "sed s/old/new/"),
            ("sed 's/old/new/g'", "sed s/old/new/g"),
            ("sed 's|old|new|g'", "sed 's|old|new|g'"),
            ("sed 's#old#new#'", "sed 's#old#new#'"),
            # Line addressing
            ("sed '1s/old/new/'", "sed 1s/old/new/"),
            ("sed '1,10s/old/new/'", "sed 1,10s/old/new/"),
            ("sed '$s/old/new/'", "sed '$s/old/new/'"),
            ("sed '/pattern/s/old/new/'", "sed /pattern/s/old/new/"),
            # Delete operations
            ("sed '1d'", "sed 1d"),
            ("sed '/pattern/d'", "sed /pattern/d"),
            ("sed '1,5d'", "sed 1,5d"),
            # Print operations
            ("sed -n '1p'", "sed -n 1p"),
            ("sed --quiet '/pattern/p'", "sed --quiet /pattern/p"),
            ("sed --silent '1,10p'", "sed --silent 1,10p"),
            # Multiple expressions
            ("sed -e 's/old/new/' -e 's/foo/bar/'", "sed -e s/old/new/ -e s/foo/bar/"),
            (
                "sed --expression='1d' --expression='s/old/new/'",
                "sed --expression=1d --expression=s/old/new/",
            ),
            # Extended regex
            ("sed -r 's/[0-9]+/NUM/g'", "sed -r 's/[0-9]+/NUM/g'"),
            (
                "sed --regexp-extended 's/(word)/[\\1]/'",
                "sed --regexp-extended 's/(word)/[\\1]/'",
            ),
            ("sed -E 's/([a-z]+)/\\U\\1/g'", "sed -E 's/([a-z]+)/\\U\\1/g'"),
            # Line length and other options
            ("sed -l 120", "sed -l 120"),
            ("sed --line-length=80", "sed --line-length=80"),
            ("sed -c 's/old/new/'", "sed -c s/old/new/"),
            ("sed --copy 's/old/new/'", "sed --copy s/old/new/"),
            # Null data
            ("sed -z 's/old/new/'", "sed -z s/old/new/"),
            ("sed --null-data 's/old/new/'", "sed --null-data s/old/new/"),
            # Other operations
            ("sed 'y/abc/xyz/'", "sed y/abc/xyz/"),
            ("sed '1i\\\\text to insert'", "sed '1i\\\\text to insert'"),
            ("sed '1a\\\\text to append'", "sed '1a\\\\text to append'"),
            ("sed '1c\\\\replacement text'", "sed '1c\\\\replacement text'"),
            # Help and version
            ("sed --help", "sed --help"),
            ("sed --version", "sed --version"),
        ],
    )
    def test_safe_sed_commands(self, input_command, expected_output):
        """Test that safe sed commands are parsed and stringified correctly."""
        result = make_command_safe(input_command, None)
        assert result == expected_output


class TestSedCliUnsafeCommands:
    """Test sed CLI unsafe commands that should be blocked."""

    @pytest.mark.parametrize(
        "input_command",
        [
            ("sed -i 's/old/new/'"),
            ("sed --in-place 's/old/new/'"),
            # Dangerous commands in scripts
            ("sed 's/old/new/w /tmp/output'"),
            ("sed '1w /tmp/first-line'"),
            ("sed '1W /tmp/pattern-space'"),
            ("sed 'r /etc/passwd'"),
            ("sed '1R /tmp/input'"),
            ("sed 'e'"),
        ],
    )
    def test_unsafe_sed_commands(self, input_command):
        """Test that unsafe sed commands are blocked with appropriate error messages."""
        with pytest.raises(ValueError):
            make_command_safe(input_command, None)

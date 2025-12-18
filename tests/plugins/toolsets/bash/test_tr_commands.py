"""
Tests for tr CLI command parsing, validation, and stringification.
"""

import pytest
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestTrCliSafeCommands:
    """Test tr CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Character translation
            ("tr a-z A-Z", "tr a-z A-Z"),
            ("tr 'a-z' 'A-Z'", "tr a-z A-Z"),
            ("tr [:lower:] [:upper:]", "tr '[:lower:]' '[:upper:]'"),
            ("tr '0-9' '*'", "tr 0-9 '*'"),
            # Character deletion
            ("tr -d '\n'", "tr -d '\n'"),
            ("tr --delete 'aeiou'", "tr --delete aeiou"),
            ("tr -d '[:punct:]'", "tr -d '[:punct:]'"),
            # Squeeze repeating characters
            ("tr -s ' '", "tr -s ' '"),
            ("tr --squeeze-repeats ' '", "tr --squeeze-repeats ' '"),
            ("tr -s 'a-z'", "tr -s a-z"),
            # Complement sets
            ("tr -c 'a-zA-Z0-9' '_'", "tr -c a-zA-Z0-9 _"),
            ("tr --complement [:alpha:] '*'", "tr --complement '[:alpha:]' '*'"),
            # Truncate set1
            ("tr -t 'abc' '123'", "tr -t abc 123"),
            ("tr --truncate-set1 'hello' 'world'", "tr --truncate-set1 hello world"),
            # Combined options
            ("tr -ds '[:space:]' ''", "tr -ds '[:space:]' ''"),
            ("tr -cs 'a-zA-Z' '\n'", "tr -cs a-zA-Z '\n'"),
            # Help and version
            ("tr --help", "tr --help"),
            ("tr --version", "tr --version"),
        ],
    )
    def test_safe_tr_commands(self, input_command, expected_output):
        """Test that safe tr commands are parsed and stringified correctly."""
        result = make_command_safe(input_command, None)
        assert result == expected_output


class TestTrCliEdgeCases:
    """Test edge cases for tr CLI commands."""

    def test_tr_with_escape_sequences(self):
        """Test tr with common escape sequences."""
        result = make_command_safe("tr '\\n' ' '", None)
        assert result == "tr '\\n' ' '"

        result = make_command_safe("tr '\\t' ','", None)
        assert result == "tr '\\t' ,"

    def test_tr_with_character_classes(self):
        """Test tr with POSIX character classes."""
        result = make_command_safe("tr '[:digit:]' '*'", None)
        assert result == "tr '[:digit:]' '*'"

        result = make_command_safe("tr '[:space:]' '_'", None)
        assert result == "tr '[:space:]' _"

import pytest

from holmes.utils.memory_limit import (
    parse_size_to_kb,
    get_memory_limit_kb,
    get_ulimit_prefix,
    check_oom_and_append_hint,
    TOOL_MEMORY_LIMIT_ENV,
    TOOL_MEMORY_LIMIT_DEFAULT,
)


class TestParseSizeToKb:
    """Tests for the parse_size_to_kb function."""

    @pytest.mark.parametrize(
        "input_value,expected_kb",
        [
            # Gigabytes
            ("2GB", 2 * 1024 * 1024),
            ("2gb", 2 * 1024 * 1024),
            ("2Gb", 2 * 1024 * 1024),
            ("2G", 2 * 1024 * 1024),
            ("2g", 2 * 1024 * 1024),
            ("2 GB", 2 * 1024 * 1024),
            ("2 G", 2 * 1024 * 1024),
            ("1GB", 1024 * 1024),
            ("4GB", 4 * 1024 * 1024),
            # Megabytes
            ("1024MB", 1024 * 1024),
            ("1024M", 1024 * 1024),
            ("1024mb", 1024 * 1024),
            ("1024m", 1024 * 1024),
            ("512MB", 512 * 1024),
            ("512 MB", 512 * 1024),
            # Kilobytes
            ("2097152KB", 2097152),
            ("2097152K", 2097152),
            ("2097152kb", 2097152),
            ("2097152k", 2097152),
            ("1024KB", 1024),
            ("1024 KB", 1024),
            # Terabytes
            ("1TB", 1024 * 1024 * 1024),
            ("1T", 1024 * 1024 * 1024),
            ("1tb", 1024 * 1024 * 1024),
            ("2TB", 2 * 1024 * 1024 * 1024),
            # No unit (defaults to KB)
            ("2097152", 2097152),
            ("1024", 1024),
            ("512", 512),
            # Decimal values
            ("1.5GB", int(1.5 * 1024 * 1024)),
            ("1.5G", int(1.5 * 1024 * 1024)),
            ("2.5MB", int(2.5 * 1024)),
            ("0.5GB", int(0.5 * 1024 * 1024)),
            # Whitespace handling
            ("  2GB  ", 2 * 1024 * 1024),
            ("2  GB", 2 * 1024 * 1024),
        ],
    )
    def test_valid_size_strings(self, input_value: str, expected_kb: int):
        """Test parsing of valid size strings."""
        result = parse_size_to_kb(input_value)
        assert result == expected_kb

    @pytest.mark.parametrize(
        "invalid_input",
        [
            "invalid",
            "abc",
            "GB",
            "2XB",
            "2PB",  # Petabytes not supported
            "-2GB",  # Negative values
            "",
            "   ",
        ],
    )
    def test_invalid_size_strings(self, invalid_input: str):
        """Test that invalid size strings raise ValueError."""
        with pytest.raises(ValueError):
            parse_size_to_kb(invalid_input)

    def test_default_value_parses_correctly(self):
        """Test that the default value parses without error."""
        result = parse_size_to_kb(TOOL_MEMORY_LIMIT_DEFAULT)
        assert result > 0  # Just verify it parses to a positive value


class TestGetMemoryLimitKb:
    """Tests for get_memory_limit_kb function."""

    def test_returns_default_when_env_not_set(self, monkeypatch):
        """Test that default value is used when env var is not set."""
        monkeypatch.delenv(TOOL_MEMORY_LIMIT_ENV, raising=False)
        result = get_memory_limit_kb()
        assert result == parse_size_to_kb(TOOL_MEMORY_LIMIT_DEFAULT)

    def test_returns_custom_value_from_env(self, monkeypatch):
        """Test that custom value is used when env var is set."""
        monkeypatch.setenv(TOOL_MEMORY_LIMIT_ENV, "4GB")
        result = get_memory_limit_kb()
        assert result == 4 * 1024 * 1024

    def test_falls_back_to_default_on_invalid_env(self, monkeypatch):
        """Test fallback to default when env var has invalid value."""
        monkeypatch.setenv(TOOL_MEMORY_LIMIT_ENV, "invalid")
        result = get_memory_limit_kb()
        assert result == parse_size_to_kb(TOOL_MEMORY_LIMIT_DEFAULT)


class TestGetUlimitPrefix:
    """Tests for get_ulimit_prefix function."""

    def test_returns_ulimit_command_with_default(self, monkeypatch):
        """Test ulimit prefix format with default value."""
        monkeypatch.delenv(TOOL_MEMORY_LIMIT_ENV, raising=False)
        result = get_ulimit_prefix()
        expected_kb = parse_size_to_kb(TOOL_MEMORY_LIMIT_DEFAULT)
        assert result == f"ulimit -v {expected_kb} || true; "

    def test_returns_ulimit_command_with_custom_value(self, monkeypatch):
        """Test ulimit prefix format with custom value."""
        monkeypatch.setenv(TOOL_MEMORY_LIMIT_ENV, "4GB")
        result = get_ulimit_prefix()
        assert result == "ulimit -v 4194304 || true; "


class TestCheckOomAndAppendHint:
    """Tests for check_oom_and_append_hint function."""

    def test_no_hint_on_success(self):
        """Test that no hint is appended on successful command."""
        output = "command output"
        result = check_oom_and_append_hint(output, 0)
        assert result == output
        assert "[OOM]" not in result

    def test_no_hint_on_regular_error(self):
        """Test that no hint is appended on regular (non-OOM) error."""
        output = "some error occurred"
        result = check_oom_and_append_hint(output, 1)
        assert result == output
        assert "[OOM]" not in result

    @pytest.mark.parametrize(
        "return_code,output",
        [
            (137, ""),  # SIGKILL (128 + 9)
            (-9, ""),  # SIGKILL on some systems
            (0, "Killed"),  # Linux OOM killer message
            (1, "MemoryError: unable to allocate"),  # Python OOM
            (1, "Cannot allocate memory"),  # System allocation failure
            (1, "std::bad_alloc"),  # C++ allocation failure
        ],
    )
    def test_hint_appended_on_oom_indicators(self, return_code: int, output: str):
        """Test that hint is appended when OOM indicators are detected."""
        result = check_oom_and_append_hint(output, return_code)
        assert "[OOM]" in result
        assert TOOL_MEMORY_LIMIT_ENV in result
        assert TOOL_MEMORY_LIMIT_DEFAULT in result  # Shows current limit

    def test_hint_includes_current_limit(self, monkeypatch):
        """Test that hint shows the current configured limit."""
        monkeypatch.setenv(TOOL_MEMORY_LIMIT_ENV, "1GB")
        result = check_oom_and_append_hint("Killed", 137)
        assert "current limit: 1GB" in result

    def test_hint_shows_default_when_not_configured(self, monkeypatch):
        """Test that hint shows default when env var not set."""
        monkeypatch.delenv(TOOL_MEMORY_LIMIT_ENV, raising=False)
        result = check_oom_and_append_hint("Killed", 137)
        assert f"current limit: {TOOL_MEMORY_LIMIT_DEFAULT}" in result

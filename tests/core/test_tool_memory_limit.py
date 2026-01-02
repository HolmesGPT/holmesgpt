import pytest

from holmes.common.env_vars import TOOL_MEMORY_LIMIT_MB
from holmes.utils.memory_limit import (
    check_oom_and_append_hint,
    get_ulimit_prefix,
)


class TestGetUlimitPrefix:
    """Tests for get_ulimit_prefix function."""

    def test_returns_ulimit_command_with_default(self, monkeypatch):
        """Test ulimit prefix format with default value."""
        result = get_ulimit_prefix()
        expected_kb = 1024 * TOOL_MEMORY_LIMIT_MB
        assert result == f"ulimit -v {expected_kb} || true; "


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
        assert "TOOL_MEMORY_LIMIT_MB" in result
        assert str(TOOL_MEMORY_LIMIT_MB) in result  # Shows current limit

    def test_hint_shows_default_when_not_configured(self, monkeypatch):
        """Test that hint shows default when env var not set."""
        result = check_oom_and_append_hint("Killed", 137)
        assert f"current limit: {TOOL_MEMORY_LIMIT_MB}" in result

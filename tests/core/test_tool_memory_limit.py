import pytest

from holmes.common.env_vars import TOOL_MEMORY_LIMIT_MB
from holmes.utils.memory_limit import (
    check_oom_and_append_hint,
    get_ulimit_prefix,
)


class TestGetUlimitPrefix:
    """Tests for get_ulimit_prefix function."""

    def test_returns_ulimit_command_with_default(self):
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
            (1, "Killed"),  # Linux OOM killer message
            (1, "MemoryError: unable to allocate"),  # Python OOM
            (1, "Cannot allocate memory"),  # System allocation failure
            (1, "std::bad_alloc"),  # C++ allocation failure
            (
                2,
                "runtime: out of memory: cannot allocate 8388608-byte block",
            ),  # Go runtime OOM
            (2, "fatal error: out of memory"),  # Go fatal error
        ],
    )
    def test_hint_prepended_on_oom_indicators(self, return_code: int, output: str):
        """Test that hint is prepended when OOM indicators are detected."""
        result = check_oom_and_append_hint(output, return_code)
        assert "[OOM]" in result
        assert "TOOL_MEMORY_LIMIT_MB" in result
        assert str(TOOL_MEMORY_LIMIT_MB) in result  # Shows current limit
        assert result.startswith("[OOM]")  # Hint comes first

    def test_hint_prepended_before_output(self):
        """Test that hint appears before the original output, not after."""
        output = "runtime: out of memory\ngoroutine 1 [running]:\nmain.main()"
        result = check_oom_and_append_hint(output, 2)
        oom_pos = result.index("[OOM]")
        output_pos = result.index("runtime: out of memory")
        assert oom_pos < output_pos

    def test_hint_shows_default_when_not_configured(self, monkeypatch):
        """Test that hint shows default when env var not set."""
        result = check_oom_and_append_hint("Killed", 137)
        assert f"current limit: {TOOL_MEMORY_LIMIT_MB}" in result

    @pytest.mark.parametrize(
        "output",
        [
            "Pod was OOMKilled due to out of memory",
            "Container Killed by OOM killer",
            "Last State: Terminated (reason: MemoryError)",
            "Cannot allocate memory for requested operation",
        ],
    )
    def test_no_hint_on_success_with_oom_strings(self, output: str):
        """Test that no hint is appended when command succeeds but output contains OOM-like text.

        This prevents false positives when e.g. kubectl describes a pod that was OOMKilled.
        """
        result = check_oom_and_append_hint(output, 0)
        assert result == output
        assert "[OOM]" not in result

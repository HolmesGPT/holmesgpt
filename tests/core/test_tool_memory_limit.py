import pytest

from holmes.common.env_vars import TOOL_MEMORY_LIMIT_MB
from holmes.utils.memory_limit import (
    OOM_OUTPUT_MAX_LINES,
    _truncate_oom_output,
    check_oom_and_append_hint,
    get_ulimit_prefix,
)


class TestGetUlimitPrefix:
    """Tests for get_ulimit_prefix function."""

    def test_returns_ulimit_command_with_default(self):
        """Test ulimit prefix format with default value."""
        result = get_ulimit_prefix()
        expected_kb = 1024 * TOOL_MEMORY_LIMIT_MB
        assert result == f"ulimit -v {expected_kb} 2>/dev/null || true; "


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
        assert "NOT an error" in result  # Emphasizes this is by design

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
        assert f"{TOOL_MEMORY_LIMIT_MB} MB" in result

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

    def test_large_go_stack_trace_is_truncated(self):
        """Test that Go runtime OOM stack traces (goroutine dumps) are truncated to save tokens."""
        goroutine_lines = [
            "runtime: out of memory: cannot allocate 4194304-byte block (66453504 in use)",
            "fatal error: out of memory",
            "",
            "goroutine 1 gp=0xc000002380 m=6 mp=0xc0002e4808 [running]:",
            "runtime.throw({0x247d3ca?, 0xc0002e4808?})",
            "\truntime/panic.go:1101 +0x48 fp=0xc00166c4b0 sp=0xc00166c480 pc=0x4780e8",
        ]
        # Add many goroutine stack lines to simulate a real crash
        for i in range(200):
            goroutine_lines.append(f"goroutine {i+2} gp=0x{i:08x} m=nil [GC worker (idle)]:")
            goroutine_lines.append(f"runtime.gopark(0x{i:08x}?, 0x0?, 0x0?, 0x0?, 0x0?)")
            goroutine_lines.append(f"\truntime/proc.go:435 +0xce fp=0x{i:08x} sp=0x{i:08x}")

        output = "\n".join(goroutine_lines)
        result = check_oom_and_append_hint(output, 2)

        assert "[OOM]" in result
        # The original 600+ line output should be truncated
        assert "lines of stack trace omitted" in result
        # Only the hint + truncated output should remain
        result_lines = result.splitlines()
        # Hint is a few lines + OOM_OUTPUT_MAX_LINES from output + 1 omission marker
        assert len(result_lines) < 25  # Much less than original 600+

    def test_short_oom_output_not_truncated(self):
        """Test that short OOM output (within limit) is not truncated."""
        output = "runtime: out of memory\nfatal error: out of memory"
        result = check_oom_and_append_hint(output, 2)
        assert "[OOM]" in result
        assert "lines of stack trace omitted" not in result
        assert "runtime: out of memory" in result
        assert "fatal error: out of memory" in result


class TestTruncateOomOutput:
    """Tests for _truncate_oom_output function."""

    def test_empty_output(self):
        assert _truncate_oom_output("") == ""

    def test_short_output_unchanged(self):
        output = "line 1\nline 2\nline 3"
        assert _truncate_oom_output(output) == output

    def test_output_at_limit_unchanged(self):
        lines = [f"line {i}" for i in range(OOM_OUTPUT_MAX_LINES)]
        output = "\n".join(lines)
        assert _truncate_oom_output(output) == output

    def test_output_over_limit_truncated(self):
        total_lines = 100
        lines = [f"line {i}" for i in range(total_lines)]
        output = "\n".join(lines)
        result = _truncate_oom_output(output)

        result_lines = result.splitlines()
        assert len(result_lines) == OOM_OUTPUT_MAX_LINES + 1  # +1 for omission marker
        assert result_lines[0] == "line 0"
        assert result_lines[OOM_OUTPUT_MAX_LINES - 1] == f"line {OOM_OUTPUT_MAX_LINES - 1}"
        omitted = total_lines - OOM_OUTPUT_MAX_LINES
        assert f"[... {omitted} lines of stack trace omitted ...]" in result_lines[-1]

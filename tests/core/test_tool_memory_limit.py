import subprocess
import sys

import pytest

from holmes.common.env_vars import (
    TOOL_MEMORY_LIMIT_MB,
    TOOL_VIRTUAL_MEMORY_HEADROOM_MB,
)
from holmes.utils import memory_limit as ml
from holmes.utils.memory_limit import (
    OOM_OUTPUT_MAX_LINES,
    _sample_tree_rss_mb,
    _truncate_oom_output,
    append_output_truncated_hint,
    check_oom_and_append_hint,
    get_ulimit_prefix,
    read_process_output_capped,
)


class TestGetUlimitPrefix:
    """Tests for get_ulimit_prefix function."""

    def test_includes_virtual_headroom(self):
        """The ulimit -v backstop = resident budget + virtual headroom."""
        result = get_ulimit_prefix()
        expected_kb = 1024 * (TOOL_MEMORY_LIMIT_MB + TOOL_VIRTUAL_MEMORY_HEADROOM_MB)
        assert result == f"ulimit -v {expected_kb} 2>/dev/null || true; "

    def test_disabled_when_headroom_non_positive(self, monkeypatch):
        """Headroom <= 0 disables the ulimit -v backstop (RSS poll is sole enforcer)."""
        monkeypatch.setattr(ml, "TOOL_VIRTUAL_MEMORY_HEADROOM_MB", 0)
        assert get_ulimit_prefix() == ""


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
        assert (
            "https://holmesgpt.dev/data-sources/tool-execution-safety/#when-to-raise-the-limit"
            in result
        )  # Includes docs link

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


class TestAppendOutputTruncatedHint:
    """Tests for append_output_truncated_hint function."""

    def test_hint_prepended_before_output(self):
        result = append_output_truncated_hint("some large output")
        assert result.startswith("[OUTPUT TRUNCATED]")
        assert "some large output" in result
        assert result.index("[OUTPUT TRUNCATED]") < result.index("some large output")

    def test_hint_mentions_env_var_and_not_an_error(self):
        result = append_output_truncated_hint("data")
        assert "TOOL_MAX_OUTPUT_LENGTH" in result
        assert "NOT an error" in result

    def test_empty_output_returns_hint_only(self):
        result = append_output_truncated_hint("")
        assert result.startswith("[OUTPUT TRUNCATED]")


def _popen(script: str, shell: bool = False) -> subprocess.Popen:
    """Launch a helper process in its own session so the whole tree is killable."""
    if shell:
        args: object = script
    else:
        args = [sys.executable, "-c", script]
    return subprocess.Popen(
        args,
        shell=shell,
        executable="/bin/bash" if shell else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


class TestReadProcessOutputCapped:
    """Tests for read_process_output_capped function."""

    def test_small_output_not_truncated(self):
        process = _popen("print('hello world')")
        output, timed_out, truncated, mem_killed = read_process_output_capped(
            process, max_rss_mb=0
        )
        assert output.strip() == "hello world"
        assert timed_out is False
        assert truncated is False
        assert mem_killed is False
        assert process.returncode == 0

    def test_output_capped_at_max_chars(self):
        # Emit far more than the cap; ensure we keep exactly the cap and flag it.
        process = _popen("import sys; sys.stdout.write('a' * 1000000)")
        output, timed_out, truncated, mem_killed = read_process_output_capped(
            process, max_output_chars=1000, max_rss_mb=0
        )
        assert truncated is True
        assert timed_out is False
        assert len(output) == 1000
        assert set(output) == {"a"}

    def test_cap_disabled_with_zero(self):
        process = _popen("import sys; sys.stdout.write('a' * 50000)")
        output, timed_out, truncated, mem_killed = read_process_output_capped(
            process, max_output_chars=0, max_rss_mb=0
        )
        assert truncated is False
        assert len(output) == 50000

    def test_timeout_kills_process(self):
        process = _popen("import time; time.sleep(30); print('done')")
        output, timed_out, truncated, mem_killed = read_process_output_capped(
            process, timeout=1, max_rss_mb=0
        )
        assert timed_out is True
        assert "done" not in output
        # Process must have been killed (reaped), not left running.
        assert process.returncode is not None

    def test_truncation_kills_process_and_reaps(self):
        # A process that would keep writing forever must be killed once capped.
        process = _popen(
            "import sys\nwhile True:\n    sys.stdout.write('x' * 4096)"
        )
        output, timed_out, truncated, mem_killed = read_process_output_capped(
            process, max_output_chars=8192, max_rss_mb=0
        )
        assert truncated is True
        assert len(output) == 8192
        assert process.returncode is not None

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="RSS polling requires /proc (Linux only)",
    )
    def test_rss_budget_kills_runaway(self):
        # Allocate ~200MB of resident memory and hold it; a 50MB budget must
        # kill the process before it finishes.
        process = _popen(
            "import sys, time\n"
            "buf = bytearray(200 * 1024 * 1024)\n"
            "for i in range(0, len(buf), 4096): buf[i] = 1\n"  # fault pages -> resident
            "sys.stdout.write('done'); sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        output, timed_out, truncated, mem_killed = read_process_output_capped(
            process, timeout=20, max_rss_mb=50
        )
        assert mem_killed is True
        assert timed_out is False
        assert process.returncode is not None

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="RSS polling requires /proc (Linux only)",
    )
    def test_rss_budget_allows_within_budget(self):
        process = _popen("print('small output')")
        output, timed_out, truncated, mem_killed = read_process_output_capped(
            process, max_rss_mb=500
        )
        assert mem_killed is False
        assert output.strip() == "small output"

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="process-group kill + /proc are Linux only",
    )
    def test_kills_grandchild_process(self, tmp_path):
        # A bash parent that spawns a long-lived grandchild which writes its PID
        # to a file. On timeout the whole group must die (not just bash), so the
        # grandchild must be gone afterwards.
        import os
        import time

        pidfile = tmp_path / "grandchild.pid"
        # bash -> python grandchild that records its pid then sleeps forever
        script = (
            f"{sys.executable} -c "
            f"'import os,time; open(\"{pidfile}\",\"w\").write(str(os.getpid())); "
            f"time.sleep(120)' & wait"
        )
        process = _popen(script, shell=True)
        read_process_output_capped(process, timeout=2, max_rss_mb=0)

        # Wait for the pidfile then confirm the grandchild is dead.
        for _ in range(50):
            if pidfile.exists():
                break
            time.sleep(0.05)
        assert pidfile.exists(), "grandchild never started"
        gc_pid = int(pidfile.read_text().strip())

        alive = True
        for _ in range(40):
            try:
                os.kill(gc_pid, 0)
            except ProcessLookupError:
                alive = False
                break
            time.sleep(0.05)
        assert alive is False, f"grandchild {gc_pid} survived process-group kill"


class TestSampleTreeRssMb:
    """Tests for _sample_tree_rss_mb."""

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="requires /proc"
    )
    def test_returns_positive_for_current_process(self):
        import os

        rss = _sample_tree_rss_mb(os.getpid())
        assert rss is not None
        assert rss > 0

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="requires /proc"
    )
    def test_missing_pid_does_not_raise(self):
        # A pid that does not exist yields 0 (no matching /proc entry), not an error.
        rss = _sample_tree_rss_mb(2**22)
        assert rss == 0

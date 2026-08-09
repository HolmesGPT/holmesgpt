"""
Tests for the subprocess timeout added to Tool.__execute_subprocess().

Regression coverage for: a script/command tool (e.g. kubernetes/core's
kubectl-based tools) whose underlying subprocess stalls forever (for example
an apiserver connection that accepts the TCP handshake but never responds)
used to block the calling thread indefinitely and leak the child process.
`__execute_subprocess()` now bounds every run with `TOOL_SUBPROCESS_TIMEOUT_SECONDS`
and kills the whole process group on timeout, not just the immediate shell
child, so any grandchild process (e.g. `kubectl`) spawned by the command is
also reaped.
"""

import os
import time
from unittest.mock import patch

import pytest

from holmes.common.env_vars import TOOL_SUBPROCESS_TIMEOUT_SECONDS
from holmes.core.tools import StructuredToolResultStatus, YAMLTool
from tests.conftest import create_mock_tool_invoke_context


def _make_tool(command: str) -> YAMLTool:
    return YAMLTool(name="test-tool", description="test tool", command=command)


class TestSubprocessTimeoutConfig:
    def test_default_timeout_is_positive(self):
        assert TOOL_SUBPROCESS_TIMEOUT_SECONDS > 0


class TestExecuteSubprocessTimeout:
    def test_fast_command_completes_normally(self):
        tool = _make_tool("echo hello")
        context = create_mock_tool_invoke_context()

        result = tool._invoke(params={}, context=context)

        assert result.return_code == 0
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "hello" in result.data

    def test_slow_command_is_killed_after_timeout(self, monkeypatch):
        monkeypatch.setattr("holmes.core.tools.TOOL_SUBPROCESS_TIMEOUT_SECONDS", 0.5)
        tool = _make_tool("sleep 30")
        context = create_mock_tool_invoke_context()

        start = time.time()
        result = tool._invoke(params={}, context=context)
        elapsed = time.time() - start

        # Must not have blocked anywhere near the full 30s sleep.
        assert elapsed < 10
        assert result.return_code == 124
        assert result.status == StructuredToolResultStatus.ERROR
        assert "timed out" in result.data.lower()

    def test_partial_output_before_timeout_is_preserved(self, monkeypatch):
        monkeypatch.setattr("holmes.core.tools.TOOL_SUBPROCESS_TIMEOUT_SECONDS", 0.5)
        tool = _make_tool("echo partial-output; sleep 30")
        context = create_mock_tool_invoke_context()

        result = tool._invoke(params={}, context=context)

        assert result.return_code == 124
        assert "partial-output" in result.data
        assert "timed out" in result.data.lower()

    def test_grandchild_process_is_also_killed(self, tmp_path, monkeypatch):
        """Regression test for the reported bug: killing only the direct shell
        child (as plain subprocess.run(timeout=...) + .kill() would do) leaves
        a backgrounded grandchild (e.g. a stalled kubectl call) running
        forever. The whole process group must be killed."""
        monkeypatch.setattr("holmes.core.tools.TOOL_SUBPROCESS_TIMEOUT_SECONDS", 0.5)
        pid_file = tmp_path / "grandchild.pid"
        command = f"sleep 30 & echo $! > {pid_file}; wait"
        tool = _make_tool(command)
        context = create_mock_tool_invoke_context()

        result = tool._invoke(params={}, context=context)
        assert result.return_code == 124

        # Give the kernel a brief moment to finish delivering SIGKILL.
        deadline = time.time() + 2
        grandchild_pid = int(pid_file.read_text().strip())
        alive = True
        while time.time() < deadline:
            try:
                os.kill(grandchild_pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
                break
            time.sleep(0.1)
        assert (
            not alive
        ), f"grandchild process {grandchild_pid} survived the timeout kill"

    def test_kill_race_condition_does_not_raise(self, monkeypatch):
        """If the process happens to exit on its own right before we signal
        it, os.killpg raises ProcessLookupError — this must be swallowed, not
        propagated as an unhandled exception."""
        monkeypatch.setattr("holmes.core.tools.TOOL_SUBPROCESS_TIMEOUT_SECONDS", 0.3)
        tool = _make_tool("sleep 30")
        context = create_mock_tool_invoke_context()

        with patch("os.killpg", side_effect=ProcessLookupError()):
            result = tool._invoke(params={}, context=context)

        assert result.return_code == 124
        assert "timed out" in result.data.lower()

    @pytest.mark.parametrize("command", ["sleep 30"])
    def test_timeout_does_not_raise_unexpected_exception(self, monkeypatch, command):
        monkeypatch.setattr("holmes.core.tools.TOOL_SUBPROCESS_TIMEOUT_SECONDS", 0.3)
        tool = _make_tool(command)
        context = create_mock_tool_invoke_context()

        # Should never raise — the caller always gets back a StructuredToolResult.
        result = tool._invoke(params={}, context=context)
        assert result.return_code == 124

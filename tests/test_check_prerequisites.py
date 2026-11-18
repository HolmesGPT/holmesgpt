# type: ignore
import os
import subprocess
import sys
import time
from typing import Any, Dict, List
from unittest.mock import Mock, call, patch

import pytest

from holmes.common.env_vars import DEFAULT_TOOLSET_PREREQ_TIMEOUT_SECONDS
from holmes.core.tools import (
    CallablePrerequisite,
    StaticPrerequisite,
    StructuredToolResult,
    Tool,
    Toolset,
    ToolsetCommandPrerequisite,
    ToolsetEnvironmentPrerequisite,
    ToolsetStatusEnum,
    run_prerequisite_command,
)
from tests.utils.toolsets import (
    callable_failure_no_message,
    callable_failure_with_message,
    callable_success,
    failing_callable_for_test,
)


class DummyTool(Tool):
    name: str = "dummy_tool"
    description: str = "A dummy tool"

    def _invoke(
        self, params: dict, user_approved: bool = False
    ) -> StructuredToolResult:
        pass

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return ""


class SampleToolset(Toolset):
    name: str = "sample_toolset"
    description: str = "A sample toolset for testing"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tools: List[Tool] = [DummyTool()]


def test_check_prerequisites_none():
    toolset = SampleToolset(prerequisites=[])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.ENABLED
    assert toolset.error is None


def test_check_prerequisites_static_enabled():
    prereq = StaticPrerequisite(enabled=True, disabled_reason="Should not be used")
    toolset = SampleToolset(prerequisites=[prereq])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.ENABLED
    assert toolset.error is None


def test_check_prerequisites_static_disabled():
    reason = "Feature is turned off"
    prereq = StaticPrerequisite(enabled=False, disabled_reason=reason)
    toolset = SampleToolset(prerequisites=[prereq])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert toolset.error == reason


@patch("holmes.core.tools.run_prerequisite_command")
def test_check_prerequisites_command_success(mock_subprocess_run):
    mock_subprocess_run.return_value = Mock(stdout="expected output", returncode=0)
    prereq = ToolsetCommandPrerequisite(
        command="my_command", expected_output="expected output"
    )
    toolset = SampleToolset(prerequisites=[prereq])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.ENABLED
    assert toolset.error is None
    mock_subprocess_run.assert_called_once_with(
        "my_command",
        timeout=DEFAULT_TOOLSET_PREREQ_TIMEOUT_SECONDS,
    )


@patch("holmes.core.tools.run_prerequisite_command")
def test_check_prerequisites_command_output_mismatch(mock_subprocess_run):
    mock_subprocess_run.return_value = Mock(stdout="actual output", returncode=0)
    prereq = ToolsetCommandPrerequisite(
        command="my_command", expected_output="expected output"
    )
    toolset = SampleToolset(prerequisites=[prereq])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert "did not include `expected output`" in toolset.error


@patch("holmes.core.tools.run_prerequisite_command")
def test_check_prerequisites_command_failure(mock_subprocess_run):
    mock_subprocess_run.side_effect = subprocess.CalledProcessError(
        1, "my_command", "error output"
    )
    prereq = ToolsetCommandPrerequisite(command="my_command")
    toolset = SampleToolset(prerequisites=[prereq])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED


@patch("holmes.core.tools.run_prerequisite_command")
def test_check_prerequisites_command_timeout(mock_subprocess_run):
    mock_subprocess_run.side_effect = subprocess.TimeoutExpired(
        cmd="argocd version", timeout=20.0
    )
    prereq = ToolsetCommandPrerequisite(command="argocd version")
    toolset = SampleToolset(prerequisites=[prereq])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert toolset.error == "`argocd version` timed out after 20 seconds"


@patch("holmes.core.tools.run_prerequisite_command")
def test_check_prerequisites_command_timeout_uses_env_override(mock_subprocess_run):
    mock_subprocess_run.return_value = Mock(stdout="output", returncode=0)
    with patch.dict(os.environ, {"HOLMES_TOOLSET_PREREQ_TIMEOUT_SECONDS": "3.5"}):
        toolset = SampleToolset(
            prerequisites=[ToolsetCommandPrerequisite(command="my_command")]
        )
        toolset.check_prerequisites()
    assert mock_subprocess_run.call_args.kwargs["timeout"] == 3.5


@patch("holmes.core.tools.run_prerequisite_command")
def test_check_prerequisites_command_timeout_does_not_leak_interpolated_secret(
    mock_subprocess_run,
):
    mock_subprocess_run.side_effect = subprocess.TimeoutExpired(
        cmd="curl -H 'Authorization: s3cr3t'", timeout=20.0
    )
    with patch.dict(os.environ, {"MY_TOKEN": "s3cr3t"}, clear=True):
        prereq = ToolsetCommandPrerequisite(
            command="curl -H 'Authorization: $MY_TOKEN'"
        )
        toolset = SampleToolset(prerequisites=[prereq])
        toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert "s3cr3t" not in toolset.error
    assert "$MY_TOKEN" in toolset.error


def test_check_prerequisites_hung_command_is_killed():
    hang = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    with patch.dict(os.environ, {"HOLMES_TOOLSET_PREREQ_TIMEOUT_SECONDS": "1"}):
        toolset = SampleToolset(prerequisites=[ToolsetCommandPrerequisite(command=hang)])
        start = time.monotonic()
        toolset.check_prerequisites()
        elapsed = time.monotonic() - start
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert "timed out after 1 seconds" in toolset.error
    assert elapsed < 15


def test_run_prerequisite_command_returns_output():
    result = run_prerequisite_command(
        f'"{sys.executable}" -c "print(\'hello\')"', timeout=30
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_run_prerequisite_command_raises_on_non_zero_exit():
    command = (
        f'"{sys.executable}" -c "import sys; sys.stderr.write(\'boom\'); sys.exit(3)"'
    )
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        run_prerequisite_command(command, timeout=30)
    assert exc_info.value.returncode == 3
    assert "boom" in exc_info.value.stderr


def _write_heartbeat_child(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    child = tmp_path / "child.py"
    child.write_text(
        "import sys, time\n"
        "start = time.monotonic()\n"
        "count = 0\n"
        "while time.monotonic() - start < 60:\n"
        "    count += 1\n"
        "    with open(sys.argv[1], 'w') as f:\n"
        "        f.write(str(count))\n"
        "    time.sleep(0.1)\n"
    )
    return f'"{sys.executable}" "{child}" "{heartbeat}"', heartbeat


def _assert_timeout_kills_heartbeat_child(command, heartbeat):
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_prerequisite_command(command, timeout=2)
    elapsed = time.monotonic() - start

    assert heartbeat.exists(), "child process never started"
    last_beat = heartbeat.read_text()
    time.sleep(1)
    assert heartbeat.read_text() == last_beat, "child process still running after timeout"
    assert elapsed < 15


def test_run_prerequisite_command_timeout_kills_child_processes(tmp_path):
    child_command, heartbeat = _write_heartbeat_child(tmp_path)
    command = f'{child_command} && "{sys.executable}" -c "pass"'
    _assert_timeout_kills_heartbeat_child(command, heartbeat)


def test_run_prerequisite_command_timeout_kills_children_after_shell_exits(tmp_path):
    child_command, heartbeat = _write_heartbeat_child(tmp_path)
    if sys.platform == "win32":
        command = f'start "" /b {child_command}'
    else:
        command = f"{child_command} &"
    _assert_timeout_kills_heartbeat_child(command, heartbeat)


def test_run_prerequisite_command_kills_child_processes_on_keyboard_interrupt(tmp_path):
    child_command, heartbeat = _write_heartbeat_child(tmp_path)
    real_communicate = subprocess.Popen.communicate
    calls = {"count": 0}

    def interrupt_first_wait(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            deadline = time.monotonic() + 10
            while not heartbeat.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            raise KeyboardInterrupt
        return real_communicate(self, *args, **kwargs)

    with patch.object(subprocess.Popen, "communicate", interrupt_first_wait):
        with pytest.raises(KeyboardInterrupt):
            run_prerequisite_command(child_command, timeout=30)

    assert heartbeat.exists(), "child process never started"
    last_beat = heartbeat.read_text()
    time.sleep(1)
    assert heartbeat.read_text() == last_beat, "child process still running after interrupt"


UNDECODABLE_OUTPUT_COMMAND = (
    f'"{sys.executable}" -c "import sys; sys.stdout.buffer.write(bytes([0x81, 0xff])); '
    'sys.stdout.flush()"'
)


def test_run_prerequisite_command_replaces_undecodable_output():
    result = run_prerequisite_command(UNDECODABLE_OUTPUT_COMMAND, timeout=30)
    assert result.returncode == 0
    assert "�" in result.stdout


def test_run_prerequisite_command_timeout_with_undecodable_output_still_times_out():
    command = (
        f'"{sys.executable}" -c "import sys, time; '
        "sys.stdout.buffer.write(bytes([0x81, 0xff])); sys.stdout.flush(); "
        'time.sleep(60)"'
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_prerequisite_command(command, timeout=1)


@patch.dict(os.environ, {"EXISTING_VAR": "value"}, clear=True)
def test_check_prerequisites_env_var_exists():
    prereq = ToolsetEnvironmentPrerequisite(env=["EXISTING_VAR"])
    toolset = SampleToolset(prerequisites=[prereq])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.ENABLED
    assert toolset.error is None


@patch.dict(os.environ, {}, clear=True)
def test_check_prerequisites_env_var_missing():
    prereq = ToolsetEnvironmentPrerequisite(env=["MISSING_VAR"])
    toolset = SampleToolset(prerequisites=[prereq])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert toolset.error == "Environment variable MISSING_VAR was not set"


def test_check_prerequisites_callable_success():
    prereq = CallablePrerequisite(callable=callable_success)
    toolset = SampleToolset(prerequisites=[prereq], config={"key": "value"})
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.ENABLED
    assert toolset.error is None


def test_check_prerequisites_callable_failure_with_message():
    prereq = CallablePrerequisite(callable=callable_failure_with_message)
    toolset = SampleToolset(prerequisites=[prereq], config={})
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert toolset.error == "Callable check failed"


def test_check_prerequisites_callable_failure_no_message():
    prereq = CallablePrerequisite(callable=callable_failure_no_message)
    toolset = SampleToolset(prerequisites=[prereq])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert toolset.error is None


@patch("holmes.core.tools.run_prerequisite_command")
@patch.dict(os.environ, {"EXISTING_VAR": "value"}, clear=True)
def test_check_prerequisites_multiple_success(mock_subprocess_run):
    mock_subprocess_run.return_value = Mock(stdout="output", returncode=0)
    prerequisites = [
        StaticPrerequisite(enabled=True, disabled_reason=""),
        ToolsetCommandPrerequisite(command="cmd1"),
        ToolsetEnvironmentPrerequisite(env=["EXISTING_VAR"]),
        CallablePrerequisite(callable=callable_success),
    ]
    toolset = SampleToolset(prerequisites=prerequisites, config={})
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.ENABLED
    assert toolset.error is None
    assert mock_subprocess_run.call_count == 1  # Ensure command was run


@patch("holmes.core.tools.run_prerequisite_command")
def test_check_prerequisites_command_uses_interpolate_command(mock_subprocess_run):
    mock_subprocess_run.return_value = Mock(stdout="output", returncode=0)
    with patch.dict(os.environ, {"MY_VAR": "interpolated_value"}, clear=True):
        prereq = ToolsetCommandPrerequisite(command="echo $MY_VAR")
        toolset = SampleToolset(prerequisites=[prereq])
        toolset.check_prerequisites()
        mock_subprocess_run.assert_called_once_with(
            "echo interpolated_value",
            timeout=DEFAULT_TOOLSET_PREREQ_TIMEOUT_SECONDS,
        )
    assert toolset.status == ToolsetStatusEnum.ENABLED


@patch("holmes.core.tools.run_prerequisite_command")
@patch.dict(os.environ, {"EXISTING_VAR": "value"}, clear=True)
def test_check_prerequisites_multiple_all_types_success(mock_subprocess_run):
    mock_subprocess_run.return_value = Mock(stdout="output", returncode=0)
    mock_callable_success = Mock(return_value=(True, ""))
    second_mock_callable_success = Mock(return_value=(True, ""))

    prerequisites = [
        StaticPrerequisite(enabled=True, disabled_reason="Should not be used"),
        ToolsetEnvironmentPrerequisite(env=["EXISTING_VAR"]),
        ToolsetCommandPrerequisite(command="cmd1", expected_output="output"),
        ToolsetCommandPrerequisite(command="cmd2", expected_output="output"),
        CallablePrerequisite(callable=mock_callable_success),
        CallablePrerequisite(callable=second_mock_callable_success),
        ToolsetCommandPrerequisite(command="cmd3", expected_output="output"),
    ]
    toolset = SampleToolset(
        prerequisites=prerequisites, config={"some_config": "value"}
    )
    toolset.check_prerequisites()

    assert toolset.status == ToolsetStatusEnum.ENABLED
    assert toolset.error is None
    assert mock_subprocess_run.call_count == 3
    expected_subprocess_calls = [
        call(
            "cmd1",
            timeout=DEFAULT_TOOLSET_PREREQ_TIMEOUT_SECONDS,
        ),
        call(
            "cmd2",
            timeout=DEFAULT_TOOLSET_PREREQ_TIMEOUT_SECONDS,
        ),
        call(
            "cmd3",
            timeout=DEFAULT_TOOLSET_PREREQ_TIMEOUT_SECONDS,
        ),
    ]
    mock_subprocess_run.assert_has_calls(expected_subprocess_calls, any_order=False)
    mock_callable_success.assert_called_once_with({"some_config": "value"})
    second_mock_callable_success.assert_called_once_with({"some_config": "value"})


@patch("holmes.core.tools.run_prerequisite_command")
def test_check_prerequisites_stops_at_first_failure_command(mock_subprocess_run):
    # Track which commands were executed
    executed_commands = []

    def track_and_return(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [None])[0]
        executed_commands.append(cmd)

        if cmd == "first_command":
            return Mock(stdout="success", returncode=0)
        elif cmd == "second_command_fails":
            raise subprocess.CalledProcessError(1, cmd, "command failed")
        elif cmd == "third_command_should_not_run":
            return Mock(stdout="should not see this", returncode=0)
        else:
            return Mock(stdout="", returncode=0)

    mock_subprocess_run.side_effect = track_and_return

    prerequisites = [
        ToolsetCommandPrerequisite(command="first_command"),  # Should succeed
        ToolsetCommandPrerequisite(command="second_command_fails"),  # Should fail
        ToolsetCommandPrerequisite(
            command="third_command_should_not_run"
        ),  # Should not be called
    ]

    toolset = SampleToolset(prerequisites=prerequisites)
    toolset.check_prerequisites()

    assert toolset.status == ToolsetStatusEnum.FAILED
    assert toolset.error == "`second_command_fails` failed with exit code 1"

    # Verify execution order
    assert executed_commands == ["first_command", "second_command_fails"]
    # Third command should NOT have been executed
    assert "third_command_should_not_run" not in executed_commands


def test_check_prerequisites_stops_at_first_failure_callable():
    mock_callable_1 = Mock(return_value=(True, ""))
    mock_callable_2_fails = Mock(return_value=(False, "This callable failed"))
    mock_callable_3_should_not_be_called = Mock(return_value=(True, ""))

    prerequisites = [
        CallablePrerequisite(callable=mock_callable_1),  # Should succeed
        CallablePrerequisite(callable=mock_callable_2_fails),  # Should fail
        CallablePrerequisite(
            callable=mock_callable_3_should_not_be_called
        ),  # Should not be called
    ]
    toolset = SampleToolset(prerequisites=prerequisites, config={"test": "config"})
    toolset.check_prerequisites()

    assert toolset.status == ToolsetStatusEnum.FAILED
    assert toolset.error == "This callable failed"

    # Verify execution order
    mock_callable_1.assert_called_once_with({"test": "config"})
    mock_callable_2_fails.assert_called_once_with({"test": "config"})
    mock_callable_3_should_not_be_called.assert_not_called()


def test_check_prerequisites_with_failing_callable():
    failing_prereq = CallablePrerequisite(callable=failing_callable_for_test)

    toolset = SampleToolset(
        name="failing-callable-toolset", prerequisites=[failing_prereq], config={}
    )

    toolset.check_prerequisites()

    assert toolset.status == ToolsetStatusEnum.FAILED
    assert toolset.error is not None
    expected_error_message = (
        "Prerequisite call failed unexpectedly: Failure in callable prerequisite"
    )
    assert toolset.error == expected_error_message


@patch.dict(os.environ, {"TEST_ENV": "exists"}, clear=False)
def test_check_prerequisites_env_before_command():
    """Test that environment prerequisites are checked before command prerequisites"""

    # Track what gets executed
    executed = []

    # Mock that tracks when env is checked
    class EnvTracker:
        def __init__(self):
            self.original_in = os.environ.__contains__

        def __call__(self, key):
            if key == "TEST_ENV":
                if "env_check" not in executed:
                    executed.append("env_check")
            return self.original_in(key)

    # Mock that tracks command execution
    def mock_run(*args, **kwargs):
        executed.append("command_check")
        return Mock(stdout="", returncode=0)

    prerequisites = [
        ToolsetCommandPrerequisite(command="test_cmd"),  # This is listed first
        ToolsetEnvironmentPrerequisite(
            env=["TEST_ENV"]
        ),  # But this should run first due to sorting
    ]

    toolset = SampleToolset(prerequisites=prerequisites, config={})

    # Patch both env check and subprocess
    env_tracker = EnvTracker()

    with patch.object(os.environ.__class__, "__contains__", env_tracker):
        with patch("holmes.core.tools.run_prerequisite_command", side_effect=mock_run):
            toolset.check_prerequisites()

    # Verify env was checked before command
    assert executed == ["env_check", "command_check"]
    assert toolset.status == ToolsetStatusEnum.ENABLED


def test_check_prerequisites_static_checked_first():
    """Test that static prerequisites are checked first and can short-circuit"""

    # Create mocks that should NOT be called
    mock_callable_should_not_run = Mock(return_value=(True, ""))

    # Define prerequisites with static failure in the middle
    prerequisites = [
        ToolsetCommandPrerequisite(command="should_not_run"),
        CallablePrerequisite(callable=mock_callable_should_not_run),
        StaticPrerequisite(enabled=False, disabled_reason="Static check failed"),
        ToolsetEnvironmentPrerequisite(env=["SOME_VAR"]),
    ]

    with patch("holmes.core.tools.run_prerequisite_command") as mock_run:
        toolset = SampleToolset(prerequisites=prerequisites, config={})
        toolset.check_prerequisites()

    # Verify static prerequisite failed first
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert toolset.error == "Static check failed"

    # Verify nothing else was executed
    mock_callable_should_not_run.assert_not_called()
    mock_run.assert_not_called()


# ============================================================================
# Lazy initialization tests
# ============================================================================


class TestCheckConfigPrerequisites:
    """Tests for check_config_prerequisites - validates only fast config checks."""

    def test_no_prerequisites(self):
        """Toolset with no prerequisites is fully initialized immediately."""
        toolset = SampleToolset(prerequisites=[])
        toolset.check_config_prerequisites()
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset._initialized is True
        assert toolset._lazy_init is False
        assert not toolset.needs_initialization

    def test_static_enabled(self):
        """Static prerequisite passes: no deferred prereqs means fully initialized."""
        prereq = StaticPrerequisite(enabled=True, disabled_reason="")
        toolset = SampleToolset(prerequisites=[prereq])
        toolset.check_config_prerequisites()
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset._initialized is True
        assert not toolset.needs_initialization

    def test_static_disabled(self):
        """Static prerequisite fails: toolset should be FAILED, not lazy."""
        prereq = StaticPrerequisite(enabled=False, disabled_reason="Feature off")
        toolset = SampleToolset(prerequisites=[prereq])
        toolset.check_config_prerequisites()
        assert toolset.status == ToolsetStatusEnum.FAILED
        assert toolset.error == "Feature off"

    @patch.dict(os.environ, {"MY_VAR": "value"}, clear=True)
    def test_env_var_present(self):
        """Env var present: no deferred prereqs means fully initialized."""
        prereq = ToolsetEnvironmentPrerequisite(env=["MY_VAR"])
        toolset = SampleToolset(prerequisites=[prereq])
        toolset.check_config_prerequisites()
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset._initialized is True

    @patch.dict(os.environ, {}, clear=True)
    def test_env_var_missing(self):
        """Env var missing: toolset should be FAILED."""
        prereq = ToolsetEnvironmentPrerequisite(env=["MISSING_VAR"])
        toolset = SampleToolset(prerequisites=[prereq])
        toolset.check_config_prerequisites()
        assert toolset.status == ToolsetStatusEnum.FAILED
        assert "MISSING_VAR" in toolset.error

    def test_callable_deferred(self):
        """Callable prerequisite is deferred: toolset marked for lazy init."""
        prereq = CallablePrerequisite(callable=callable_success)
        toolset = SampleToolset(prerequisites=[prereq], config={})
        toolset.check_config_prerequisites()
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset._initialized is False
        assert toolset._lazy_init is True
        assert toolset.needs_initialization

    def test_command_deferred(self):
        """Command prerequisite is deferred: toolset marked for lazy init."""
        prereq = ToolsetCommandPrerequisite(command="my_cmd")
        toolset = SampleToolset(prerequisites=[prereq])
        toolset.check_config_prerequisites()
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset._initialized is False
        assert toolset._lazy_init is True
        assert toolset.needs_initialization

    @patch.dict(os.environ, {"MY_VAR": "value"}, clear=True)
    def test_mixed_prereqs_with_deferred(self):
        """Mix of config checks and callable: config passes, callable deferred."""
        prerequisites = [
            StaticPrerequisite(enabled=True, disabled_reason=""),
            ToolsetEnvironmentPrerequisite(env=["MY_VAR"]),
            CallablePrerequisite(callable=callable_success),
        ]
        toolset = SampleToolset(prerequisites=prerequisites, config={})
        toolset.check_config_prerequisites()
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset.needs_initialization

    @patch.dict(os.environ, {}, clear=True)
    def test_env_fails_before_callable_deferred(self):
        """If env var check fails, callable is never considered (short-circuit)."""
        mock_callable = Mock(return_value=(True, ""))
        prerequisites = [
            ToolsetEnvironmentPrerequisite(env=["MISSING"]),
            CallablePrerequisite(callable=mock_callable),
        ]
        toolset = SampleToolset(prerequisites=prerequisites, config={})
        toolset.check_config_prerequisites()
        assert toolset.status == ToolsetStatusEnum.FAILED
        mock_callable.assert_not_called()


class TestLazyInitialize:
    """Tests for lazy_initialize - runs deferred prerequisites on first use."""

    def test_already_initialized(self):
        """If already initialized, lazy_initialize is a no-op."""
        toolset = SampleToolset(prerequisites=[])
        toolset.check_config_prerequisites()
        assert toolset._initialized is True
        result = toolset.lazy_initialize()
        assert result is True
        assert toolset.status == ToolsetStatusEnum.ENABLED

    def test_lazy_init_runs_full_prerequisites(self):
        """lazy_initialize runs the full check_prerequisites including callables."""
        mock_callable = Mock(return_value=(True, ""))
        prereq = CallablePrerequisite(callable=mock_callable)
        toolset = SampleToolset(prerequisites=[prereq], config={"key": "value"})

        # Config check defers the callable
        toolset.check_config_prerequisites()
        assert toolset.needs_initialization
        mock_callable.assert_not_called()

        # Lazy init runs the callable
        result = toolset.lazy_initialize()
        assert result is True
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset._initialized is True
        assert not toolset.needs_initialization
        mock_callable.assert_called_once_with({"key": "value"})

    def test_lazy_init_failure(self):
        """If callable prerequisite fails during lazy init, status becomes FAILED."""
        prereq = CallablePrerequisite(callable=callable_failure_with_message)
        toolset = SampleToolset(prerequisites=[prereq], config={})

        toolset.check_config_prerequisites()
        assert toolset.needs_initialization

        result = toolset.lazy_initialize()
        assert result is False
        assert toolset.status == ToolsetStatusEnum.FAILED
        assert toolset.error == "Callable check failed"
        assert toolset._initialized is True  # Marked as initialized (won't retry)
        assert not toolset.needs_initialization

    @patch("holmes.core.tools.run_prerequisite_command")
    def test_lazy_init_with_command(self, mock_subprocess_run):
        """Command prerequisites run during lazy init, not during config check."""
        mock_subprocess_run.return_value = Mock(stdout="output", returncode=0)
        prereq = ToolsetCommandPrerequisite(command="my_cmd")
        toolset = SampleToolset(prerequisites=[prereq])

        toolset.check_config_prerequisites()
        mock_subprocess_run.assert_not_called()

        toolset.lazy_initialize()
        mock_subprocess_run.assert_called_once()
        assert toolset.status == ToolsetStatusEnum.ENABLED

    def test_lazy_init_exception_in_callable(self):
        """If callable raises exception during lazy init, status is FAILED."""
        prereq = CallablePrerequisite(callable=failing_callable_for_test)
        toolset = SampleToolset(prerequisites=[prereq], config={})

        toolset.check_config_prerequisites()
        assert toolset.needs_initialization

        result = toolset.lazy_initialize()
        assert result is False
        assert toolset.status == ToolsetStatusEnum.FAILED
        assert "Failure in callable prerequisite" in toolset.error

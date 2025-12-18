# type: ignore
import os
import subprocess
from typing import Any, Dict, List
from unittest.mock import Mock, call, patch

from holmes.core.tools import (
    CallablePrerequisite,
    StaticPrerequisite,
    StructuredToolResult,
    Tool,
    Toolset,
    ToolsetCommandPrerequisite,
    ToolsetEnvironmentPrerequisite,
    ToolsetStatusEnum,
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

    def get_example_config(self) -> Dict[str, Any]:
        return {}


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


@patch("subprocess.run")
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
        shell=True,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@patch("subprocess.run")
def test_check_prerequisites_command_output_mismatch(mock_subprocess_run):
    mock_subprocess_run.return_value = Mock(stdout="actual output", returncode=0)
    prereq = ToolsetCommandPrerequisite(
        command="my_command", expected_output="expected output"
    )
    toolset = SampleToolset(prerequisites=[prereq])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert "did not include `expected output`" in toolset.error


@patch("subprocess.run")
def test_check_prerequisites_command_failure(mock_subprocess_run):
    mock_subprocess_run.side_effect = subprocess.CalledProcessError(
        1, "my_command", "error output"
    )
    prereq = ToolsetCommandPrerequisite(command="my_command")
    toolset = SampleToolset(prerequisites=[prereq])
    toolset.check_prerequisites()
    assert toolset.status == ToolsetStatusEnum.FAILED


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


@patch("subprocess.run")
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


@patch("subprocess.run")
def test_check_prerequisites_command_uses_interpolate_command(mock_subprocess_run):
    mock_subprocess_run.return_value = Mock(stdout="output", returncode=0)
    with patch.dict(os.environ, {"MY_VAR": "interpolated_value"}, clear=True):
        prereq = ToolsetCommandPrerequisite(command="echo $MY_VAR")
        toolset = SampleToolset(prerequisites=[prereq])
        toolset.check_prerequisites()
        mock_subprocess_run.assert_called_once_with(
            "echo interpolated_value",
            shell=True,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    assert toolset.status == ToolsetStatusEnum.ENABLED


@patch("subprocess.run")
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
            shell=True,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ),
        call(
            "cmd2",
            shell=True,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ),
        call(
            "cmd3",
            shell=True,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ),
    ]
    mock_subprocess_run.assert_has_calls(expected_subprocess_calls, any_order=False)
    mock_callable_success.assert_called_once_with({"some_config": "value"})
    second_mock_callable_success.assert_called_once_with({"some_config": "value"})


@patch("subprocess.run")
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
    assert toolset.error == "`second_command_fails` returned 1"

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
        with patch("subprocess.run", side_effect=mock_run):
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

    with patch("subprocess.run") as mock_run:
        toolset = SampleToolset(prerequisites=prerequisites, config={})
        toolset.check_prerequisites()

    # Verify static prerequisite failed first
    assert toolset.status == ToolsetStatusEnum.FAILED
    assert toolset.error == "Static check failed"

    # Verify nothing else was executed
    mock_callable_should_not_run.assert_not_called()
    mock_run.assert_not_called()

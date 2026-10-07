import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import holmes.core.tools as tools_module
from holmes.core.tools import StructuredToolResultStatus, YAMLTool
from tests.conftest import create_mock_tool_invoke_context

TOOLSETS_DIR = Path(tools_module.__file__).parent.parent / "plugins" / "toolsets"


def _marker() -> str:
    # A unique sleep duration lets the test find exactly the processes it spawned.
    return str(9000 + uuid.uuid4().int % 900)


def _alive(marker: str) -> list:
    out = subprocess.run(
        ["ps", "-eo", "pid,stat,args"], capture_output=True, text=True
    ).stdout
    return [
        line
        for line in out.splitlines()
        if f"sleep {marker}" in line and " Z" not in line and "ps -eo" not in line
    ]


def _invoke(tool: YAMLTool, params=None):
    return tool.invoke(params or {}, create_mock_tool_invoke_context())


@pytest.fixture
def short_grace(monkeypatch):
    monkeypatch.setattr(tools_module, "YAML_TOOL_TERMINATE_GRACE_SECONDS", 1)


class TestTimeoutResolution:
    def test_default_applies_when_unset(self, monkeypatch):
        monkeypatch.setattr(tools_module, "YAML_TOOL_TIMEOUT_SECONDS", 42)
        tool = YAMLTool(name="t", description="d", command="echo hi")
        assert tool.timeout_seconds is None
        assert tool.effective_timeout_seconds == 42

    def test_per_tool_override(self, monkeypatch):
        monkeypatch.setattr(tools_module, "YAML_TOOL_TIMEOUT_SECONDS", 42)
        tool = YAMLTool(name="t", description="d", command="echo", timeout_seconds=7)
        assert tool.effective_timeout_seconds == 7

    def test_override_clamped_to_max(self, monkeypatch, caplog):
        monkeypatch.setattr(tools_module, "YAML_TOOL_MAX_TIMEOUT_SECONDS", 600)
        tool = YAMLTool(
            name="t", description="d", command="echo", timeout_seconds=100000
        )
        with caplog.at_level(logging.WARNING):
            assert tool.effective_timeout_seconds == 600
        assert "clamping to 600" in caplog.text

    def test_env_default_clamped_to_max(self, monkeypatch):
        monkeypatch.setattr(tools_module, "YAML_TOOL_TIMEOUT_SECONDS", 900)
        monkeypatch.setattr(tools_module, "YAML_TOOL_MAX_TIMEOUT_SECONDS", 120)
        tool = YAMLTool(name="t", description="d", command="echo")
        assert tool.effective_timeout_seconds == 120

    @pytest.mark.parametrize("value", [0, -5])
    def test_non_positive_rejected(self, value):
        with pytest.raises(ValueError):
            YAMLTool(name="t", description="d", command="echo", timeout_seconds=value)

    def test_parsed_from_yaml_toolset(self):
        from holmes.core.tools import YAMLToolset

        toolset = YAMLToolset(
            name="ts",
            description="d",
            tools=[
                {
                    "name": "slow",
                    "description": "d",
                    "command": "echo",
                    "timeout_seconds": 3,
                }
            ],
        )
        assert toolset.tools[0].effective_timeout_seconds == 3

    def test_env_vars_defaults(self):
        import holmes.common.env_vars as env_vars

        if "YAML_TOOL_TIMEOUT_SECONDS" not in os.environ:
            assert env_vars.YAML_TOOL_TIMEOUT_SECONDS == 60
        if "YAML_TOOL_MAX_TIMEOUT_SECONDS" not in os.environ:
            assert env_vars.YAML_TOOL_MAX_TIMEOUT_SECONDS == 600


class TestTimeoutExecution:
    def test_hung_command_times_out_and_kills_children(self, short_grace):
        marker = _marker()
        # bash -> bash -> sleep: killing only the top-level bash would orphan sleep.
        tool = YAMLTool(
            name="hang",
            description="d",
            command=f"echo partial-before-hang; bash -c 'sleep {marker} & wait'",
            timeout_seconds=1,
        )
        start = time.monotonic()
        result = _invoke(tool)
        elapsed = time.monotonic() - start

        assert 1 <= elapsed < 6
        assert result.status == StructuredToolResultStatus.ERROR
        assert result.return_code is None
        assert "timed out after 1s" in result.error
        assert f"sleep {marker}" in result.error  # exact rendered command
        assert "--since" in result.error  # actionable hint
        assert result.data == "partial-before-hang"
        assert result.invocation.endswith(f"sleep {marker} & wait'")
        assert _alive(marker) == []

    def test_script_tool_times_out_and_temp_file_removed(self, short_grace):
        marker = _marker()
        tool = YAMLTool(
            name="hang_script",
            description="d",
            script=f"#!/bin/bash\necho starting\nsleep {marker}\n",
            timeout_seconds=1,
        )
        with patch.object(
            tools_module.os, "remove", wraps=tools_module.os.remove
        ) as remove:
            result = _invoke(tool)
        assert result.status == StructuredToolResultStatus.ERROR
        assert "timed out after 1s" in result.error
        assert result.data == "starting"
        assert _alive(marker) == []
        script_path = remove.call_args[0][0]
        assert not os.path.exists(script_path)

    def test_sigterm_ignoring_child_is_sigkilled(self, short_grace):
        marker = _marker()
        tool = YAMLTool(
            name="stubborn",
            description="d",
            command=f"bash -c \"trap '' TERM; sleep {marker}\"",
            timeout_seconds=1,
        )
        start = time.monotonic()
        result = _invoke(tool)
        elapsed = time.monotonic() - start
        assert result.status == StructuredToolResultStatus.ERROR
        assert "timed out" in result.error
        assert elapsed < 6
        assert _alive(marker) == []

    def test_sigterm_handler_runs_before_kill(self, short_grace, tmp_path):
        marker = _marker()
        cleaned = tmp_path / "cleaned"
        tool = YAMLTool(
            name="graceful",
            description="d",
            command=f"trap 'touch {cleaned}; exit 1' TERM; sleep {marker} & wait",
            timeout_seconds=1,
        )
        result = _invoke(tool)
        assert "timed out" in result.error
        assert cleaned.exists()
        assert _alive(marker) == []

    @pytest.mark.parametrize(
        "prefix,expected",
        [("echo before-escape; ", "before-escape"), ("", "")],
    )
    def test_escaped_session_does_not_block_return(
        self, short_grace, prefix, expected
    ):
        marker = _marker()
        # setsid moves the child out of our process group while it still holds
        # the stdout pipe; the call must still return.
        tool = YAMLTool(
            name="escaped",
            description="d",
            command=f"{prefix}setsid sleep {marker}",
            timeout_seconds=1,
        )
        start = time.monotonic()
        try:
            result = _invoke(tool)
            assert time.monotonic() - start < 8
            assert result.status == StructuredToolResultStatus.ERROR
            assert "timed out" in result.error
            assert result.data == expected
        finally:
            subprocess.run(["pkill", "-f", f"sleep {marker}"], check=False)

    def test_signal_to_exited_group_is_ignored(self):
        process = subprocess.Popen(["true"], start_new_session=True)
        process.wait()
        tools_module._signal_process_group(process, tools_module.signal.SIGTERM)

    def test_fast_command_unaffected(self):
        tool = YAMLTool(
            name="fast", description="d", command="echo ok", timeout_seconds=5
        )
        result = _invoke(tool)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "ok"
        assert result.error is None

    def test_failing_command_unaffected(self):
        tool = YAMLTool(
            name="fail", description="d", command="echo boom; exit 3", timeout_seconds=5
        )
        result = _invoke(tool)
        assert result.status == StructuredToolResultStatus.ERROR
        assert result.return_code == 3
        assert result.data == "boom"
        assert result.error == "Command `echo boom; exit 3` failed with return code 3"

    def test_default_timeout_used_when_unset(self, monkeypatch, short_grace):
        monkeypatch.setattr(tools_module, "YAML_TOOL_TIMEOUT_SECONDS", 1)
        marker = _marker()
        tool = YAMLTool(name="hang", description="d", command=f"sleep {marker}")
        result = _invoke(tool)
        assert "timed out after 1s" in result.error
        assert _alive(marker) == []

    def test_clamp_enforced_at_runtime(self, monkeypatch, short_grace):
        monkeypatch.setattr(tools_module, "YAML_TOOL_MAX_TIMEOUT_SECONDS", 1)
        marker = _marker()
        tool = YAMLTool(
            name="hang",
            description="d",
            command=f"sleep {marker}",
            timeout_seconds=500,
        )
        start = time.monotonic()
        result = _invoke(tool)
        assert time.monotonic() - start < 6
        assert "timed out after 1s" in result.error

    def test_timeout_logged_at_warning(self, short_grace, caplog):
        marker = _marker()
        tool = YAMLTool(
            name="my_slow_tool",
            description="d",
            command=f"sleep {marker}",
            timeout_seconds=1,
        )
        with caplog.at_level(logging.WARNING):
            _invoke(tool)
        records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "my_slow_tool" in r.getMessage() and "1s" in r.getMessage()
            for r in records
        )

    def test_timed_out_flag_set(self, short_grace):
        tool = YAMLTool(
            name="hang",
            description="d",
            command=f"sleep {_marker()}",
            timeout_seconds=1,
        )
        assert _invoke(tool).timed_out is True
        ok = YAMLTool(name="ok", description="d", command="echo ok")
        assert not _invoke(ok).timed_out

    def test_popen_failure_returns_error(self):
        tool = YAMLTool(name="t", description="d", command="echo ok")
        with patch.object(
            tools_module.subprocess, "Popen", side_effect=OSError("no fork")
        ):
            result = _invoke(tool)
        assert result.status == StructuredToolResultStatus.ERROR
        assert "no fork" in result.data

    def test_no_chmod_subprocess_for_scripts(self):
        tool = YAMLTool(name="s", description="d", script="#!/bin/bash\necho hi\n")
        with patch.object(
            tools_module.subprocess, "run", side_effect=AssertionError("forked")
        ):
            result = _invoke(tool)
        assert result.data == "hi"


class TestKubectlRequestTimeout:
    @staticmethod
    def _kubectl_calls(filename: str):
        data = yaml.safe_load((TOOLSETS_DIR / filename).read_text())
        for toolset in data["toolsets"].values():
            for tool in toolset.get("tools", []):
                body = tool.get("command") or tool.get("script") or ""
                for line in body.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    idx = stripped.find("kubectl ")
                    while idx != -1:
                        # Skip kubectl mentioned inside an error-message string.
                        if idx == 0 or stripped[idx - 1] not in "\"'":
                            yield tool["name"], stripped[idx:]
                        idx = stripped.find("kubectl ", idx + 1)

    @pytest.mark.parametrize("filename", ["kubernetes.yaml", "kubernetes_logs.yaml"])
    def test_every_kubectl_call_sets_request_timeout(self, filename):
        calls = list(self._kubectl_calls(filename))
        assert calls
        missing = [
            (name, call)
            for name, call in calls
            if "--request-timeout=" not in call.split("|")[0]
        ]
        assert missing == []

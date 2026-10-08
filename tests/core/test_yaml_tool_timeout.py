import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import holmes.common.env_vars as env_vars
import holmes.core.tools as tools_module
import holmes.main as holmes_main
import holmes.utils.process_group as process_group
from holmes.core.tool_calling_llm import ToolCallExecutor
from holmes.core.tools import StructuredToolResultStatus, YAMLTool, YAMLToolset
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


def _wait_until_alive(marker: str, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not _alive(marker):
        assert time.monotonic() < deadline, f"sleep {marker} never started"
        time.sleep(0.05)


def _invoke(tool: YAMLTool, params=None):
    return tool.invoke(params or {}, create_mock_tool_invoke_context())


@pytest.fixture
def short_grace(monkeypatch):
    monkeypatch.setattr(process_group, "TERMINATE_GRACE_SECONDS", 1)


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

    def test_override_clamped_to_max_and_warned_once_at_load(
        self, monkeypatch, caplog
    ):
        monkeypatch.setattr(tools_module, "YAML_TOOL_MAX_TIMEOUT_SECONDS", 600)
        with caplog.at_level(logging.WARNING):
            tool = YAMLTool(
                name="big", description="d", command="echo", timeout_seconds=100000
            )
        assert "'big' timeout_seconds=100000" in caplog.text
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert tool.effective_timeout_seconds == 600
            assert tool.effective_timeout_seconds == 600
        assert caplog.text == ""

    def test_env_default_clamped_to_max(self, monkeypatch):
        monkeypatch.setattr(tools_module, "YAML_TOOL_TIMEOUT_SECONDS", 900)
        monkeypatch.setattr(tools_module, "YAML_TOOL_MAX_TIMEOUT_SECONDS", 120)
        tool = YAMLTool(name="t", description="d", command="echo")
        assert tool.effective_timeout_seconds == 120

    @pytest.mark.parametrize("default,maximum", [(0, 600), (-5, 600), (60, 0)])
    def test_non_positive_env_values_floor_at_one_second(
        self, monkeypatch, default, maximum
    ):
        monkeypatch.setattr(tools_module, "YAML_TOOL_TIMEOUT_SECONDS", default)
        monkeypatch.setattr(tools_module, "YAML_TOOL_MAX_TIMEOUT_SECONDS", maximum)
        tool = YAMLTool(name="t", description="d", command="echo")
        assert tool.effective_timeout_seconds == 1

    @pytest.mark.parametrize("value", [0, -5])
    def test_non_positive_override_rejected(self, value):
        with pytest.raises(ValueError):
            YAMLTool(name="t", description="d", command="echo", timeout_seconds=value)

    def test_parsed_from_yaml_toolset(self):
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

    def test_misconfigured_env_warned_once_at_import(self):
        env = {
            **os.environ,
            "YAML_TOOL_TIMEOUT_SECONDS": "900",
            "YAML_TOOL_MAX_TIMEOUT_SECONDS": "120",
        }
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "import logging; logging.basicConfig();"
                "import holmes.core.tools as t;"
                "print(t.YAMLTool(name='x', description='d', command='echo')"
                ".effective_timeout_seconds)",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.strip() == "120"
        assert out.stderr.count("YAML_TOOL_TIMEOUT_SECONDS=900 exceeds") == 1

    def test_env_vars_defaults(self):
        if "YAML_TOOL_TIMEOUT_SECONDS" not in os.environ:
            assert env_vars.YAML_TOOL_TIMEOUT_SECONDS == 60
        if "YAML_TOOL_MAX_TIMEOUT_SECONDS" not in os.environ:
            assert env_vars.YAML_TOOL_MAX_TIMEOUT_SECONDS == 600


class TestRequestTimeoutVariable:
    @pytest.mark.parametrize("timeout,expected", [(60, "50"), (300, "290"), (5, "1")])
    def test_rendered_from_tool_timeout(self, timeout, expected):
        tool = YAMLTool(
            name="t",
            description="d",
            command="echo {{ request_timeout_seconds }}",
            timeout_seconds=timeout,
        )
        assert _invoke(tool).data == expected

    def test_follows_env_default(self, monkeypatch):
        monkeypatch.setattr(tools_module, "YAML_TOOL_TIMEOUT_SECONDS", 120)
        tool = YAMLTool(
            name="t", description="d", command="echo {{ request_timeout_seconds }}"
        )
        assert _invoke(tool).data == "110"

    def test_not_exposed_as_llm_parameter(self):
        tool = YAMLTool(
            name="t",
            description="d",
            command="kubectl get {{ kind }} --request-timeout={{ request_timeout_seconds }}s",
        )
        assert list(tool.parameters) == ["kind"]

    def test_llm_cannot_override(self):
        tool = YAMLTool(
            name="t",
            description="d",
            command="echo {{ request_timeout_seconds }}",
            timeout_seconds=60,
        )
        assert _invoke(tool, {"request_timeout_seconds": 9999}).data == "50"

    def test_rendered_in_one_liner(self):
        tool = YAMLTool(
            name="t",
            description="d",
            command="kubectl top pods --request-timeout={{ request_timeout_seconds }}s",
            timeout_seconds=60,
        )
        assert tool.get_parameterized_one_liner({}).endswith("=50s")


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
        assert result.timed_out is True
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
        assert result.error.startswith(
            "Script of tool 'hang_script' timed out after 1s"
        )
        assert f"sleep {marker}" not in result.error
        assert result.invocation.endswith(f"sleep {marker}")
        assert result.data == "starting"
        assert _alive(marker) == []
        assert not os.path.exists(remove.call_args[0][0])

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
        assert result.timed_out is True
        assert time.monotonic() - start < 6
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
        assert _invoke(tool).timed_out is True
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
            assert result.timed_out is True
            assert result.data == expected
        finally:
            subprocess.run(["pkill", "-f", f"sleep {marker}"], check=False)

    def test_interrupt_kills_process_group(self):
        marker = _marker()
        tool = YAMLTool(
            name="interrupted",
            description="d",
            command=f"sleep {marker}",
            timeout_seconds=30,
        )
        threading.Thread(
            target=lambda: (
                _wait_until_alive(marker),
                os.kill(os.getpid(), signal.SIGINT),
            ),
            daemon=True,
        ).start()
        start = time.monotonic()
        with pytest.raises(KeyboardInterrupt):
            _invoke(tool)
        assert time.monotonic() - start < 10
        assert _alive(marker) == []
        assert process_group._running == {}

    def test_terminate_running_commands_unblocks_running_tool(self):
        marker = _marker()
        tool = YAMLTool(
            name="running",
            description="d",
            command=f"bash -c 'sleep {marker} & wait'",
            timeout_seconds=30,
        )
        results = []
        worker = threading.Thread(target=lambda: results.append(_invoke(tool)))
        worker.start()
        _wait_until_alive(marker)
        process_group.terminate_running_commands()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert results[0].status == StructuredToolResultStatus.ERROR
        assert _alive(marker) == []

    def test_terminate_scoped_to_thread_ids(self):
        marker = _marker()
        tool = YAMLTool(
            name="running",
            description="d",
            command=f"sleep {marker}",
            timeout_seconds=30,
        )
        results = []
        worker = threading.Thread(target=lambda: results.append(_invoke(tool)))
        worker.start()
        _wait_until_alive(marker)
        process_group.terminate_running_commands({threading.get_ident()})
        time.sleep(0.3)
        assert _alive(marker) != []
        process_group.terminate_running_commands({worker.ident})
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert _alive(marker) == []

    @pytest.mark.parametrize("interrupt", [KeyboardInterrupt, GeneratorExit])
    def test_tool_call_executor_kills_its_commands_when_abandoned(self, interrupt):
        marker = _marker()
        tool = YAMLTool(
            name="hang", description="d", command=f"sleep {marker}", timeout_seconds=30
        )
        start = time.monotonic()
        with pytest.raises(interrupt):
            with ToolCallExecutor(max_workers=2) as executor:
                future = executor.submit(_invoke, tool)
                _wait_until_alive(marker)
                raise interrupt()
        assert time.monotonic() - start < 10
        assert future.result().status == StructuredToolResultStatus.ERROR
        assert _alive(marker) == []

    def test_tool_call_executor_waits_on_other_errors(self):
        tool = YAMLTool(
            name="quick", description="d", command="sleep 1; echo done", timeout_seconds=30
        )
        with pytest.raises(ValueError):
            with ToolCallExecutor(max_workers=1) as executor:
                future = executor.submit(_invoke, tool)
                raise ValueError()
        assert future.result().data == "done"

    def test_cli_exit_terminates_running_tools(self, monkeypatch):
        calls = []

        def aborted():
            raise SystemExit(1)

        monkeypatch.setattr(holmes_main, "app", aborted)
        monkeypatch.setattr(
            holmes_main, "terminate_running_commands", lambda: calls.append(1)
        )
        monkeypatch.setattr(holmes_main.sys, "argv", ["holmes", "ask", "q"])
        with pytest.raises(SystemExit):
            holmes_main.run()
        assert calls == [1]

    def test_signal_to_exited_group_is_ignored(self):
        process = subprocess.Popen(["true"], start_new_session=True)
        process.wait()
        process_group._signal_process_group(
            process, process_group.signal.SIGTERM
        )

    def test_fast_command_unaffected(self):
        tool = YAMLTool(
            name="fast", description="d", command="echo ok", timeout_seconds=5
        )
        result = _invoke(tool)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "ok"
        assert result.error is None
        assert result.timed_out is None
        assert process_group._running == {}

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
        assert "timed out after 1s" in _invoke(tool).error
        assert _alive(marker) == []

    def test_clamp_enforced_at_runtime(self, monkeypatch, short_grace):
        monkeypatch.setattr(tools_module, "YAML_TOOL_MAX_TIMEOUT_SECONDS", 1)
        tool = YAMLTool(
            name="hang",
            description="d",
            command=f"sleep {_marker()}",
            timeout_seconds=500,
        )
        start = time.monotonic()
        result = _invoke(tool)
        assert time.monotonic() - start < 6
        assert "timed out after 1s" in result.error

    def test_timeout_logged_at_warning(self, short_grace, caplog):
        tool = YAMLTool(
            name="my_slow_tool",
            description="d",
            command=f"sleep {_marker()}",
            timeout_seconds=1,
        )
        with caplog.at_level(logging.WARNING):
            _invoke(tool)
        assert any(
            r.levelno == logging.WARNING
            and "my_slow_tool" in r.getMessage()
            and "1s" in r.getMessage()
            for r in caplog.records
        )

    def test_popen_failure_returns_error(self):
        tool = YAMLTool(name="t", description="d", command="echo ok")
        with patch.object(
            process_group.subprocess, "Popen", side_effect=OSError("no fork")
        ):
            result = _invoke(tool)
        assert result.status == StructuredToolResultStatus.ERROR
        assert "no fork" in result.data

    def test_no_chmod_subprocess_for_scripts(self):
        tool = YAMLTool(name="s", description="d", script="#!/bin/bash\necho hi\n")
        with patch.object(
            tools_module.subprocess, "run", side_effect=AssertionError("forked")
        ):
            assert _invoke(tool).data == "hi"


def _builtin_tools(filename: str) -> dict:
    data = yaml.safe_load((TOOLSETS_DIR / filename).read_text()) or {}
    return {
        tool["name"]: tool
        for toolset in (data.get("toolsets") or {}).values()
        for tool in toolset.get("tools", [])
    }


class TestBuiltinToolsets:
    @staticmethod
    def _kubectl_calls(filename: str):
        for name, tool in _builtin_tools(filename).items():
            body = tool.get("command") or tool.get("script") or ""
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                idx = stripped.find("kubectl ")
                while idx != -1:
                    # Skip kubectl mentioned inside an error-message string.
                    if idx == 0 or stripped[idx - 1] not in "\"'":
                        yield name, stripped[idx:]
                    idx = stripped.find("kubectl ", idx + 1)

    @pytest.mark.parametrize("filename", ["kubernetes.yaml", "kubernetes_logs.yaml"])
    def test_every_kubectl_call_derives_request_timeout(self, filename):
        calls = list(self._kubectl_calls(filename))
        assert calls
        missing = [
            (name, call)
            for name, call in calls
            if "--request-timeout={{ request_timeout_seconds }}s"
            not in call.split("|")[0]
        ]
        assert missing == []

    @pytest.mark.parametrize(
        "filename,tool,seconds",
        [
            ("cilium.yaml", "cilium_connectivity_test", 600),
            ("cilium.yaml", "cilium_connectivity_test_namespace", 600),
            ("cilium.yaml", "cilium_sysdump", 600),
            ("cilium.yaml", "cilium_install_status", 330),
            ("inspektor_gadget.yaml", "ig_node_snapshot_process", 120),
            ("inspektor_gadget.yaml", "ig_node_trace_exec", 300),
            ("inspektor_gadget.yaml", "ig_node_tcpdump", 300),
        ],
    )
    def test_long_running_tools_override_default(self, filename, tool, seconds):
        assert _builtin_tools(filename)[tool]["timeout_seconds"] == seconds

    def test_every_inspektor_gadget_tool_overrides_default(self):
        tools = _builtin_tools("inspektor_gadget.yaml")
        assert all("timeout_seconds" in t for t in tools.values())

    @pytest.mark.parametrize(
        "filename", sorted(p.name for p in TOOLSETS_DIR.glob("*.yaml"))
    )
    def test_builtin_overrides_within_default_max(self, filename):
        for name, tool in _builtin_tools(filename).items():
            seconds = tool.get("timeout_seconds")
            assert seconds is None or 0 < seconds <= 600, name

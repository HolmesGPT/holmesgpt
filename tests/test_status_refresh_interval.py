import sys
import time
from pathlib import Path

import pytest

import server
from holmes.config import Config
from holmes.core.tools import ToolsetStatusEnum, ToolsetTag

STDIO_SERVER = Path(__file__).parent / "stdio_server.py"
TAGS = [ToolsetTag.CORE, ToolsetTag.CLUSTER]


@pytest.fixture
def clock(monkeypatch):
    """Lets a test move time.monotonic forward without sleeping."""
    real_monotonic = time.monotonic
    offset = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + offset[0])

    def advance(seconds: float) -> None:
        offset[0] += seconds

    return advance


def _stdio_server(sessions: Path, server_script: str, **server_fields) -> dict:
    """A stdio MCP server entry that appends a line to `sessions` each time
    Holmes opens a session to it."""
    return {
        "description": "Example MCP server",
        "config": {
            "mode": "stdio",
            "command": "sh",
            "args": ["-c", f"echo >> {sessions}; {server_script}"],
        },
        **server_fields,
    }


def _mcp_config(tmp_path: Path, server_script: str, **server_fields) -> tuple[Config, Path]:
    sessions = tmp_path / "sessions"
    config = Config(
        mcp_servers={"example": _stdio_server(sessions, server_script, **server_fields)}
    )
    return config, sessions


def _sessions_opened(sessions: Path) -> int:
    return len(sessions.read_text().splitlines()) if sessions.exists() else 0


def _example(config: Config):
    executor = config.cached_tool_executor
    return next(t for t in executor.toolsets if t.name == "example")


def _refresh(config: Config) -> None:
    config.refresh_tool_executor(None, toolset_tag_filter=TAGS)


WORKING_SERVER = f"exec {sys.executable} {STDIO_SERVER}"


def test_without_interval_every_refresh_checks_the_server(tmp_path):
    config, sessions = _mcp_config(tmp_path, WORKING_SERVER)

    for expected in (1, 2, 3):
        _refresh(config)
        assert _sessions_opened(sessions) == expected


def test_interval_skips_checks_until_it_elapses(tmp_path, clock):
    config, sessions = _mcp_config(tmp_path, WORKING_SERVER, status_refresh_interval_seconds=3600)

    _refresh(config)
    assert _sessions_opened(sessions) == 1

    clock(300)
    _refresh(config)
    clock(300)
    _refresh(config)
    assert _sessions_opened(sessions) == 1
    example = _example(config)
    assert example.status == ToolsetStatusEnum.ENABLED
    assert {t.mcp_tool_name for t in example.tools} >= {"greet", "add"}
    assert "greet" in config.cached_tool_executor.tools_by_name

    clock(3600)
    _refresh(config)
    assert _sessions_opened(sessions) == 2


def test_failed_server_with_interval_does_not_shorten_the_refresh_loop(tmp_path, monkeypatch):
    with_interval, _ = _mcp_config(tmp_path, "exit 1", status_refresh_interval_seconds=3600)
    _refresh(with_interval)
    assert _example(with_interval).status == ToolsetStatusEnum.FAILED
    monkeypatch.setattr(server, "config", with_interval)
    assert server._has_failed_mcp_toolsets() is False

    without_interval, _ = _mcp_config(tmp_path, "exit 1")
    _refresh(without_interval)
    assert _example(without_interval).status == ToolsetStatusEnum.FAILED
    monkeypatch.setattr(server, "config", without_interval)
    assert server._has_failed_mcp_toolsets() is True


def test_carried_over_server_keeps_its_tools_when_the_executor_is_rebuilt(tmp_path, clock):
    sessions = tmp_path / "sessions"
    other_down = tmp_path / "other_down"
    config = Config(
        mcp_servers={
            "example": _stdio_server(sessions, WORKING_SERVER, status_refresh_interval_seconds=3600),
            "other": _stdio_server(
                tmp_path / "other_sessions", f"test -e {other_down} && exit 1; {WORKING_SERVER}"
            ),
        }
    )
    _refresh(config)
    executor_before = config.cached_tool_executor

    other_down.touch()
    clock(300)
    _refresh(config)

    executor = config.cached_tool_executor
    assert executor is not executor_before
    assert next(t for t in executor.toolsets if t.name == "other").status == ToolsetStatusEnum.FAILED
    assert _sessions_opened(sessions) == 1
    assert _example(config).status == ToolsetStatusEnum.ENABLED
    assert "greet" in executor.tools_by_name


def test_reload_checks_the_server_inside_its_interval(tmp_path):
    config, sessions = _mcp_config(tmp_path, WORKING_SERVER, status_refresh_interval_seconds=3600)
    _refresh(config)

    config.reload_toolsets()
    _refresh(config)
    assert _sessions_opened(sessions) == 2

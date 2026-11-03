"""Every Holmes server caller reuses the server's one cached tool executor.

Config caches a single executor under a (tags, enable_all_toolsets_possible)
key. A caller that asks with another key rebuilds the executor from scratch,
re-checking every toolset, and replaces the cached one, so the next caller
rebuilds it again (ROB-1525). The MCP server here has a status refresh
interval, so once the executor is built, neither a caller nor a refresh inside
the interval should open another session to it.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import server
from holmes.admin import admin_api
from holmes.checks import checks_api
from holmes.config import Config
from holmes.core.conversations_worker.models import ConversationTask
from holmes.core.conversations_worker.processor import ConversationProcessor
from holmes.core.conversations_worker.tool_call_worker import ToolCallWorker
from holmes.core.models import ChatRequest
from holmes.utils.holmes_sync_toolsets import holmes_sync_toolsets_status
from tests.test_status_refresh_interval import WORKING_SERVER, _mcp_config, _refresh, _sessions_opened


class _StopRequest(Exception):
    """Ends a request once it has fetched its executor."""


def _disabled_dal() -> MagicMock:
    # enabled=False keeps the OAuth token manager, a process-wide singleton,
    # off this mock: it only adopts an enabled DAL as its token store.
    return MagicMock(enabled=False)


@pytest.fixture
def built_server(tmp_path, monkeypatch):
    """The server module running on a Config whose executor is already built."""
    config, sessions = _mcp_config(tmp_path, WORKING_SERVER, status_refresh_interval_seconds=3600)
    dal = _disabled_dal()
    dal.get_global_instructions_for_account.side_effect = _StopRequest
    monkeypatch.setattr(server, "config", config)
    monkeypatch.setattr(server, "dal", dal)
    monkeypatch.setattr(Config, "_get_llm", lambda self, **kwargs: MagicMock())

    _refresh(config)
    assert _sessions_opened(sessions) == 1
    return config, sessions


def _api_info(config: Config, monkeypatch) -> None:
    TestClient(server.app).get("/api/info")


def _oauth_callback(config: Config, monkeypatch) -> None:
    # "example" has no OAuth config, so the callback answers 400 once it has
    # looked the toolset up in the executor.
    TestClient(server.app).post(
        "/api/oauth/callback",
        json={"toolset_name": "example", "code": "code", "redirect_uri": "https://platform.example/callback"},
    )


def _chat(config: Config, monkeypatch) -> None:
    # The server's DAL raises _StopRequest right after the executor step.
    TestClient(server.app, raise_server_exceptions=False).post("/api/chat", json={"ask": "why is checkout slow?"})


def _conversation_worker(config: Config, monkeypatch) -> None:
    processor = ConversationProcessor(dal=_disabled_dal(), config=config, holmes_id="holmes-test")
    processor._inject_frontend_tools = lambda ai, chat_request, task: None
    processor._run_chat_and_publish(
        task=ConversationTask(
            conversation_id="c1", account_id="a1", cluster_id="cl1", origin="chat", request_sequence=1
        ),
        chat_request=ChatRequest(ask="why is checkout slow?", conversation_id="c1"),
        publisher=MagicMock(),
    )


def _tool_call_worker(config: Config, monkeypatch) -> None:
    ToolCallWorker._get_tool_executor(SimpleNamespace(config=config, dal=None))


def _toolset_sync(config: Config, monkeypatch) -> None:
    config.cluster_name = "test-cluster"
    dal = _disabled_dal()
    dal.account_id = "test-account"
    holmes_sync_toolsets_status(dal, config)


def _health_check(config: Config, monkeypatch) -> None:
    monkeypatch.setattr(checks_api, "_CONFIG", config, raising=False)
    checks_api._get_ai(model=None)


SERVER_CALLERS = {
    "api_info": _api_info,
    "oauth_callback": _oauth_callback,
    "chat": _chat,
    "conversation_worker": _conversation_worker,
    "tool_call_worker": _tool_call_worker,
    "toolset_sync": _toolset_sync,
    "health_check": _health_check,
}


@pytest.mark.parametrize("call", SERVER_CALLERS.values(), ids=SERVER_CALLERS.keys())
def test_server_callers_reuse_the_cached_executor(built_server, clock, monkeypatch, call):
    config, sessions = built_server
    executor = config.cached_tool_executor
    executor_requests = []
    create_tool_executor = Config.create_tool_executor

    def recording_create_tool_executor(self, *args, **kwargs):
        executor_requests.append(kwargs)
        return create_tool_executor(self, *args, **kwargs)

    monkeypatch.setattr(Config, "create_tool_executor", recording_create_tool_executor)

    call(config, monkeypatch)
    assert executor_requests, "the caller never asked for an executor"
    assert config.cached_tool_executor is executor
    assert _sessions_opened(sessions) == 1

    clock(300)
    _refresh(config)
    assert config.cached_tool_executor is executor
    assert _sessions_opened(sessions) == 1


def test_api_info_reports_the_toolsets_the_chat_loads(built_server):
    """ROB-1501: /api/info describes the executor the server's chat uses."""
    config, _ = built_server
    chat_toolsets = config.cached_tool_executor.toolsets

    info = TestClient(server.app).get("/api/info?detail=full").json()

    assert [(t["name"], t["status"]) for t in info["toolsets"]] == [
        (t.name, t.status.value) for t in chat_toolsets
    ]
    assert info["toolsets_summary"]["enabled"] == len([t for t in chat_toolsets if t.status.value == "enabled"])


def test_admin_reload_builds_the_executor_the_refresh_reuses(built_server, clock, monkeypatch):
    config, sessions = built_server
    monkeypatch.setattr(admin_api, "_CONFIG", config)
    monkeypatch.setattr(admin_api, "_DAL", _disabled_dal())

    executor = admin_api._reload_and_rebuild_toolsets()
    assert config.cached_tool_executor is executor
    assert _sessions_opened(sessions) == 2

    clock(300)
    _refresh(config)
    assert config.cached_tool_executor is executor
    assert _sessions_opened(sessions) == 2


class _StopLoop(Exception):
    pass


class _InlineThread:
    def __init__(self, target, **kwargs):
        self._target = target

    def start(self) -> None:
        self._target()


class _Delegating:
    """Stands in for a module in server's namespace only, so the patch doesn't
    reach other modules' threads or sleeps."""

    def __init__(self, module) -> None:
        self._module = module

    def __getattr__(self, name: str):
        return getattr(self._module, name)


class _InlineThreading(_Delegating):
    Thread = _InlineThread


class _CountedSleep(_Delegating):
    """Ends the refresh loop when it goes to sleep after `cycles` cycles."""

    def __init__(self, module, cycles: int) -> None:
        super().__init__(module)
        self._sleeps_left = cycles + 1

    def sleep(self, seconds: float) -> None:
        self._sleeps_left -= 1
        if not self._sleeps_left:
            raise _StopLoop


def _run_refresh_loop(config: Config, monkeypatch, cycles: int) -> None:
    monkeypatch.setattr(server, "config", config)
    monkeypatch.setattr(server, "dal", _disabled_dal())
    monkeypatch.setattr(server, "threading", _InlineThreading(server.threading))
    monkeypatch.setattr(server, "time", _CountedSleep(server.time, cycles=cycles))

    with pytest.raises(_StopLoop):
        server._toolset_status_refresh_loop()


def _two_refresh_loop_cycles(config: Config, monkeypatch) -> None:
    _run_refresh_loop(config, monkeypatch, cycles=2)


# Callers that can be the first to build the server's executor: the refresh
# loop, and the tool-call worker when the startup toolset sync failed or an
# admin reload just cleared the cache.
FIRST_BUILDERS = {
    "refresh_loop": _two_refresh_loop_cycles,
    "tool_call_worker": _tool_call_worker,
}


@pytest.mark.parametrize("build", FIRST_BUILDERS.values(), ids=FIRST_BUILDERS.keys())
def test_first_build_runs_one_live_check(tmp_path, monkeypatch, build):
    """With nothing built yet, the executor is built with live checks: one
    session per server and nothing written to disk, as the server may run on
    a read-only filesystem. The server's refresh interval starts, so the
    refresh loop's next cycle checks nothing."""
    config, sessions = _mcp_config(tmp_path, WORKING_SERVER, status_refresh_interval_seconds=3600)
    status_file = tmp_path / "toolsets_status.json"
    config.toolset_manager.toolset_status_location = status_file

    build(config, monkeypatch)

    assert config.cached_tool_executor is not None
    assert _sessions_opened(sessions) == 1
    assert not status_file.exists()


def test_refresh_loop_cycle_that_builds_the_executor_checks_each_server_once(tmp_path, monkeypatch):
    """A server without a status refresh interval is checked on every cycle,
    but the cycle that builds the executor has just checked it."""
    config, sessions = _mcp_config(tmp_path, WORKING_SERVER)

    _run_refresh_loop(config, monkeypatch, cycles=1)
    assert config.cached_tool_executor is not None
    assert _sessions_opened(sessions) == 1

    _run_refresh_loop(config, monkeypatch, cycles=1)
    assert _sessions_opened(sessions) == 2


def test_refresh_loop_rediscovers_platform_mcp_tools_on_the_cached_executor(built_server, monkeypatch):
    config, _ = built_server
    executor = config.cached_tool_executor
    rediscovered = []
    monkeypatch.setattr(server, "refresh_platform_mcp_tools", rediscovered.append)

    _run_refresh_loop(config, monkeypatch, cycles=1)

    assert rediscovered == [executor]

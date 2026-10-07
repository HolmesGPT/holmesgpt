import asyncio
import contextvars
import os
import socket
import threading
import time
from typing import Dict, List
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
)
from holmes.core.usage_recorder import (
    UsageRecorderState,
    record_from_llm_result,
    stream_with_usage_recording,
)
from holmes.plugins.toolsets.mcp import toolset_mcp
from holmes.plugins.toolsets.mcp.toolset_mcp import (
    MCPConfig,
    RemoteMCPTool,
    RemoteMCPToolset,
    StdioMCPConfig,
    _LoopThread,
    _PooledTransport,
    get_server_semaphore,
)
from holmes.utils.stream import StreamEvents, StreamMessage

CALL_SECONDS = 0.5


class _Tracker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.inflight: Dict[str, int] = {}
        self.peak: Dict[str, int] = {}

    def enter(self, key: str) -> None:
        with self.lock:
            self.inflight[key] = self.inflight.get(key, 0) + 1
            self.peak[key] = max(self.peak.get(key, 0), self.inflight[key])

    def exit(self, key: str) -> None:
        with self.lock:
            self.inflight[key] -= 1


def _context(i: int = 0) -> ToolInvokeContext:
    return ToolInvokeContext.model_construct(
        tool_call_id=f"call-{i}", tool_name="lookup", max_token_count=1000
    )


def _make_tool(config, name: str = "srv") -> RemoteMCPTool:
    toolset = RemoteMCPToolset(name=name, description="test", config={})
    toolset._mcp_config = config
    return RemoteMCPTool(name="lookup", description="d", parameters={}, toolset=toolset)


def _http_config(url: str = "http://mcp.test/mcp", **kwargs) -> MCPConfig:
    return MCPConfig(url=url, mode="streamable-http", **kwargs)


@pytest.fixture
def tracker():
    tracker = _Tracker()

    async def fake_invoke_async(self, params, *args, **kwargs):
        key = self.toolset._mcp_config.get_lock_string()
        tracker.enter(key)
        try:
            # Blocking sleep for stdio (own loop per call), async for the shared loop.
            if isinstance(self.toolset._mcp_config, StdioMCPConfig):
                time.sleep(CALL_SECONDS)
            else:
                await asyncio.sleep(CALL_SECONDS)
            if params.get("fail"):
                raise RuntimeError("server exploded")
        finally:
            tracker.exit(key)
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS, data="ok", params=params
        )

    with patch.object(RemoteMCPTool, "_invoke_async", fake_invoke_async):
        yield tracker


def _call_concurrently(tools: List[RemoteMCPTool], params=None):
    results: List = [None] * len(tools)

    def run(i):
        results[i] = tools[i]._invoke(dict(params or {}), _context(i))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(tools))]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, time.monotonic() - start


class TestServerConcurrency:
    def test_calls_to_one_server_run_in_parallel(self, tracker):
        tool = _make_tool(_http_config(max_concurrent_calls=4))
        results, elapsed = _call_concurrently([tool] * 4)

        assert all(r.status == StructuredToolResultStatus.SUCCESS for r in results)
        assert elapsed < CALL_SECONDS * 2
        assert tracker.peak["http://mcp.test/mcp"] == 4

    def test_default_comes_from_env_var(self, tracker, monkeypatch):
        monkeypatch.setattr(toolset_mcp, "MCP_MAX_CONCURRENT_CALLS_PER_SERVER", 2)
        tool = _make_tool(_http_config(url="http://env-default.test/mcp"))
        _, elapsed = _call_concurrently([tool] * 4)

        assert tracker.peak["http://env-default.test/mcp"] == 2
        assert elapsed >= CALL_SECONDS * 2

    def test_size_one_runs_one_at_a_time(self, tracker):
        tool = _make_tool(_http_config(url="http://serial.test/mcp", max_concurrent_calls=1))
        results, elapsed = _call_concurrently([tool] * 4)

        assert tracker.peak["http://serial.test/mcp"] == 1
        assert elapsed >= CALL_SECONDS * 4
        waits = sorted(r.mcp_wait_ms for r in results)
        assert waits[0] < 100
        assert waits[-1] >= CALL_SECONDS * 3 * 1000 * 0.9
        assert all(r.mcp_call_ms >= CALL_SECONDS * 1000 * 0.9 for r in results)

    def test_override_beats_env_default(self, tracker, monkeypatch):
        monkeypatch.setattr(toolset_mcp, "MCP_MAX_CONCURRENT_CALLS_PER_SERVER", 16)
        tool = _make_tool(_http_config(url="http://override.test/mcp", max_concurrent_calls=2))
        _call_concurrently([tool] * 6)

        assert tracker.peak["http://override.test/mcp"] == 2

    def test_servers_do_not_block_each_other(self, tracker):
        a = _make_tool(_http_config(url="http://a.test/mcp", max_concurrent_calls=1))
        b = _make_tool(_http_config(url="http://b.test/mcp", max_concurrent_calls=1))
        _, elapsed = _call_concurrently([a, b])

        assert elapsed < CALL_SECONDS * 2
        assert tracker.peak == {"http://a.test/mcp": 1, "http://b.test/mcp": 1}

    def test_stdio_server_is_bounded_without_the_shared_loop(self, tracker):
        config = StdioMCPConfig(command="fake-stdio-server", max_concurrent_calls=2)
        tool = _make_tool(config)
        with patch.object(_LoopThread, "get", side_effect=AssertionError("stdio must not use the loop")):
            results, _ = _call_concurrently([tool] * 4)

        assert all(r.status == StructuredToolResultStatus.SUCCESS for r in results)
        assert tracker.peak["fake-stdio-server"] == 2

    def test_failed_call_releases_the_slot_and_reports_timing(self, tracker):
        tool = _make_tool(_http_config(url="http://fails.test/mcp", max_concurrent_calls=1))

        failed = tool._invoke({"fail": True}, _context())
        assert failed.status == StructuredToolResultStatus.ERROR
        assert "server exploded" in failed.error
        assert failed.mcp_call_ms >= CALL_SECONDS * 1000 * 0.9

        ok, _ = _call_concurrently([tool])
        assert ok[0].status == StructuredToolResultStatus.SUCCESS
        assert ok[0].mcp_wait_ms < 100

    def test_pooling_kill_switch_uses_asyncio_run(self, tracker, monkeypatch):
        monkeypatch.setattr(toolset_mcp, "MCP_POOL_HTTP_CONNECTIONS", False)
        tool = _make_tool(_http_config(url="http://nopool.test/mcp", max_concurrent_calls=4))
        with patch.object(_LoopThread, "get", side_effect=AssertionError("pooling is off")):
            results, elapsed = _call_concurrently([tool] * 4)

        assert all(r.status == StructuredToolResultStatus.SUCCESS for r in results)
        assert elapsed < CALL_SECONDS * 2

    def test_uninitialized_config_is_an_error_not_a_crash(self):
        tool = _make_tool(None)
        result = tool._invoke({}, _context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not initialized" in result.error


class TestServerSemaphore:
    def test_same_key_and_size_share_a_semaphore(self):
        assert get_server_semaphore("k1", 3) is get_server_semaphore("k1", 3)

    def test_changed_size_gets_a_new_semaphore(self):
        assert get_server_semaphore("k2", 3) is not get_server_semaphore("k2", 1)

    def test_semaphore_is_bounded(self):
        sem = get_server_semaphore("k3", 1)
        with pytest.raises(ValueError):
            sem.release()

    @pytest.mark.parametrize("config_cls", [MCPConfig, StdioMCPConfig])
    def test_max_concurrent_calls_must_be_positive(self, config_cls):
        base = {"url": "http://x.test/mcp"} if config_cls is MCPConfig else {"command": "x"}
        with pytest.raises(ValueError):
            config_cls(**base, max_concurrent_calls=0)

    def test_max_concurrent_calls_defaults_to_unset(self):
        assert _http_config().max_concurrent_calls is None
        assert StdioMCPConfig(command="x").max_concurrent_calls is None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def json_mcp_server():
    """A real stateless JSON-response MCP server, shaped like relay's platform-mcp."""
    port = _free_port()
    server_state = {"inflight": 0, "peak": 0, "auth": []}
    mcp = FastMCP("test", host="127.0.0.1", port=port, json_response=True, stateless_http=True)

    @mcp.tool()
    async def slow_lookup(name: str) -> str:
        """Slow lookup."""
        server_state["inflight"] += 1
        server_state["peak"] = max(server_state["peak"], server_state["inflight"])
        await asyncio.sleep(CALL_SECONDS)
        server_state["inflight"] -= 1
        return f"found {name}"

    @mcp.tool()
    def get_me() -> str:
        """Identity."""
        return "me"

    app = mcp.streamable_http_app()

    async def record_auth(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            server_state["auth"].append(headers.get(b"authorization", b"").decode())
        await app(scope, receive, send)

    server = uvicorn.Server(uvicorn.Config(record_auth, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "MCP test server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp", server_state
    server.should_exit = True
    thread.join(5)


def _load_toolset(url: str, name: str, **config) -> RemoteMCPToolset:
    toolset = RemoteMCPToolset(
        name=name, description="test", config={"url": url, "mode": "streamable-http", **config}
    )
    ok, error = toolset.prerequisites_callable(toolset.config)
    assert ok, error
    return toolset


class TestRealServer:
    def test_concurrent_calls_overlap_on_the_server(self, json_mcp_server):
        url, state = json_mcp_server
        state["peak"] = 0
        toolset = _load_toolset(url, "real-parallel", max_concurrent_calls=4)
        tool = next(t for t in toolset.tools if t.name == "slow_lookup")

        results: List = [None] * 4

        def run(i):
            results[i] = tool._invoke({"name": f"n{i}"}, _context(i))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert [r.status for r in results] == [StructuredToolResultStatus.SUCCESS] * 4
        assert [r.data.startswith(f"found n{i}") for i, r in enumerate(results)] == [True] * 4
        assert state["peak"] == 4
        assert time.monotonic() - start < CALL_SECONDS * 3

    def test_calls_reuse_one_pooled_client_and_connection(self, json_mcp_server):
        url, _ = json_mcp_server
        toolset = _load_toolset(url, "real-pool")
        tool = next(t for t in toolset.tools if t.name == "get_me")
        loop_thread = _LoopThread.get()

        assert tool._invoke({}, _context(1)).status == StructuredToolResultStatus.SUCCESS
        pool = loop_thread.pools[(url, True)]
        assert tool._invoke({}, _context(2)).status == StructuredToolResultStatus.SUCCESS

        assert loop_thread.pools[(url, True)] is pool
        assert [k for k in loop_thread.pools if k[0] == url] == [(url, True)]
        connections = pool._transport._pool.connections
        assert sum(c._connection._request_count for c in connections if c._connection) > len(connections)

    def test_per_call_headers_are_not_shared_between_calls(self, json_mcp_server):
        url, state = json_mcp_server
        toolset = _load_toolset(url, "real-auth")
        tool = next(t for t in toolset.tools if t.name == "get_me")
        state["auth"].clear()

        with patch.object(
            RemoteMCPToolset,
            "_render_headers",
            lambda self, ctx: {"Authorization": f"Bearer {ctx['user']}"} if ctx else None,
        ):
            for user in ("alice", "bob"):
                ctx = _context().model_copy(update={"request_context": {"user": user}})
                assert tool._invoke({}, ctx).status == StructuredToolResultStatus.SUCCESS
            assert toolset.tools  # still loaded
            state_after = list(state["auth"])

        alice = [a for a in state_after if a == "Bearer alice"]
        bob = [a for a in state_after if a == "Bearer bob"]
        assert alice and bob
        assert set(state_after) <= {"Bearer alice", "Bearer bob"}
        first_bob = state_after.index("Bearer bob")
        assert "Bearer alice" not in state_after[first_bob:]

    def test_header_rendering_runs_off_the_shared_loop(self, json_mcp_server):
        url, _ = json_mcp_server
        toolset = _load_toolset(url, "real-render-thread")
        tool = next(t for t in toolset.tools if t.name == "get_me")
        render_threads = []

        def render(self, ctx):
            render_threads.append(threading.current_thread())
            return None

        with patch.object(RemoteMCPToolset, "_render_headers", render):
            assert tool._invoke({}, _context()).status == StructuredToolResultStatus.SUCCESS

        assert render_threads
        assert _LoopThread.get().thread not in render_threads

    def test_kill_switch_still_works_against_a_real_server(self, json_mcp_server, monkeypatch):
        url, _ = json_mcp_server
        monkeypatch.setattr(toolset_mcp, "MCP_POOL_HTTP_CONNECTIONS", False)
        toolset = _load_toolset(url, "real-nopool")
        tool = next(t for t in toolset.tools if t.name == "get_me")
        inst = _LoopThread._instance
        pools_before = dict(inst.pools) if inst else {}

        result = tool._invoke({}, _context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data.startswith("me")
        inst = _LoopThread._instance
        assert (dict(inst.pools) if inst else {}) == pools_before

    def test_unreachable_server_returns_error(self):
        tool = _make_tool(_http_config(url=f"http://127.0.0.1:{_free_port()}/mcp"))
        result = tool._invoke({}, _context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert result.error
        assert result.mcp_call_ms is not None


class TestPooledTransport:
    def test_pool_is_only_used_on_the_loop_thread(self):
        _LoopThread.get()
        assert _LoopThread.current_pool("http://x.test/mcp", True) is None

    def test_pooled_client_keeps_env_proxy_settings(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:3128")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)

        async def build():
            return _LoopThread.current_pool("https://proxied.test/mcp", True)

        pool = _LoopThread.get().run(build())
        transport = pool._transport_for_url(httpx.URL("https://proxied.test/mcp"))
        assert type(transport._pool).__name__ == "AsyncHTTPProxy"

    def test_closing_a_per_call_client_keeps_the_pool_open(self):
        async def scenario():
            seen = []

            def handler(request):
                seen.append(request.headers.get("authorization"))
                return httpx.Response(200, text="ok")

            pool = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            factory = toolset_mcp.create_mcp_http_client_factory(True, pool)
            async with factory(headers={"Authorization": "a"}) as client:
                await client.get("http://x.test/")
            async with factory(headers={"Authorization": "b"}) as client:
                await client.get("http://x.test/")
            assert not pool.is_closed
            await pool.aclose()
            return seen

        assert asyncio.run(scenario()) == ["a", "b"]

    def test_pooled_transport_close_is_a_noop(self):
        pool = httpx.AsyncClient()
        asyncio.run(_PooledTransport(pool).aclose())
        assert not pool.is_closed


class TestLoopThread:
    def test_reused_within_a_process(self):
        assert _LoopThread.get() is _LoopThread.get()

    def test_runs_in_the_callers_context(self):
        var = contextvars.ContextVar("request_id", default=None)
        var.set("req-42")

        async def read_var():
            return var.get(), threading.current_thread().name

        assert _LoopThread.get().run(read_var()) == ("req-42", "mcp-event-loop")

    def test_exceptions_propagate_to_the_caller(self):
        async def boom():
            raise KeyError("lost")

        with pytest.raises(KeyError):
            _LoopThread.get().run(boom())

    def test_recreated_after_fork(self, monkeypatch):
        first = _LoopThread.get()
        monkeypatch.setattr(first, "pid", os.getpid() + 1)
        second = _LoopThread.get()
        assert second is not first
        assert second.run(asyncio.sleep(0, result="alive")) == "alive"
        first.close()

    def test_close_releases_pools_and_a_new_loop_starts_on_demand(self):
        inst = _LoopThread.get()

        async def make_pool():
            return _LoopThread.current_pool("http://closing.test/mcp", True)

        pool = inst.run(make_pool())
        inst.close()
        inst.thread.join(5)

        assert pool.is_closed
        assert not inst.thread.is_alive()
        fresh = _LoopThread.get()
        assert fresh is not inst
        assert fresh.run(asyncio.sleep(0, result=1)) == 1

    def test_close_twice_is_harmless(self):
        inst = _LoopThread.get()
        inst.close()
        inst.thread.join(5)
        inst.close()
        assert not inst.thread.is_alive()

    def test_atexit_hook_ignores_a_loop_from_the_parent_process(self, monkeypatch):
        inst = _LoopThread.get()
        monkeypatch.setattr(inst, "pid", os.getpid() + 1)
        toolset_mcp._close_loop_thread()
        assert inst.thread.is_alive()
        monkeypatch.undo()
        inst.close()

    def test_atexit_hook_closes_the_current_loop(self):
        inst = _LoopThread.get()
        toolset_mcp._close_loop_thread()
        inst.thread.join(5)
        assert not inst.thread.is_alive()


def _state(**kwargs) -> UsageRecorderState:
    return UsageRecorderState(
        dal=None, request_type="user_chat", model="m", provider="p", is_robusta_model=False, **kwargs
    )


class TestUsageMetrics:
    def test_stream_accumulates_mcp_timings_into_meta(self):
        state = _state(meta={"existing": 1})
        events = [
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"result": {"mcp_wait_ms": 10, "mcp_call_ms": 100}}),
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"result": {"mcp_wait_ms": 250, "mcp_call_ms": 40}}),
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"result": {"status": "success"}}),
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"result": "not-a-dict"}),
            StreamMessage(event=StreamEvents.ANSWER_END, data={}),
        ]
        list(stream_with_usage_recording(iter(events), state))

        assert state.tool_call_count == 4
        assert state.meta == {
            "existing": 1,
            "mcp_calls": 2,
            "mcp_wait_ms_total": 260,
            "mcp_call_ms_total": 140,
            "mcp_max_wait_ms": 250,
        }

    def test_no_mcp_calls_leaves_meta_untouched(self):
        state = _state()
        events = [
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"result": {"status": "success"}}),
            StreamMessage(event=StreamEvents.ANSWER_END, data={}),
        ]
        list(stream_with_usage_recording(iter(events), state))
        assert state.meta == {}

    def test_non_streaming_result_accumulates_mcp_timings(self):
        class _Call:
            def __init__(self, wait, call):
                self.result = StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS, mcp_wait_ms=wait, mcp_call_ms=call
                )

        class _LLMResult:
            num_llm_calls = 2
            finish_reason = "stop"
            tool_calls = [_Call(5, 50), _Call(None, 20), _Call(None, None)]

            def model_dump(self, include=None):
                return {}

        state = _state()
        record_from_llm_result(state, _LLMResult())
        assert state.meta == {
            "mcp_calls": 2,
            "mcp_wait_ms_total": 5,
            "mcp_call_ms_total": 70,
            "mcp_max_wait_ms": 5,
        }

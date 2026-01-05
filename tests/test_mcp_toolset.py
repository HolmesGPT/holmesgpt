import asyncio
import logging
import shutil
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from holmes.core.tools import (
    StructuredToolResultStatus,
    ToolParameter,
)
from holmes.plugins.toolsets.mcp.toolset_mcp import (
    MCPConfig,
    MCPMode,
    RemoteMCPTool,
    RemoteMCPToolset,
    StdioMCPConfig,
    get_initialized_mcp_session,
)


@pytest.fixture
def suppress_migration_warnings():
    logger = logging.getLogger()
    original_level = logger.level
    logger.setLevel(logging.ERROR)
    yield
    logger.setLevel(original_level)


def npx_not_available() -> tuple[bool, str]:
    """
    Check if npx command is available in the system.
    Returns a tuple of (skip_test: bool, reason: str)
    """
    if not shutil.which("npx"):
        return True, "npx command not found in PATH"

    try:
        # Try to run 'npx --version' to check if npx is working
        subprocess.run(
            ["npx", "--version"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return False, ""
    except subprocess.CalledProcessError:
        return True, "npx command failed"
    except subprocess.TimeoutExpired:
        return True, "npx command timed out"
    except Exception as e:
        return True, f"npx not available: {str(e)}"


class TestMCPGeneral:
    def test_parsed_tool_schema_matches_expected(self, suppress_migration_warnings):
        mcp_tool = Tool(
            name="b",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "qty": {
                        "type": "integer",
                        "description": "example for description",
                    },
                    "side": {
                        "type": "string",
                        "enum": ["buy", "sell"],
                    },
                    "limit_price": {"type": "number"},
                },
                "required": ["symbol", "qty", "side"],
            },
            description="desc",
            annotations=None,
        )

        expected_schema = {
            "symbol": ToolParameter(type="string", required=True),
            "qty": ToolParameter(
                type="integer", required=True, description="example for description"
            ),
            "side": ToolParameter(type="string", required=True),
            "limit_price": ToolParameter(type="number", required=False),
        }

        mock_toolset = RemoteMCPToolset(
            name="test_toolset",
            description="Test toolset",
            config={"url": "http://localhost:1234"},
        )
        tool = RemoteMCPTool.create(mcp_tool, mock_toolset)
        assert tool.parameters == expected_schema
        assert tool.description == "desc"

    def test_unreachable_server_returns_error(self, suppress_migration_warnings):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="",
            config={"url": "http://0.0.0.0:3009"},
        )

        result = mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        assert result[0] is False
        assert "Failed to load mcp server test_mcp" in result[1]

    def test_server_with_one_tool_initializes_correctly(
        self, monkeypatch, suppress_migration_warnings
    ):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="demo mcp with 2 simple functions",
            config={"url": "http://0.0.0.0/3005"},
        )

        async def mock_get_server_tools():
            return ListToolsResult(
                tools=[
                    Tool(
                        name="b",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "symbol": {"type": "string"},
                            },
                            "required": [],
                        },
                    ),
                ]
            )

        monkeypatch.setattr(mcp_toolset, "_get_server_tools", mock_get_server_tools)
        mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        assert len(list(mcp_toolset.tools)) == 1

    def test_toolset_returns_configured_headers(
        self, monkeypatch, suppress_migration_warnings
    ):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="demo mcp with 2 simple functions",
            config={
                "url": "http://0.0.0.0/3005",
                "headers": {"header1": "test1", "header2": "test2"},
            },
        )

        # prerequisites_callable receives self.config from the framework, which has both url and headers
        mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        assert mcp_toolset._mcp_config.headers.get("header1") == "test1"

    def test_toolset_without_headers_returns_none(self, suppress_migration_warnings):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="demo mcp with 2 simple functions",
            config={"url": "http://0.0.0.0/3005"},
        )

        mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        assert mcp_toolset._mcp_config.headers is None

    def test_old_config_format_with_url_field_returns_true(
        self, monkeypatch, suppress_migration_warnings
    ):
        # Test that url passed as field parameter gets migrated to config
        mcp_toolset = RemoteMCPToolset(
            url="http://localhost:1234",
            name="test_mcp",
            description="Test toolset",
        )

        async def mock_get_server_tools():
            return ListToolsResult(tools=[])

        monkeypatch.setattr(mcp_toolset, "_get_server_tools", mock_get_server_tools)
        result = mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        assert result[0] is True
        assert str(mcp_toolset._mcp_config.url) == "http://localhost:1234/sse"
        assert mcp_toolset._mcp_config.mode == MCPMode.SSE

    def test_new_config_format_with_url_in_config_returns_true(
        self, monkeypatch, suppress_migration_warnings
    ):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
        )

        async def mock_get_server_tools():
            return ListToolsResult(tools=[])

        monkeypatch.setattr(mcp_toolset, "_get_server_tools", mock_get_server_tools)
        result = mcp_toolset.prerequisites_callable(
            config={"url": "http://localhost:1234"}
        )
        assert result[0] is True
        assert str(mcp_toolset._mcp_config.url) == "http://localhost:1234/sse"
        assert mcp_toolset._mcp_config.mode == MCPMode.SSE

    def test_no_url_returns_false(self):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
        )

        result = mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        assert result[0] is False
        assert "Config is required" in result[1]

    def test_no_url_in_config_returns_false(self):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
        )

        result = mcp_toolset.prerequisites_callable(config={})
        assert result[0] is False
        assert "Config is required" in result[1]

    def test_no_mode_configured_defaults_to_sse(
        self, monkeypatch, suppress_migration_warnings
    ):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
        )

        async def mock_get_server_tools():
            return ListToolsResult(tools=[])

        monkeypatch.setattr(mcp_toolset, "_get_server_tools", mock_get_server_tools)
        result = mcp_toolset.prerequisites_callable(
            config={"url": "http://localhost:1234"}
        )
        assert result[0] is True
        assert mcp_toolset._mcp_config.mode == MCPMode.SSE

    def test_invalid_mode_returns_false(self):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
        )

        result = mcp_toolset.prerequisites_callable(
            config={"url": "http://localhost:1234", "mode": "invalid-mode"}
        )
        assert result[0] is False
        assert 'Invalid mode "invalid-mode", allowed modes are' in result[1]

    def test_streamable_http_mode_works(self, monkeypatch, suppress_migration_warnings):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
        )

        async def mock_get_server_tools():
            return ListToolsResult(tools=[])

        monkeypatch.setattr(mcp_toolset, "_get_server_tools", mock_get_server_tools)
        result = mcp_toolset.prerequisites_callable(
            config={"url": "http://localhost:1234", "mode": "streamable-http"}
        )
        assert result[0] is True
        assert mcp_toolset._mcp_config.mode == MCPMode.STREAMABLE_HTTP


class TestStreamableHttp:
    def _setup_mocks(self, mock_session):
        mock_read_stream = AsyncMock()
        mock_write_stream = AsyncMock()

        mock_client_context = AsyncMock()
        mock_client_context.__aenter__ = AsyncMock(
            return_value=(mock_read_stream, mock_write_stream, None)
        )
        mock_client_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_context = AsyncMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)

        return mock_client_context, mock_session_context

    def _patch_clients(self, mock_client_context, mock_session_context):
        return patch(
            "holmes.plugins.toolsets.mcp.toolset_mcp.streamablehttp_client",
            return_value=mock_client_context,
        ), patch(
            "holmes.plugins.toolsets.mcp.toolset_mcp.ClientSession",
            return_value=mock_session_context,
        )

    @pytest.mark.parametrize(
        "tool_name,tool_schema,params,response_text,expected_in_response",
        [
            (
                "list_authorizations",
                {"type": "object", "properties": {}, "required": []},
                {},
                '{"ok": true, "authorizations": [{"authorization_id": "auth_default_001", "status": "authorized", "amount": 150.0, "currency": "USD", "merchant_id": "merchant_001", "card_last4": "4242"}], "count": 1, "authorization_ids": ["auth_default_001"]}',
                ["auth_default_001"],
            ),
            (
                "authorize_payment",
                {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "number"},
                        "currency": {"type": "string"},
                        "card_last4": {"type": "string"},
                        "merchant_id": {"type": "string"},
                    },
                    "required": ["amount", "currency", "card_last4", "merchant_id"],
                },
                {
                    "amount": 100.0,
                    "currency": "USD",
                    "card_last4": "1234",
                    "merchant_id": "test-merchant",
                },
                '{"ok": true, "authorization_id": "auth_test_123", "status": "authorized"}',
                ["auth_test_123", "authorized"],
            ),
        ],
    )
    def test_run_tool(
        self,
        tool_name,
        tool_schema,
        params,
        response_text,
        expected_in_response,
        monkeypatch,
        suppress_migration_warnings,
    ):
        tool = Tool(
            name=tool_name,
            inputSchema=tool_schema,
            description="Test tool",
        )

        mock_toolset = RemoteMCPToolset(
            name="test_toolset",
            description="Test toolset",
            config={
                "url": "http://localhost:1234/mcp/messages",
                "mode": "streamable-http",
            },
        )

        async def mock_get_server_tools():
            return ListToolsResult(tools=[])

        monkeypatch.setattr(mock_toolset, "_get_server_tools", mock_get_server_tools)
        mock_toolset.prerequisites_callable(config=mock_toolset.config)

        mcp_tool = RemoteMCPTool.create(tool, mock_toolset)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)
        call_tool_result = CallToolResult(
            content=[TextContent(type="text", text=response_text)],
            isError=False,
        )
        mock_session.call_tool = AsyncMock(return_value=call_tool_result)

        mock_client_context, mock_session_context = self._setup_mocks(mock_session)
        client_patch, session_patch = self._patch_clients(
            mock_client_context, mock_session_context
        )

        with client_patch, session_patch:
            result = asyncio.run(mcp_tool._invoke_async(params))

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert response_text in result.data
        for expected in expected_in_response:
            assert expected in result.data

    def test_list_tools(self, monkeypatch, suppress_migration_warnings):
        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)

        tool1 = Tool(
            name="tool1",
            inputSchema={"type": "object", "properties": {}, "required": []},
            description="First tool",
        )
        tool2 = Tool(
            name="tool2",
            inputSchema={"type": "object", "properties": {}, "required": []},
            description="Second tool",
        )
        list_tools_result = ListToolsResult(tools=[tool1, tool2])
        mock_session.list_tools = AsyncMock(return_value=list_tools_result)

        mock_client_context, mock_session_context = self._setup_mocks(mock_session)
        client_patch, session_patch = self._patch_clients(
            mock_client_context, mock_session_context
        )

        mock_toolset = RemoteMCPToolset(
            name="test_toolset",
            description="Test toolset",
            config={
                "url": "http://localhost:1234/mcp/messages",
                "mode": "streamable-http",
            },
        )

        async def mock_get_server_tools():
            return list_tools_result

        monkeypatch.setattr(mock_toolset, "_get_server_tools", mock_get_server_tools)

        with client_patch, session_patch:
            mock_toolset.prerequisites_callable(config=mock_toolset.config)

            async def run_test():
                async with get_initialized_mcp_session(mock_toolset) as session:
                    return await session.list_tools()

            result = asyncio.run(run_test())

        assert result == list_tools_result
        assert len(result.tools) == 2
        assert result.tools[0].name == "tool1"
        assert result.tools[1].name == "tool2"


class TestSSE:
    def _setup_mocks(self, mock_session):
        mock_read_stream = AsyncMock()
        mock_write_stream = AsyncMock()

        mock_client_context = AsyncMock()
        mock_client_context.__aenter__ = AsyncMock(
            return_value=(mock_read_stream, mock_write_stream)
        )
        mock_client_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_context = AsyncMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)

        return mock_client_context, mock_session_context

    def _patch_clients(self, mock_client_context, mock_session_context):
        return patch(
            "holmes.plugins.toolsets.mcp.toolset_mcp.sse_client",
            return_value=mock_client_context,
        ), patch(
            "holmes.plugins.toolsets.mcp.toolset_mcp.ClientSession",
            return_value=mock_session_context,
        )

    @pytest.mark.parametrize(
        "tool_name,tool_schema,params,response_text,expected_in_response",
        [
            (
                "list_authorizations",
                {"type": "object", "properties": {}, "required": []},
                {},
                '{"ok": true, "authorizations": [{"authorization_id": "auth_default_001", "status": "authorized", "amount": 150.0, "currency": "USD", "merchant_id": "merchant_001", "card_last4": "4242"}], "count": 1, "authorization_ids": ["auth_default_001"]}',
                ["auth_default_001"],
            ),
            (
                "authorize_payment",
                {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "number"},
                        "currency": {"type": "string"},
                        "card_last4": {"type": "string"},
                        "merchant_id": {"type": "string"},
                    },
                    "required": ["amount", "currency", "card_last4", "merchant_id"],
                },
                {
                    "amount": 100.0,
                    "currency": "USD",
                    "card_last4": "1234",
                    "merchant_id": "test-merchant",
                },
                '{"ok": true, "authorization_id": "auth_test_456", "status": "authorized"}',
                ["auth_test_456", "authorized"],
            ),
        ],
    )
    def test_run_tool(
        self,
        tool_name,
        tool_schema,
        params,
        response_text,
        expected_in_response,
        monkeypatch,
        suppress_migration_warnings,
    ):
        tool = Tool(
            name=tool_name,
            inputSchema=tool_schema,
            description="Test tool",
        )

        mock_toolset = RemoteMCPToolset(
            name="test_toolset",
            description="Test toolset",
            config={"url": "http://localhost:1234/sse", "mode": "sse"},
        )

        async def mock_get_server_tools():
            return ListToolsResult(tools=[])

        monkeypatch.setattr(mock_toolset, "_get_server_tools", mock_get_server_tools)
        mock_toolset.prerequisites_callable(config=mock_toolset.config)

        mcp_tool = RemoteMCPTool.create(tool, mock_toolset)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)
        call_tool_result = CallToolResult(
            content=[TextContent(type="text", text=response_text)],
            isError=False,
        )
        mock_session.call_tool = AsyncMock(return_value=call_tool_result)

        mock_client_context, mock_session_context = self._setup_mocks(mock_session)
        client_patch, session_patch = self._patch_clients(
            mock_client_context, mock_session_context
        )

        with client_patch, session_patch:
            result = asyncio.run(mcp_tool._invoke_async(params))

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert response_text in result.data
        for expected in expected_in_response:
            assert expected in result.data

    def test_list_tools(self, monkeypatch, suppress_migration_warnings):
        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)

        tool1 = Tool(
            name="tool1",
            inputSchema={"type": "object", "properties": {}, "required": []},
            description="First tool",
        )
        tool2 = Tool(
            name="tool2",
            inputSchema={"type": "object", "properties": {}, "required": []},
            description="Second tool",
        )
        list_tools_result = ListToolsResult(tools=[tool1, tool2])
        mock_session.list_tools = AsyncMock(return_value=list_tools_result)

        mock_client_context, mock_session_context = self._setup_mocks(mock_session)
        client_patch, session_patch = self._patch_clients(
            mock_client_context, mock_session_context
        )

        mock_toolset = RemoteMCPToolset(
            name="test_toolset",
            description="Test toolset",
            config={"url": "http://localhost:1234/sse", "mode": "sse"},
        )

        async def mock_get_server_tools():
            return list_tools_result

        monkeypatch.setattr(mock_toolset, "_get_server_tools", mock_get_server_tools)

        with client_patch, session_patch:
            mock_toolset.prerequisites_callable(config=mock_toolset.config)

            async def run_test():
                async with get_initialized_mcp_session(mock_toolset) as session:
                    return await session.list_tools()

            result = asyncio.run(run_test())

        assert result == list_tools_result
        assert len(result.tools) == 2
        assert result.tools[0].name == "tool1"
        assert result.tools[1].name == "tool2"


class TestContextManagerCleanup:
    """
    Test that the context manager closes the client and session correctly since we are using async context managers.
    This is important to avoid resource leaks and ensure that the client and session are properly closed.
    """

    def _create_mock_session(self, call_tool_result=None, call_tool_side_effect=None):
        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)
        if call_tool_side_effect:
            mock_session.call_tool = AsyncMock(side_effect=call_tool_side_effect)
        elif call_tool_result:
            mock_session.call_tool = AsyncMock(return_value=call_tool_result)
        return mock_session

    def _create_mock_client_context(self, return_value):
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=return_value)
        mock_exit = AsyncMock(return_value=None)
        mock_context.__aexit__ = mock_exit
        return mock_context, mock_exit

    def _create_mock_session_context(self, session):
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=session)
        mock_exit = AsyncMock(return_value=None)
        mock_context.__aexit__ = mock_exit
        return mock_context, mock_exit

    def _verify_exit_called_with_no_exception(self, client_exit, session_exit):
        client_exit.assert_called_once()
        session_exit.assert_called_once()

        client_args = client_exit.call_args[0]
        session_args = session_exit.call_args[0]

        assert client_args[0] is None
        assert client_args[1] is None
        assert client_args[2] is None

        assert session_args[0] is None
        assert session_args[1] is None
        assert session_args[2] is None

    def _verify_exit_called_with_exception(
        self, client_exit, session_exit, exc_type, exc_val
    ):
        client_exit.assert_called_once()
        session_exit.assert_called_once()

        client_args = client_exit.call_args[0]
        session_args = session_exit.call_args[0]

        assert client_args[0] == exc_type
        assert client_args[1] == exc_val
        assert client_args[2] is not None

        assert session_args[0] == exc_type
        assert session_args[1] == exc_val
        assert session_args[2] is not None

    def test_sse_session_closes_on_success(self):
        mock_read_stream = AsyncMock()
        mock_write_stream = AsyncMock()
        mock_session = self._create_mock_session(
            call_tool_result=CallToolResult(
                content=[TextContent(type="text", text="test")], isError=False
            )
        )

        mock_sse_context, mock_sse_exit = self._create_mock_client_context(
            (mock_read_stream, mock_write_stream)
        )
        mock_session_context, mock_session_exit = self._create_mock_session_context(
            mock_session
        )

        with patch(
            "holmes.plugins.toolsets.mcp.toolset_mcp.sse_client",
            return_value=mock_sse_context,
        ):
            with patch(
                "holmes.plugins.toolsets.mcp.toolset_mcp.ClientSession",
                return_value=mock_session_context,
            ):
                from pydantic import AnyUrl

                mock_toolset = RemoteMCPToolset(
                    name="test_toolset",
                    description="Test toolset",
                    config={"url": "http://localhost:1234/sse", "mode": "sse"},
                )
                mock_toolset._mcp_config = MCPConfig(
                    url=AnyUrl("http://localhost:1234/sse"), mode=MCPMode.SSE
                )

                async def run_test():
                    async with get_initialized_mcp_session(mock_toolset) as session:
                        await session.call_tool("test", {})

                asyncio.run(run_test())

        self._verify_exit_called_with_no_exception(mock_sse_exit, mock_session_exit)

    def test_streamable_http_session_closes_on_success(self):
        mock_read_stream = AsyncMock()
        mock_write_stream = AsyncMock()
        mock_session = self._create_mock_session()
        mock_session.list_tools = AsyncMock(return_value=ListToolsResult(tools=[]))

        mock_streamable_context, mock_streamable_exit = (
            self._create_mock_client_context(
                (mock_read_stream, mock_write_stream, None)
            )
        )
        mock_session_context, mock_session_exit = self._create_mock_session_context(
            mock_session
        )

        with patch(
            "holmes.plugins.toolsets.mcp.toolset_mcp.streamablehttp_client",
            return_value=mock_streamable_context,
        ):
            with patch(
                "holmes.plugins.toolsets.mcp.toolset_mcp.ClientSession",
                return_value=mock_session_context,
            ):
                mock_toolset = RemoteMCPToolset(
                    name="test_toolset",
                    description="Test toolset",
                    config={
                        "url": "http://localhost:1234/mcp/messages",
                        "mode": "streamable-http",
                    },
                )
                from pydantic import AnyUrl

                from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig

                mock_toolset._mcp_config = MCPConfig(
                    url=AnyUrl("http://localhost:1234/mcp/messages"),
                    mode=MCPMode.STREAMABLE_HTTP,
                )

                async def run_test():
                    async with get_initialized_mcp_session(mock_toolset) as session:
                        await session.list_tools()

                asyncio.run(run_test())

        self._verify_exit_called_with_no_exception(
            mock_streamable_exit, mock_session_exit
        )

    def test_sse_session_closes_on_exception(self):
        test_error = RuntimeError("Test error")
        mock_read_stream = AsyncMock()
        mock_write_stream = AsyncMock()
        mock_session = self._create_mock_session(call_tool_side_effect=test_error)

        mock_sse_context, mock_sse_exit = self._create_mock_client_context(
            (mock_read_stream, mock_write_stream)
        )
        mock_session_context, mock_session_exit = self._create_mock_session_context(
            mock_session
        )

        with patch(
            "holmes.plugins.toolsets.mcp.toolset_mcp.sse_client",
            return_value=mock_sse_context,
        ):
            with patch(
                "holmes.plugins.toolsets.mcp.toolset_mcp.ClientSession",
                return_value=mock_session_context,
            ):
                mock_toolset = RemoteMCPToolset(
                    name="test_toolset",
                    description="Test toolset",
                    config={"url": "http://localhost:1234/sse", "mode": "sse"},
                )
                from pydantic import AnyUrl

                from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig

                mock_toolset._mcp_config = MCPConfig(
                    url=AnyUrl("http://localhost:1234/sse"), mode=MCPMode.SSE
                )

                async def run_test():
                    try:
                        async with get_initialized_mcp_session(mock_toolset) as session:
                            await session.call_tool("test", {})
                    except RuntimeError:
                        pass

                asyncio.run(run_test())

        self._verify_exit_called_with_exception(
            mock_sse_exit, mock_session_exit, RuntimeError, test_error
        )


class TestStdio:
    def _setup_mocks(self, mock_session):
        mock_read_stream = AsyncMock()
        mock_write_stream = AsyncMock()

        mock_client_context = AsyncMock()
        mock_client_context.__aenter__ = AsyncMock(
            return_value=(mock_read_stream, mock_write_stream)
        )
        mock_client_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_context = AsyncMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)

        return mock_client_context, mock_session_context

    def _patch_clients(self, mock_client_context, mock_session_context):
        return patch(
            "holmes.plugins.toolsets.mcp.toolset_mcp.stdio_client",
            return_value=mock_client_context,
        ), patch(
            "holmes.plugins.toolsets.mcp.toolset_mcp.ClientSession",
            return_value=mock_session_context,
        )

    @pytest.mark.parametrize(
        "tool_name,tool_schema,params,response_text,expected_in_response",
        [
            (
                "echo",
                {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                    },
                    "required": ["message"],
                },
                {"message": "Hello, World!"},
                "Hello, World!",
                ["Hello, World!"],
            ),
            (
                "add",
                {
                    "type": "object",
                    "properties": {
                        "a": {"type": "number"},
                        "b": {"type": "number"},
                    },
                    "required": ["a", "b"],
                },
                {"a": 5, "b": 3},
                "8",
                ["8"],
            ),
        ],
    )
    def test_run_tool(
        self,
        tool_name,
        tool_schema,
        params,
        response_text,
        expected_in_response,
        monkeypatch,
        suppress_migration_warnings,
    ):
        tool = Tool(
            name=tool_name,
            inputSchema=tool_schema,
            description="Test tool",
        )

        mock_toolset = RemoteMCPToolset(
            name="test_toolset",
            description="Test toolset",
            config={
                "mode": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-everything"],
            },
        )

        async def mock_get_server_tools():
            return ListToolsResult(tools=[])

        monkeypatch.setattr(mock_toolset, "_get_server_tools", mock_get_server_tools)
        mock_toolset.prerequisites_callable(config=mock_toolset.config)

        mcp_tool = RemoteMCPTool.create(tool, mock_toolset)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)
        call_tool_result = CallToolResult(
            content=[TextContent(type="text", text=response_text)],
            isError=False,
        )
        mock_session.call_tool = AsyncMock(return_value=call_tool_result)

        mock_client_context, mock_session_context = self._setup_mocks(mock_session)
        client_patch, session_patch = self._patch_clients(
            mock_client_context, mock_session_context
        )

        with client_patch, session_patch:
            result = asyncio.run(mcp_tool._invoke_async(params))

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert response_text in result.data
        for expected in expected_in_response:
            assert expected in result.data

    def test_list_tools(self, monkeypatch, suppress_migration_warnings):
        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)

        tool1 = Tool(
            name="echo",
            inputSchema={"type": "object", "properties": {}, "required": []},
            description="Echo tool",
        )
        tool2 = Tool(
            name="add",
            inputSchema={"type": "object", "properties": {}, "required": []},
            description="Add tool",
        )
        list_tools_result = ListToolsResult(tools=[tool1, tool2])
        mock_session.list_tools = AsyncMock(return_value=list_tools_result)

        mock_client_context, mock_session_context = self._setup_mocks(mock_session)
        client_patch, session_patch = self._patch_clients(
            mock_client_context, mock_session_context
        )

        mock_toolset = RemoteMCPToolset(
            name="test_toolset",
            description="Test toolset",
            config={
                "mode": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-everything"],
            },
        )

        async def mock_get_server_tools():
            return list_tools_result

        monkeypatch.setattr(mock_toolset, "_get_server_tools", mock_get_server_tools)

        with client_patch, session_patch:
            mock_toolset.prerequisites_callable(config=mock_toolset.config)

            async def run_test():
                async with get_initialized_mcp_session(mock_toolset) as session:
                    return await session.list_tools()

            result = asyncio.run(run_test())

        assert result == list_tools_result
        assert len(result.tools) == 2
        assert result.tools[0].name == "echo"
        assert result.tools[1].name == "add"

    def test_stdio_config_requires_command(self, suppress_migration_warnings):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
        )

        result = mcp_toolset.prerequisites_callable(config={"mode": "stdio"})
        assert result[0] is False
        assert (
            "validation error for StdioMCPConfig\ncommand\n  Field required"
            in result[1]
        )

    def test_stdio_mode_works(self, monkeypatch, suppress_migration_warnings):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
        )

        async def mock_get_server_tools():
            return ListToolsResult(tools=[])

        monkeypatch.setattr(mcp_toolset, "_get_server_tools", mock_get_server_tools)
        result = mcp_toolset.prerequisites_callable(
            config={
                "mode": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-everything"],
            }
        )
        assert result[0] is True
        assert isinstance(mcp_toolset._mcp_config, StdioMCPConfig)
        assert mcp_toolset._mcp_config.command == "npx"
        assert mcp_toolset._mcp_config.args == [
            "-y",
            "@modelcontextprotocol/server-everything",
        ]

    def test_stdio_with_env_vars(self, monkeypatch, suppress_migration_warnings):
        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
        )

        async def mock_get_server_tools():
            return ListToolsResult(tools=[])

        monkeypatch.setattr(mcp_toolset, "_get_server_tools", mock_get_server_tools)
        result = mcp_toolset.prerequisites_callable(
            config={
                "mode": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-everything"],
                "env": {"NODE_ENV": "test", "DEBUG": "1"},
            }
        )
        assert result[0] is True
        assert isinstance(mcp_toolset._mcp_config, StdioMCPConfig)
        assert mcp_toolset._mcp_config.env == {"NODE_ENV": "test", "DEBUG": "1"}

    def test_everything_stdio_config_from_yaml(self, suppress_migration_warnings):
        """Test loading everything_stdio MCP server config matching the YAML example"""
        import os

        # Config matching the YAML structure - using Python stdio server
        server_path = os.path.join(os.path.dirname(__file__), "stdio_server.py")
        yaml_config = {
            "mode": "stdio",
            "command": "python",
            "args": [server_path],
        }

        mcp_toolset = RemoteMCPToolset(
            name="everything_stdio",
            description="MCP Example stdio server (Python FastMCP server)",
            config=yaml_config,
        )

        # Test initialization - this will actually connect to the real MCP server
        result = mcp_toolset.prerequisites_callable(config=yaml_config)

        if not result[0]:
            print(f"Error: {result[1]}")
        assert result[0] is True, f"Failed to initialize MCP server: {result[1]}"
        assert isinstance(mcp_toolset._mcp_config, StdioMCPConfig)
        assert mcp_toolset._mcp_config.command == "python"
        assert mcp_toolset._mcp_config.args == [server_path]
        # Verify that tools were actually loaded from the real server
        assert len(mcp_toolset.tools) > 0
        # Check for expected tools from the Python server
        tool_names = [tool.name for tool in mcp_toolset.tools]
        assert "greet" in tool_names
        assert "add" in tool_names

    def test_everything_stdio_tool_invocation(self, suppress_migration_warnings):
        """Test invoking a tool from everything_stdio MCP server"""
        import os

        server_path = os.path.join(os.path.dirname(__file__), "stdio_server.py")
        yaml_config = {
            "mode": "stdio",
            "command": "python",
            "args": [server_path],
        }

        toolset = RemoteMCPToolset(
            name="everything_stdio",
            description="MCP Example stdio server (Python FastMCP server)",
            config=yaml_config,
        )

        # Initialize the toolset - this will actually connect to the real MCP server
        result = toolset.prerequisites_callable(config=yaml_config)
        assert result[0] is True

        # Find the greet tool from the real server
        greet_tool = None
        for tool in toolset.tools:
            if tool.name == "greet":
                greet_tool = tool
                break

        if greet_tool is None:
            pytest.skip("greet tool not found in MCP server")

        # Actually invoke the tool on the real server with timeout
        try:
            invoke_result = greet_tool._invoke({"name": "Alice"}, None)
        except Exception as e:
            pytest.fail(f"Tool invocation failed: {e}")

        assert invoke_result.status == StructuredToolResultStatus.SUCCESS
        assert "Alice" in invoke_result.data
        assert "Hello" in invoke_result.data

    def test_everything_stdio_list_tools(self, suppress_migration_warnings):
        """Test listing tools from everything_stdio MCP server"""
        import os

        server_path = os.path.join(os.path.dirname(__file__), "stdio_server.py")
        yaml_config = {
            "mode": "stdio",
            "command": "python",
            "args": [server_path],
        }

        toolset = RemoteMCPToolset(
            name="everything_stdio",
            description="MCP Example stdio server (Python FastMCP server)",
            config=yaml_config,
        )

        # Initialize the toolset - this will actually connect to the real MCP server
        result = toolset.prerequisites_callable(config=yaml_config)
        assert result[0] is True

        # Actually list tools from the real server with timeout
        async def run_test():
            async with get_initialized_mcp_session(toolset) as session:
                return await asyncio.wait_for(session.list_tools(), timeout=30.0)

        list_result = asyncio.run(run_test())

        # Verify we got tools from the real server
        assert len(list_result.tools) > 0

        # Check for expected tools from the Python server
        tool_names = [tool.name for tool in list_result.tools]
        assert "greet" in tool_names
        assert "add" in tool_names

        # Verify the tools loaded in the toolset match what we got from list_tools
        assert len(toolset.tools) == len(list_result.tools)

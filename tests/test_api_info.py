from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server import app


@pytest.fixture
def client():
    """TestClient wrapping the main FastAPI app."""
    return TestClient(app)


def _mock_toolset(name, enabled=True, status="enabled", ts_type="built-in", error=None, tool_count=2):
    """Build a mock Toolset with the fields the /api/info endpoint reads."""
    from holmes.core.tools import ToolsetStatusEnum, ToolsetType

    t = MagicMock()
    t.name = name
    t.enabled = enabled
    t.status = ToolsetStatusEnum(status)
    t.type = ToolsetType(ts_type) if ts_type else None
    t.error = error
    t.tools = [MagicMock() for _ in range(tool_count)]
    return t


def _mock_runbook_toolset(runbook_names):
    """Build a mock 'runbook' toolset whose first tool exposes available_runbooks."""
    ts = _mock_toolset("runbook", enabled=True, status="enabled", tool_count=1)
    ts.tools[0].available_runbooks = runbook_names
    return ts


class TestInfoDefault:
    """Tests for GET /api/info (default mode, no detail param)."""

    @patch("server.config")
    def test_returns_basic_fields(self, mock_config, client):
        """Default response includes version, uptime, models, toolsets_summary, runbooks_count."""
        toolsets = [
            _mock_toolset("prometheus/metrics"),
            _mock_toolset("grafana/dashboards", status="failed", error="Connection refused"),
            _mock_toolset("kubernetes/logs", enabled=False, status="disabled"),
            _mock_runbook_toolset(["cpu_high.md", "oom_killer.md"]),
        ]
        executor = MagicMock()
        executor.toolsets = toolsets
        mock_config.create_tool_executor.return_value = executor
        mock_config.get_models_list.return_value = ["gpt-4.1", "claude-sonnet-4"]

        response = client.get("/api/info")
        assert response.status_code == 200

        data = response.json()
        assert "version" in data
        assert data["uptime_seconds"] >= 0
        assert data["models"] == ["gpt-4.1", "claude-sonnet-4"]
        assert data["toolsets_summary"]["total"] == 4
        assert data["toolsets_summary"]["enabled"] == 2  # prometheus + runbook
        assert data["toolsets_summary"]["failed"] == 1
        assert data["toolsets_summary"]["disabled"] == 1
        assert data["runbooks_count"] == 2

    @patch("server.config")
    def test_excludes_full_fields(self, mock_config, client):
        """Default mode must not include toolsets list, runbooks, mcp_servers, or paths."""
        executor = MagicMock()
        executor.toolsets = [_mock_toolset("test")]
        mock_config.create_tool_executor.return_value = executor
        mock_config.get_models_list.return_value = []

        data = client.get("/api/info").json()
        assert "toolsets" not in data
        assert "runbooks" not in data
        assert "mcp_servers" not in data
        assert "config_path" not in data
        assert "model_list_path" not in data


class TestInfoFull:
    """Tests for GET /api/info?detail=full."""

    @patch("server.config")
    def test_includes_toolsets_list(self, mock_config, client):
        """Full mode returns per-toolset details."""
        toolsets = [
            _mock_toolset("prometheus/metrics", tool_count=5),
            _mock_toolset("grafana/dashboards", status="failed", error="Connection refused", tool_count=3),
        ]
        executor = MagicMock()
        executor.toolsets = toolsets
        mock_config.create_tool_executor.return_value = executor
        mock_config.get_models_list.return_value = ["gpt-4.1"]
        mock_config._config_file_path = "/app/config.yaml"
        mock_config.mcp_servers = {"my-server": {}}

        with patch("server.MODEL_LIST_FILE_LOCATION", "/app/model_list.yaml"):
            response = client.get("/api/info?detail=full")

        assert response.status_code == 200
        data = response.json()

        assert len(data["toolsets"]) == 2
        prom = data["toolsets"][0]
        assert prom["name"] == "prometheus/metrics"
        assert prom["enabled"] is True
        assert prom["status"] == "enabled"
        assert prom["type"] == "built-in"
        assert prom.get("error") is None
        assert prom["tool_count"] == 5

        graf = data["toolsets"][1]
        assert graf["status"] == "failed"
        assert graf["error"] == "Connection refused"

    @patch("server.config")
    def test_includes_runbook_names(self, mock_config, client):
        """Full mode lists individual runbook names."""
        toolsets = [_mock_runbook_toolset(["cpu_high.md", "oom.md", "lag.md"])]
        executor = MagicMock()
        executor.toolsets = toolsets
        mock_config.create_tool_executor.return_value = executor
        mock_config.get_models_list.return_value = []
        mock_config._config_file_path = None
        mock_config.mcp_servers = None

        data = client.get("/api/info?detail=full").json()
        assert data["runbooks"] == ["cpu_high.md", "oom.md", "lag.md"]
        assert data["runbooks_count"] == 3

    @patch("server.config")
    def test_includes_mcp_servers(self, mock_config, client):
        """Full mode lists MCP server names."""
        executor = MagicMock()
        executor.toolsets = []
        mock_config.create_tool_executor.return_value = executor
        mock_config.get_models_list.return_value = []
        mock_config._config_file_path = None
        mock_config.mcp_servers = {"server-a": {}, "server-b": {}}

        data = client.get("/api/info?detail=full").json()
        assert sorted(data["mcp_servers"]) == ["server-a", "server-b"]

    @patch("server.config")
    def test_includes_config_paths(self, mock_config, client):
        """Full mode exposes config_path and model_list_path."""
        executor = MagicMock()
        executor.toolsets = []
        mock_config.create_tool_executor.return_value = executor
        mock_config.get_models_list.return_value = []
        mock_config._config_file_path = "/etc/holmes/config.yaml"
        mock_config.mcp_servers = None

        with patch("server.MODEL_LIST_FILE_LOCATION", "/app/model_list.yaml"):
            data = client.get("/api/info?detail=full").json()

        assert data["config_path"] == "/etc/holmes/config.yaml"
        assert data["model_list_path"] == "/app/model_list.yaml"


class TestInfoAuthEnabled:
    """Verify auth_enabled reflects HOLMES_API_KEY env var."""

    @patch("server.config")
    def test_auth_disabled(self, mock_config, client, monkeypatch):
        """auth_enabled is false when HOLMES_API_KEY is unset."""
        monkeypatch.delenv("HOLMES_API_KEY", raising=False)
        executor = MagicMock()
        executor.toolsets = []
        mock_config.create_tool_executor.return_value = executor
        mock_config.get_models_list.return_value = []

        data = client.get("/api/info").json()
        assert data["auth_enabled"] is False

    @patch("server.config")
    def test_auth_enabled(self, mock_config, client, monkeypatch):
        """auth_enabled is true when HOLMES_API_KEY is set."""
        monkeypatch.setenv("HOLMES_API_KEY", "test-key-123")
        executor = MagicMock()
        executor.toolsets = []
        mock_config.create_tool_executor.return_value = executor
        mock_config.get_models_list.return_value = []

        data = client.get("/api/info").json()
        assert data["auth_enabled"] is True

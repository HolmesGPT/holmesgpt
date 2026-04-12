from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from holmes.core.tools import Toolset, ToolsetStatusEnum, ToolsetTag, ToolsetType
from server import app


@pytest.fixture
def client():
    return TestClient(app)


def _make_fake_toolset(name: str, status: ToolsetStatusEnum = ToolsetStatusEnum.DISABLED) -> Toolset:
    """Create a minimal Toolset for testing."""
    return Toolset(
        name=name,
        description=f"Test toolset {name}",
        tools=[],
        enabled=False,
        status=status,
        tags=[ToolsetTag.CORE],
    )


class TestValidateToolset:
    def test_invalid_yaml_returns_400(self, client):
        response = client.post(
            "/api/toolsets/validate",
            json={"yaml_config": "invalid: yaml: [[["},
        )
        assert response.status_code == 400
        assert "Invalid YAML" in response.json()["detail"]

    def test_non_dict_yaml_returns_400(self, client):
        response = client.post(
            "/api/toolsets/validate",
            json={"yaml_config": "- just a list"},
        )
        assert response.status_code == 400
        assert "dictionary" in response.json()["detail"]

    def test_empty_toolsets_returns_400(self, client):
        response = client.post(
            "/api/toolsets/validate",
            json={"yaml_config": "holmes:\n  toolsets: {}"},
        )
        assert response.status_code == 400
        assert "No toolsets or mcp_servers" in response.json()["detail"]

    @patch("holmes.toolsets.toolsets_api.ToolsetManager.check_toolset_prerequisites")
    @patch("holmes.toolsets.toolsets_api.load_builtin_toolsets")
    def test_builtin_toolset_valid(self, mock_load_builtins, mock_check_prereqs, client):
        fake_toolset = _make_fake_toolset("elasticsearch/data")
        mock_load_builtins.return_value = [fake_toolset]

        def set_enabled(toolsets, silent=False):
            for ts in toolsets:
                ts.status = ToolsetStatusEnum.ENABLED

        mock_check_prereqs.side_effect = set_enabled

        yaml_config = """
holmes:
  toolsets:
    elasticsearch/data:
      enabled: true
      config:
        api_url: "http://localhost:9200"
"""
        response = client.post(
            "/api/toolsets/validate",
            json={"yaml_config": yaml_config},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["toolset_name"] == "elasticsearch/data"
        assert data["results"][0]["status"] == "valid"
        assert data["results"][0]["error"] is None

    @patch("holmes.toolsets.toolsets_api.ToolsetManager.check_toolset_prerequisites")
    @patch("holmes.toolsets.toolsets_api.load_builtin_toolsets")
    def test_builtin_toolset_invalid(self, mock_load_builtins, mock_check_prereqs, client):
        fake_toolset = _make_fake_toolset("elasticsearch/data")
        mock_load_builtins.return_value = [fake_toolset]

        def set_failed(toolsets, silent=False):
            for ts in toolsets:
                ts.status = ToolsetStatusEnum.FAILED
                ts.error = "Connection refused"

        mock_check_prereqs.side_effect = set_failed

        yaml_config = """
holmes:
  toolsets:
    elasticsearch/data:
      enabled: true
      config:
        api_url: "http://bad-host:9200"
"""
        response = client.post(
            "/api/toolsets/validate",
            json={"yaml_config": yaml_config},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "invalid"
        assert data["results"][0]["error"] == "Connection refused"

    @patch("holmes.toolsets.toolsets_api.ToolsetManager.check_toolset_prerequisites")
    @patch("holmes.toolsets.toolsets_api.load_toolsets_from_config")
    @patch("holmes.toolsets.toolsets_api.load_builtin_toolsets")
    def test_mcp_server(self, mock_load_builtins, mock_load_from_config, mock_check_prereqs, client):
        mock_load_builtins.return_value = []

        mcp_toolset = _make_fake_toolset("my-mcp-server")
        mcp_toolset.type = ToolsetType.MCP
        mock_load_from_config.return_value = [mcp_toolset]

        def set_enabled(toolsets, silent=False):
            for ts in toolsets:
                ts.status = ToolsetStatusEnum.ENABLED

        mock_check_prereqs.side_effect = set_enabled

        yaml_config = """
holmes:
  mcp_servers:
    my-mcp-server:
      description: "My custom MCP server"
      config:
        url: "https://www.mcp.com"
        mode: streamable-http
"""
        response = client.post(
            "/api/toolsets/validate",
            json={"yaml_config": yaml_config},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["toolset_name"] == "my-mcp-server"
        assert data["results"][0]["status"] == "valid"

    def test_yaml_without_holmes_wrapper(self, client):
        """YAML without the 'holmes:' wrapper should also be accepted."""
        response = client.post(
            "/api/toolsets/validate",
            json={"yaml_config": "toolsets: {}"},
        )
        # Should get 400 for empty toolsets, not a parse error
        assert response.status_code == 400
        assert "No toolsets or mcp_servers" in response.json()["detail"]


class TestTriggerToolsetRefresh:
    def test_returns_refresh_triggered(self, client):
        response = client.post("/api/toolsets/refresh")
        assert response.status_code == 200
        assert response.json() == {"status": "refresh_triggered"}

    @patch("holmes.toolsets.toolsets_api._REFRESH_EVENT")
    def test_sets_refresh_event(self, mock_event, client):
        response = client.post("/api/toolsets/refresh")
        assert response.status_code == 200
        mock_event.set.assert_called_once()

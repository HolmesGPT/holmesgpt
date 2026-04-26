import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from holmes.plugins.toolsets.scout.toolset_scout import ScoutConfig, ScoutToolset

_has_scout_credentials = bool(
    os.environ.get("SCOUT_API_URL")
    and (
        os.environ.get("SCOUT_API_TOKEN")
        or (os.environ.get("SCOUT_CLIENT_ID") and os.environ.get("SCOUT_CLIENT_SECRET"))
    )
)


class TestScoutConfig:
    def test_valid_config_with_token(self):
        config = ScoutConfig(
            api_url="https://scout.example.com/mcp/v1",
            api_token="test-token-123",
        )
        assert config.api_url == "https://scout.example.com/mcp/v1"
        assert config.api_token == "test-token-123"
        assert config.client_id is None
        assert config.client_secret is None

    def test_valid_config_with_client_credentials(self):
        config = ScoutConfig(
            api_url="https://scout.example.com/mcp/v1",
            client_id="my-client",
            client_secret="my-secret",
        )
        assert config.client_id == "my-client"
        assert config.client_secret == "my-secret"
        assert config.api_token is None

    def test_api_url_required(self):
        with pytest.raises(ValidationError):
            ScoutConfig(api_token="test-token")

    def test_default_mode_is_streamable_http(self):
        config = ScoutConfig(
            api_url="https://scout.example.com/mcp/v1",
            api_token="token",
        )
        assert config.mode.value == "streamable-http"

    def test_verify_ssl_default_true(self):
        config = ScoutConfig(
            api_url="https://scout.example.com/mcp/v1",
            api_token="token",
        )
        assert config.verify_ssl is True


class TestScoutToolset:
    def test_toolset_name(self):
        toolset = ScoutToolset()
        assert toolset.name == "base14/scout"

    def test_toolset_disabled_by_default(self):
        toolset = ScoutToolset()
        assert toolset.enabled is False

    def test_prerequisites_fails_without_auth(self, monkeypatch):
        monkeypatch.delenv("SCOUT_API_TOKEN", raising=False)
        monkeypatch.delenv("SCOUT_CLIENT_ID", raising=False)
        monkeypatch.delenv("SCOUT_CLIENT_SECRET", raising=False)
        toolset = ScoutToolset()
        ok, msg = toolset.prerequisites_callable({"api_url": "https://scout.example.com/mcp/v1"})
        assert ok is False
        assert "api_token" in msg or "client_id" in msg

    def test_prerequisites_fails_with_empty_config(self, monkeypatch):
        monkeypatch.delenv("SCOUT_API_URL", raising=False)
        monkeypatch.delenv("SCOUT_API_TOKEN", raising=False)
        monkeypatch.delenv("SCOUT_CLIENT_ID", raising=False)
        monkeypatch.delenv("SCOUT_CLIENT_SECRET", raising=False)
        toolset = ScoutToolset()
        ok, _ = toolset.prerequisites_callable({})
        assert ok is False

    def test_prerequisites_token_delegates_to_parent(self, monkeypatch):
        """Happy path: api_token resolves and is passed to RemoteMCPToolset."""
        monkeypatch.delenv("SCOUT_API_TOKEN", raising=False)
        monkeypatch.delenv("SCOUT_CLIENT_ID", raising=False)
        monkeypatch.delenv("SCOUT_CLIENT_SECRET", raising=False)
        toolset = ScoutToolset()

        with patch.object(
            type(toolset).__bases__[0], "prerequisites_callable", return_value=(True, "")
        ) as mock_parent:
            ok, msg = toolset.prerequisites_callable({
                "api_url": "https://scout.example.com/mcp/v1",
                "api_token": "test-bearer-token",
            })
            assert ok is True
            assert msg == ""
            # Verify parent was called with correct MCP config
            mcp_config = mock_parent.call_args[0][0]
            assert mcp_config["url"] == "https://scout.example.com/mcp/v1"
            assert mcp_config["headers"]["Authorization"] == "Bearer test-bearer-token"
            assert mcp_config["mode"] == "streamable-http"

    def test_env_vars_dont_override_explicit_auth(self, monkeypatch):
        """Env vars should not override explicit client_id/client_secret in config."""
        monkeypatch.setenv("SCOUT_API_TOKEN", "env-token-should-not-win")
        toolset = ScoutToolset()

        with patch(
            "holmes.plugins.toolsets.scout.toolset_scout._obtain_token_via_client_credentials",
            return_value="client-creds-token",
        ):
            with patch.object(
                type(toolset).__bases__[0], "prerequisites_callable", return_value=(True, "")
            ) as mock_parent:
                ok, _ = toolset.prerequisites_callable({
                    "api_url": "https://scout.example.com/mcp/v1",
                    "client_id": "my-client",
                    "client_secret": "my-secret",
                })
                assert ok is True
                mcp_config = mock_parent.call_args[0][0]
                assert mcp_config["headers"]["Authorization"] == "Bearer client-creds-token"


@pytest.mark.skipif(not _has_scout_credentials, reason="Scout credentials not set in environment")
class TestScoutIntegration:
    def test_prerequisites_against_live_instance(self):
        """End-to-end test: connects to a real Scout instance, discovers tools."""
        config = {"api_url": os.environ["SCOUT_API_URL"]}
        if os.environ.get("SCOUT_API_TOKEN"):
            config["api_token"] = os.environ["SCOUT_API_TOKEN"]
        else:
            config["client_id"] = os.environ["SCOUT_CLIENT_ID"]
            config["client_secret"] = os.environ["SCOUT_CLIENT_SECRET"]

        toolset = ScoutToolset()
        ok, msg = toolset.prerequisites_callable(config)
        assert ok is True, f"prerequisites_callable failed: {msg}"
        assert len(toolset.tools) > 0, "No tools discovered from Scout MCP server"

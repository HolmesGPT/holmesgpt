from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from holmes.admin.admin_api import init_admin_app
from holmes.config import Config


@pytest.fixture
def config_yaml_path(tmp_path):
    """Create a minimal config YAML and return its path."""
    config_data = {
        "toolsets": {
            "prometheus/metrics": {
                "enabled": True,
                "config": {"prometheus_url": "http://localhost:9090"},
            }
        },
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config_data))
    return config_file


@pytest.fixture
def config(config_yaml_path):
    """Load a Config from the temp YAML."""
    return Config.load_from_file(config_yaml_path)


class TestReloadToolsets:
    """Unit tests for Config.reload_toolsets()."""

    def test_resets_toolset_manager(self, config):
        """After reload, the cached toolset manager is cleared."""
        _ = config.toolset_manager
        assert config._toolset_manager is not None

        config.reload_toolsets()

        assert config._toolset_manager is None

    def test_resets_cached_executor(self, config):
        """After reload, the cached tool executor and key are cleared."""
        config._cached_tool_executor = MagicMock()
        config._cached_executor_key = ("fake",)

        config.reload_toolsets()

        assert config._cached_tool_executor is None
        assert config._cached_executor_key is None

    def test_picks_up_new_toolsets_from_yaml(self, config, config_yaml_path):
        """Rewriting the YAML and reloading picks up the new toolset entries."""
        assert config.toolsets is not None
        assert "prometheus/metrics" in config.toolsets

        new_config_data = {
            "toolsets": {
                "prometheus/metrics": {
                    "enabled": False,
                },
                "datadog/metrics": {
                    "enabled": True,
                    "config": {"api_key": "test"},
                },
            },
        }
        config_yaml_path.write_text(yaml.dump(new_config_data))

        config.reload_toolsets()

        assert "datadog/metrics" in config.toolsets
        assert config.toolsets["prometheus/metrics"]["enabled"] is False

    def test_picks_up_custom_skill_paths_from_yaml(self, config, config_yaml_path):
        """Rewriting the YAML and reloading picks up custom_skill_paths."""
        assert config.custom_skill_paths == []

        skill_root = config_yaml_path.parent / "extra_skills"
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_text(
            "---\nname: my-test-skill\ndescription: test skill\n---\nBody.\n",
            encoding="utf-8",
        )

        new_config_data = {
            "toolsets": {
                "prometheus/metrics": {
                    "enabled": True,
                    "config": {"prometheus_url": "http://localhost:9090"},
                }
            },
            "custom_skill_paths": [str(skill_root)],
        }
        config_yaml_path.write_text(yaml.dump(new_config_data))

        config.reload_toolsets()

        assert config.custom_skill_paths == [str(skill_root)]

    def test_returns_dict(self, config):
        """reload_toolsets returns a dict with reloaded=True."""
        result = config.reload_toolsets()
        assert isinstance(result, dict)
        assert result["reloaded"] is True

    def test_works_without_config_file(self):
        """Reload with no config file still clears caches without error."""
        config = Config()
        result = config.reload_toolsets()
        assert result["reloaded"] is True
        assert config._toolset_manager is None

    def test_invalid_yaml_raises_validation_error(self, config, config_yaml_path):
        """Unknown config keys raise ValidationError instead of calling sys.exit()."""
        config_yaml_path.write_text(yaml.dump({"toolset": {"foo": {"enabled": True}}}))

        with pytest.raises(ValidationError):
            config.reload_toolsets()

    def test_reload_preserves_custom_skill_paths_from_env(
        self, config_yaml_path, monkeypatch
    ):
        """Reload re-applies CUSTOM_SKILL_PATHS when YAML omits custom_skill_paths."""
        monkeypatch.setenv("CUSTOM_SKILL_PATHS", "/etc/holmes/skills")
        config = Config.load_from_file(config_yaml_path)
        assert config.custom_skill_paths == ["/etc/holmes/skills"]

        config.reload_toolsets()

        assert config.custom_skill_paths == ["/etc/holmes/skills"]


class TestReloadModels:
    """Unit tests for Config.reload_models()."""

    def test_resets_model_registry(self, config):
        """After reload, a fresh LLMModelRegistry instance is created."""
        def make_registry(*_args, **_kwargs):
            r = MagicMock()
            r.models = {"gpt-4": MagicMock()}
            return r

        with patch("holmes.config.LLMModelRegistry", side_effect=make_registry):
            _ = config.llm_model_registry
            old_registry = config._llm_model_registry
            assert old_registry is not None

            config.reload_models()

            new_registry = config._llm_model_registry
            assert new_registry is not old_registry

    def test_returns_model_count(self, config):
        """reload_models returns the number of models loaded."""
        with patch("holmes.config.LLMModelRegistry") as MockRegistry:
            mock_instance = MagicMock()
            mock_instance.models = {"model-a": MagicMock(), "model-b": MagicMock()}
            MockRegistry.return_value = mock_instance

            result = config.reload_models()
            assert result["models_loaded"] == 2

    def test_invalid_yaml_raises_validation_error(self, config, config_yaml_path):
        """Unknown config keys raise ValidationError instead of calling sys.exit()."""
        config_yaml_path.write_text(yaml.dump({"unknown_field": True}))

        with pytest.raises(ValidationError):
            config.reload_models()

    def test_reload_preserves_model_from_env(self, config_yaml_path, monkeypatch):
        """Reload re-applies MODEL when the mounted YAML has no model key."""
        monkeypatch.setenv("MODEL", "anthropic/claude-sonnet-4-5")
        config = Config.load_from_file(config_yaml_path)
        assert config.model == "anthropic/claude-sonnet-4-5"
        assert config._model_source == "via $MODEL"

        with patch("holmes.config.LLMModelRegistry") as MockRegistry:
            mock_instance = MagicMock()
            mock_instance.models = {}
            MockRegistry.return_value = mock_instance
            config.reload_models()

        assert config.model == "anthropic/claude-sonnet-4-5"
        assert config._model_source == "via $MODEL"

    def test_reload_keeps_yaml_model_over_env(self, config_yaml_path, monkeypatch):
        """Explicit model in YAML is not replaced by MODEL env on reload."""
        monkeypatch.setenv("MODEL", "from-env")
        config_yaml_path.write_text(
            yaml.dump(
                {
                    "model": "from-yaml",
                    "toolsets": {
                        "prometheus/metrics": {
                            "enabled": True,
                            "config": {"prometheus_url": "http://localhost:9090"},
                        }
                    },
                }
            )
        )
        config = Config.load_from_file(config_yaml_path)
        assert config.model == "from-yaml"

        with patch("holmes.config.LLMModelRegistry") as MockRegistry:
            MockRegistry.return_value = MagicMock(models={})
            config.reload_models()

        assert config.model == "from-yaml"


class TestAdminEndpoints:
    """Integration tests for the /api/admin reload endpoints."""

    @pytest.fixture
    def client(self, config):
        """Lightweight FastAPI app with the admin sub-app mounted."""
        app = FastAPI()
        init_admin_app(app, config, dal=MagicMock())
        return TestClient(app)

    @patch("holmes.config.Config.get_skill_catalog", return_value=None)
    @patch("holmes.config.Config.reload_toolsets")
    @patch("holmes.config.Config.create_tool_executor")
    def test_reload_toolsets_endpoint(self, mock_create, mock_reload, _mock_catalog, client):
        """POST /reload/toolsets returns 200 with toolset and skill counts."""
        mock_reload.return_value = {"reloaded": True}
        mock_toolset = MagicMock()
        mock_toolset.enabled = True
        mock_toolset.name = "test"
        mock_toolset.tools = []
        mock_executor = MagicMock()
        mock_executor.toolsets = [mock_toolset]
        mock_executor.enabled_toolsets = [mock_toolset]
        mock_create.return_value = mock_executor

        response = client.post("/api/admin/reload/toolsets")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["component"] == "toolsets"
        assert "counts" in data
        assert "toolsets_total" in data["counts"]
        assert "skills" in data["counts"]
        assert data["counts"]["skills"] == 0
        mock_reload.assert_called_once()

    @patch("holmes.config.Config.reload_models")
    def test_reload_models_endpoint(self, mock_reload, client):
        """POST /reload/models returns 200 with model count."""
        mock_reload.return_value = {"models_loaded": 3}

        response = client.post("/api/admin/reload/models")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["component"] == "models"
        assert data["counts"]["models_loaded"] == 3
        mock_reload.assert_called_once()

    @patch("holmes.config.Config.get_skill_catalog", return_value=None)
    @patch("holmes.config.Config.reload_models")
    @patch("holmes.config.Config.reload_toolsets")
    @patch("holmes.config.Config.create_tool_executor")
    def test_reload_all_endpoint(
        self, mock_create, mock_reload_ts, mock_reload_models, _mock_catalog, client
    ):
        """POST /reload returns 200 with toolset, skill, and model counts."""
        mock_reload_ts.return_value = {"reloaded": True}
        mock_reload_models.return_value = {"models_loaded": 2}
        mock_toolset = MagicMock()
        mock_toolset.enabled = True
        mock_toolset.name = "test"
        mock_toolset.tools = []
        mock_executor = MagicMock()
        mock_executor.toolsets = [mock_toolset]
        mock_executor.enabled_toolsets = [mock_toolset]
        mock_create.return_value = mock_executor

        response = client.post("/api/admin/reload")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["component"] == "all"
        assert "toolsets_total" in data["counts"]
        assert "skills" in data["counts"]
        assert data["counts"]["skills"] == 0
        assert "models_loaded" in data["counts"]
        mock_reload_ts.assert_called_once()
        mock_reload_models.assert_called_once()

    @patch("holmes.config.Config.reload_toolsets")
    def test_reload_toolsets_error_returns_500(self, mock_reload, client):
        """When reload_toolsets raises, the endpoint returns HTTP 500."""
        mock_reload.side_effect = RuntimeError("config file missing")

        response = client.post("/api/admin/reload/toolsets")
        assert response.status_code == 500
        assert "config file missing" in response.json()["detail"]

    @patch("holmes.config.Config.get_skill_catalog", return_value=None)
    @patch("holmes.config.Config.create_tool_executor")
    def test_reload_toolsets_validation_error_returns_500_json(
        self, mock_create, _mock_catalog, config, config_yaml_path, client
    ):
        """Invalid mounted YAML returns structured 500 detail, not a generic ASGI 500."""
        config_yaml_path.write_text(yaml.dump({"toolset": {"foo": {"enabled": True}}}))
        mock_create.return_value = MagicMock(toolsets=[], enabled_toolsets=[])

        response = client.post("/api/admin/reload/toolsets")
        assert response.status_code == 500
        detail = response.json()["detail"]
        assert "toolset" in detail.lower() or "extra" in detail.lower()

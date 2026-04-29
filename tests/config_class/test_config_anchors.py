import yaml

from holmes.config import Config


def test_anchors_field_is_accepted(tmp_path):
    """Config should not reject an 'anchors' top-level key."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
anchors:
  dd_config: &dd_config
    api_key: test-api-key
    app_key: test-app-key
    api_url: https://app.datadoghq.com

toolsets:
  datadog/general:
    enabled: true
    config: *dd_config
  datadog/logs:
    enabled: true
    config: *dd_config
"""
    )
    # Should not raise a validation error
    config = Config.load_from_file(config_file)
    assert config.anchors is not None


def test_yaml_aliases_resolve_in_toolsets(tmp_path):
    """YAML aliases in toolsets config should resolve to the same values."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
anchors:
  dd_config: &dd_config
    api_key: test-api-key
    app_key: test-app-key
    api_url: https://app.datadoghq.com

toolsets:
  datadog/general:
    enabled: true
    config: *dd_config
  datadog/logs:
    enabled: true
    config: *dd_config
"""
    )
    config = Config.load_from_file(config_file)
    assert config.toolsets is not None

    for toolset_name in ("datadog/general", "datadog/logs"):
        ts = config.toolsets[toolset_name]
        assert ts["config"]["api_key"] == "test-api-key"
        assert ts["config"]["app_key"] == "test-app-key"
        assert ts["config"]["api_url"] == "https://app.datadoghq.com"

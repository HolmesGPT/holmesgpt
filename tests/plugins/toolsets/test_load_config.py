import os

import pytest
import yaml

from holmes.plugins.toolsets import load_toolsets_from_config


def test_load_toolsets_from_config_old_format():
    old_format_data = [
        {
            "name": "aws/security",
            "prerequisites": [{"command": "aws sts get-caller-identity"}],
            "tools": [
                {
                    "name": "aws_cloudtrail_event_lookup",
                    "description": "Fetches events from AWS CloudTrail",
                    "command": "aws cloudtrail lookup-events",
                }
            ],
        }
    ]

    with pytest.raises(ValueError, match="Old toolset config format detected"):
        load_toolsets_from_config(old_format_data)


def test_load_toolsets_from_config_multiple_old_format_toolsets():
    old_format_data = [
        {
            "name": "aws/security",
            "prerequisites": [{"command": "aws sts get-caller-identity"}],
            "tools": [
                {
                    "name": "aws_cloudtrail_event_lookup",
                    "description": "Fetches events from AWS CloudTrail",
                    "command": "aws cloudtrail lookup-events",
                }
            ],
        },
        {
            "name": "kubernetes/logs",
            "tools": [
                {
                    "name": "kubectl_logs",
                    "description": "Fetch Kubernetes logs",
                    "command": "kubectl logs",
                }
            ],
        },
    ]

    with pytest.raises(ValueError, match="Old toolset config format detected"):
        load_toolsets_from_config(old_format_data)


toolsets_config_str = """
grafana/loki:
    config:
        api_key: "{{env.GRAFANA_API_KEY}}"
        api_url: "{{env.GRAFANA_URL}}"
        grafana_datasource_uid: "my_grafana_datasource_uid"
"""

env_vars = {
    "GRAFANA_API_KEY": "glsa_sdj1q2o3prujpqfd",
    "GRAFANA_URL": "https://my-grafana.com/",
}


def test_load_toolsets_from_config(monkeypatch):
    for key, value in env_vars.items():
        os.environ[key] = value
        monkeypatch.setenv(key, value)

    toolsets_config = yaml.safe_load(toolsets_config_str)
    assert isinstance(toolsets_config, dict)
    definitions = load_toolsets_from_config(
        toolsets=toolsets_config, strict_check=False
    )
    assert len(definitions) == 1
    grafana_loki = definitions[0]
    config = grafana_loki.config
    assert config
    assert config.get("api_key") == "glsa_sdj1q2o3prujpqfd"
    assert config.get("api_url") == "https://my-grafana.com/"
    assert config.get("grafana_datasource_uid") == "my_grafana_datasource_uid"

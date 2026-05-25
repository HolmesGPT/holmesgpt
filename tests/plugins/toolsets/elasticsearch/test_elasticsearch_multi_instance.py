"""Tests for multi-instance Elasticsearch configuration and basic-auth support."""

import base64
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
import responses
from pydantic import ValidationError
from requests.auth import HTTPBasicAuth

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
    ElasticsearchClusterToolset,
    ElasticsearchConfig,
    ElasticsearchInstance,
    build_auth,
)


def _toolset_with(**config: Any) -> ElasticsearchClusterToolset:
    """Build a toolset with state populated directly (no network probe)."""
    ts = ElasticsearchClusterToolset()
    ts.config = ElasticsearchConfig(**config)
    ts._instances = {i.name: i for i in (ts.elasticsearch_config.instances or [])}
    return ts


class TestLegacySingleInstanceShape:
    """Backwards-compatibility: the flat `api_url` shape still works."""

    def test_top_level_api_url_synthesizes_default_instance(self):
        cfg = ElasticsearchConfig(api_url="http://es:9200", api_key="k1")
        assert cfg.instances is not None
        assert len(cfg.instances) == 1
        inst = cfg.instances[0]
        assert inst.name == "default"
        assert inst.api_url == "http://es:9200"
        assert inst.api_key == "k1"
        # Globals applied to the default instance.
        assert inst.verify_ssl is True
        assert inst.timeout_seconds == 10

    def test_legacy_url_alias_still_works(self):
        # The deprecated `url` field still maps to `api_url`.
        cfg = ElasticsearchConfig(url="http://es:9200")  # type: ignore[call-arg]
        assert cfg.instances[0].api_url == "http://es:9200"

    def test_missing_api_url_and_instances_rejected(self):
        with pytest.raises(ValidationError, match="instances.+api_url"):
            ElasticsearchConfig()

    def test_legacy_basic_auth(self):
        cfg = ElasticsearchConfig(
            api_url="http://es:9200", username="elastic", password="pw"
        )
        inst = cfg.instances[0]
        assert inst.username == "elastic"
        assert inst.password == "pw"

    def test_legacy_mtls_synthesizes_default_with_cert_pair(self):
        cfg = ElasticsearchConfig(
            api_url="https://es:9200",
            client_cert="/c.crt",
            client_key="/c.key",
        )
        inst = cfg.instances[0]
        assert inst.client_cert == "/c.crt"
        assert inst.client_key == "/c.key"


class TestMultiInstanceShape:
    def test_basic_multi_instance(self):
        cfg = ElasticsearchConfig(
            instances=[
                {"name": "prod-eu", "api_url": "http://eu:9200"},
                {"name": "prod-us", "api_url": "http://us:9200"},
            ]
        )
        assert [i.name for i in cfg.instances] == ["prod-eu", "prod-us"]

    def test_global_credentials_inherited(self):
        cfg = ElasticsearchConfig(
            username="admin",
            password="pw",
            instances=[
                {"name": "a", "api_url": "http://a:9200"},
                # Instance with its own auth doesn't inherit username/password.
                {"name": "b", "api_url": "http://b:9200", "api_key": "k"},
            ],
        )
        assert cfg.instances[0].username == "admin"
        assert cfg.instances[0].password == "pw"
        assert cfg.instances[1].username is None
        assert cfg.instances[1].api_key == "k"

    def test_global_password_overridden_per_instance(self):
        cfg = ElasticsearchConfig(
            username="elastic",
            password="global-pw",
            instances=[
                {"name": "a", "api_url": "http://a:9200"},
                # Instance b sets its own password but inherits the global username.
                {
                    "name": "b",
                    "api_url": "http://b:9200",
                    "username": "elastic",
                    "password": "b-pw",
                },
            ],
        )
        assert cfg.instances[0].password == "global-pw"
        assert cfg.instances[1].password == "b-pw"

    def test_global_timeout_inherited_unless_overridden(self):
        cfg = ElasticsearchConfig(
            timeout_seconds=45,
            instances=[
                {"name": "a", "api_url": "http://a:9200"},
                {"name": "b", "api_url": "http://b:9200", "timeout_seconds": 120},
            ],
        )
        assert cfg.instances[0].timeout_seconds == 45
        assert cfg.instances[1].timeout_seconds == 120

    def test_global_verify_ssl_inherited_unless_overridden(self):
        cfg = ElasticsearchConfig(
            verify_ssl=False,
            instances=[
                {"name": "a", "api_url": "http://a:9200"},
                {"name": "b", "api_url": "http://b:9200", "verify_ssl": True},
            ],
        )
        assert cfg.instances[0].verify_ssl is False
        assert cfg.instances[1].verify_ssl is True

    def test_global_mtls_inherited(self):
        cfg = ElasticsearchConfig(
            client_cert="/global.crt",
            client_key="/global.key",
            instances=[
                {"name": "a", "api_url": "https://a:9200"},
                {
                    "name": "b",
                    "api_url": "https://b:9200",
                    "client_cert": "/b.crt",
                    "client_key": "/b.key",
                },
            ],
        )
        assert cfg.instances[0].client_cert == "/global.crt"
        assert cfg.instances[0].client_key == "/global.key"
        assert cfg.instances[1].client_cert == "/b.crt"
        assert cfg.instances[1].client_key == "/b.key"

    def test_duplicate_names_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate Elasticsearch instance name"):
            ElasticsearchConfig(
                instances=[
                    {"name": "dup", "api_url": "http://a:9200"},
                    {"name": "dup", "api_url": "http://b:9200"},
                ]
            )

    def test_top_level_api_url_with_instances_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            ElasticsearchConfig(
                api_url="http://ignored:9200",
                instances=[{"name": "real", "api_url": "http://real:9200"}],
            )
        assert any("top-level `api_url` is ignored" in m for m in caplog.messages)


class TestAuthXor:
    def test_per_instance_api_key_and_basic_auth_rejected(self):
        with pytest.raises(ValidationError, match="api_key.+username.+password"):
            ElasticsearchConfig(
                instances=[
                    {
                        "name": "bad",
                        "api_url": "http://x:9200",
                        "api_key": "k",
                        "username": "u",
                        "password": "p",
                    }
                ]
            )

    def test_per_instance_username_without_password_rejected(self):
        with pytest.raises(ValidationError, match="username.+password.+together"):
            ElasticsearchConfig(
                instances=[{"name": "bad", "api_url": "http://x:9200", "username": "u"}]
            )

    def test_per_instance_password_without_username_rejected(self):
        with pytest.raises(ValidationError, match="username.+password.+together"):
            ElasticsearchConfig(
                instances=[{"name": "bad", "api_url": "http://x:9200", "password": "p"}]
            )

    def test_top_level_api_key_and_basic_auth_rejected(self):
        with pytest.raises(ValidationError, match="api_key.+username.+password"):
            ElasticsearchConfig(
                api_url="http://x:9200",
                api_key="k",
                username="u",
                password="p",
            )

    def test_top_level_username_without_password_rejected(self):
        with pytest.raises(ValidationError, match="username.+password.+together"):
            ElasticsearchConfig(
                api_url="http://x:9200",
                username="u",
            )

    def test_top_level_password_without_username_rejected(self):
        with pytest.raises(ValidationError, match="username.+password.+together"):
            ElasticsearchConfig(
                api_url="http://x:9200",
                password="p",
            )


class TestBuildAuth:
    def test_basic_auth_returned_when_creds_set(self):
        inst = ElasticsearchInstance(
            name="x", api_url="http://x:9200", username="u", password="p"
        )
        auth = build_auth(inst)
        assert isinstance(auth, HTTPBasicAuth)
        assert auth.username == "u"
        assert auth.password == "p"

    def test_none_when_no_basic_auth(self):
        inst = ElasticsearchInstance(name="x", api_url="http://x:9200", api_key="k")
        assert build_auth(inst) is None


class TestRequestConstruction:
    def test_basic_auth_on_the_wire(self):
        cfg = ElasticsearchConfig(
            username="u",
            password="p",
            instances=[{"name": "x", "api_url": "http://x.example:9200"}],
        )
        inst = cfg.instances[0]
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, "http://x.example:9200/probe", json={})
            requests.get(
                "http://x.example:9200/probe", auth=build_auth(inst)
            )
            auth_header = rsps.calls[0].request.headers["Authorization"]
            assert auth_header.startswith("Basic ")
            decoded = base64.b64decode(auth_header.split(maxsplit=1)[1]).decode()
            assert decoded == "u:p"


class TestGetInstance:
    def test_auto_select_single_instance(self):
        ts = _toolset_with(api_url="http://x:9200")
        inst = ts._get_instance({})
        assert inst.name == "default"

    def test_multi_instance_requires_param(self):
        ts = _toolset_with(
            instances=[
                {"name": "a", "api_url": "http://a:9200"},
                {"name": "b", "api_url": "http://b:9200"},
            ]
        )
        with pytest.raises(ValueError, match="required"):
            ts._get_instance({})

    def test_unknown_name_rejected(self):
        ts = _toolset_with(
            instances=[
                {"name": "a", "api_url": "http://a:9200"},
                {"name": "b", "api_url": "http://b:9200"},
            ]
        )
        with pytest.raises(ValueError, match="Unknown.+'c'.+Configured.+a.+b"):
            ts._get_instance({"elasticsearch_instance": "c"})

    def test_resolves_to_requested_instance(self):
        ts = _toolset_with(
            instances=[
                {"name": "a", "api_url": "http://a:9200"},
                {"name": "b", "api_url": "http://b:9200"},
            ]
        )
        inst = ts._get_instance({"elasticsearch_instance": "b"})
        assert inst.name == "b"
        assert inst.api_url == "http://b:9200"


class TestToolPathThreading:
    @patch("holmes.plugins.toolsets.elasticsearch.elasticsearch.requests.request")
    def test_tool_routes_to_requested_instance(self, mock_request):
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "green"}
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        ts = _toolset_with(
            username="elastic",
            password="global-pw",
            instances=[
                {"name": "a", "api_url": "http://a:9200"},
                # Instance b overrides with its own complete basic-auth pair.
                {
                    "name": "b",
                    "api_url": "http://b:9200",
                    "username": "elastic",
                    "password": "b-pw",
                },
            ],
        )
        # Find the cluster_health tool and invoke it targeting instance "b".
        from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
            ElasticsearchClusterHealth,
        )

        tool = next(t for t in ts.tools if isinstance(t, ElasticsearchClusterHealth))
        result = tool._invoke({"elasticsearch_instance": "b"}, context=None)

        assert result.status is StructuredToolResultStatus.SUCCESS
        called_url = mock_request.call_args[1]["url"]
        assert called_url.startswith("http://b:9200/")
        called_auth = mock_request.call_args[1]["auth"]
        # The per-instance override password should have been used, not the global.
        assert isinstance(called_auth, HTTPBasicAuth)
        assert called_auth.password == "b-pw"

    @patch("holmes.plugins.toolsets.elasticsearch.elasticsearch.requests.request")
    def test_tool_uses_global_creds_for_instance_without_override(self, mock_request):
        """Wire-level: instance with no auth gets the global creds on the
        actual HTTP Authorization header, not the per-instance override."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "green"}
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        ts = _toolset_with(
            username="elastic",
            password="global-pw",
            instances=[
                # Inherits the global creds.
                {"name": "a", "api_url": "http://a:9200"},
                # Has its own override.
                {
                    "name": "b",
                    "api_url": "http://b:9200",
                    "username": "elastic",
                    "password": "b-pw",
                },
            ],
        )
        from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
            ElasticsearchClusterHealth,
        )

        tool = next(t for t in ts.tools if isinstance(t, ElasticsearchClusterHealth))
        result = tool._invoke({"elasticsearch_instance": "a"}, context=None)

        assert result.status is StructuredToolResultStatus.SUCCESS
        called_url = mock_request.call_args[1]["url"]
        assert called_url.startswith("http://a:9200/")
        called_auth = mock_request.call_args[1]["auth"]
        assert isinstance(called_auth, HTTPBasicAuth)
        # Global creds, not the override that lives on instance "b".
        assert called_auth.username == "elastic"
        assert called_auth.password == "global-pw"

    @patch("holmes.plugins.toolsets.elasticsearch.elasticsearch.requests.request")
    def test_tool_uses_global_api_key_for_instance_without_override(self, mock_request):
        """Wire-level: instance with no auth gets the global API key on the
        Authorization header."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "green"}
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        ts = _toolset_with(
            api_key="global-key",
            instances=[
                {"name": "a", "api_url": "http://a:9200"},
                {"name": "b", "api_url": "http://b:9200", "api_key": "b-key"},
            ],
        )
        from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
            ElasticsearchClusterHealth,
        )

        tool = next(t for t in ts.tools if isinstance(t, ElasticsearchClusterHealth))
        result = tool._invoke({"elasticsearch_instance": "a"}, context=None)

        assert result.status is StructuredToolResultStatus.SUCCESS
        called_headers = mock_request.call_args[1]["headers"]
        # Inherited global key, not "b-key".
        assert called_headers.get("Authorization") == "ApiKey global-key"
        # No basic auth when the instance is authenticating via api_key.
        assert mock_request.call_args[1]["auth"] is None

    @patch("holmes.plugins.toolsets.elasticsearch.elasticsearch.requests.request")
    def test_tool_errors_clearly_when_instance_missing(self, mock_request):
        ts = _toolset_with(
            instances=[
                {"name": "a", "api_url": "http://a:9200"},
                {"name": "b", "api_url": "http://b:9200"},
            ]
        )
        from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
            ElasticsearchClusterHealth,
        )

        tool = next(t for t in ts.tools if isinstance(t, ElasticsearchClusterHealth))
        result = tool._invoke({}, context=None)

        assert result.status is StructuredToolResultStatus.ERROR
        assert "elasticsearch_instance" in result.error
        mock_request.assert_not_called()


class TestListInstancesTool:
    def test_lists_configured_instances(self):
        ts = _toolset_with(
            instances=[
                {"name": "a", "api_url": "http://a:9200"},
                {"name": "b", "api_url": "http://b:9200"},
            ]
        )
        from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
            ElasticsearchListInstances,
        )

        tool = next(t for t in ts.tools if isinstance(t, ElasticsearchListInstances))
        result = tool._invoke({}, context=None)
        assert result.status is StructuredToolResultStatus.SUCCESS
        names = [i["name"] for i in result.data["instances"]]
        assert names == ["a", "b"]

    def test_data_and_cluster_names_do_not_collide(self):
        """When both toolsets are multi-instance, their discovery tools have
        distinct names so neither overrides the other in the tool registry."""
        from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
            ElasticsearchClusterToolset,
            ElasticsearchDataToolset,
            ElasticsearchListInstances,
        )

        cfg = {
            "instances": [
                {"name": "a", "api_url": "http://a:9200"},
                {"name": "b", "api_url": "http://b:9200"},
            ]
        }
        cluster_ts = ElasticsearchClusterToolset()
        cluster_ts.prerequisites_callable(cfg)
        data_ts = ElasticsearchDataToolset()
        data_ts.prerequisites_callable(cfg)

        cluster_tool = next(
            t for t in cluster_ts.tools if isinstance(t, ElasticsearchListInstances)
        )
        data_tool = next(
            t for t in data_ts.tools if isinstance(t, ElasticsearchListInstances)
        )
        assert cluster_tool.name != data_tool.name
        assert {cluster_tool.name, data_tool.name} == {
            "elasticsearch_cluster_list_instances",
            "elasticsearch_data_list_instances",
        }


class TestSingleInstancePruning:
    """When only one instance is configured, the multi-instance affordances
    (the `elasticsearch_instance` parameter and the `elasticsearch_list_instances`
    discovery tool) are pruned to keep the LLM's tool surface lean.
    """

    def test_single_instance_hides_list_instances_tool(self):
        ts = ElasticsearchClusterToolset()
        # `prerequisites_callable` is where pruning happens.
        ok, _ = ts.prerequisites_callable({"api_url": "http://nope:9200"})
        # We don't care if the health check succeeds — pruning runs before it.
        del ok
        from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
            ElasticsearchListInstances,
        )

        assert not any(isinstance(t, ElasticsearchListInstances) for t in ts.tools)

    def test_single_instance_strips_param_from_every_tool(self):
        ts = ElasticsearchClusterToolset()
        ts.prerequisites_callable({"api_url": "http://nope:9200"})
        for tool in ts.tools:
            assert "elasticsearch_instance" not in tool.parameters, (
                f"{tool.name} still exposes elasticsearch_instance"
            )

    def test_multi_instance_keeps_list_instances_tool(self):
        ts = ElasticsearchClusterToolset()
        ts.prerequisites_callable(
            {
                "instances": [
                    {"name": "a", "api_url": "http://a:9200"},
                    {"name": "b", "api_url": "http://b:9200"},
                ]
            }
        )
        from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
            ElasticsearchListInstances,
        )

        assert any(isinstance(t, ElasticsearchListInstances) for t in ts.tools)

    def test_multi_instance_keeps_param_on_every_tool(self):
        ts = ElasticsearchClusterToolset()
        ts.prerequisites_callable(
            {
                "instances": [
                    {"name": "a", "api_url": "http://a:9200"},
                    {"name": "b", "api_url": "http://b:9200"},
                ]
            }
        )
        from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
            ElasticsearchListInstances,
        )

        for tool in ts.tools:
            if isinstance(tool, ElasticsearchListInstances):
                continue  # this tool deliberately has no instance param
            assert "elasticsearch_instance" in tool.parameters, (
                f"{tool.name} is missing elasticsearch_instance"
            )

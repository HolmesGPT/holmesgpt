"""Tests for multi-instance Grafana configuration and basic-auth support."""

import base64
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests
import responses
from pydantic import ValidationError
from requests.auth import HTTPBasicAuth

from holmes.core.llm import LLM
from holmes.core.tools import StructuredToolResultStatus, ToolInvokeContext
from holmes.plugins.toolsets.grafana.common import (
    GrafanaInstance,
    MultiInstanceGrafanaConfig as GrafanaConfig,
    build_auth,
    build_headers,
)
from holmes.plugins.toolsets.grafana.toolset_grafana import (
    GrafanaDashboardConfig,
    GrafanaToolset,
)


def _ctx() -> ToolInvokeContext:
    return ToolInvokeContext(
        llm=MagicMock(spec=LLM),
        max_token_count=100_000,
        tool_call_id="t1",
        tool_name="x",
    )


class TestLegacySingleInstanceShape:
    def test_top_level_api_url_synthesizes_default_instance(self):
        cfg = GrafanaConfig(api_url="http://grafana", api_key="k1")
        assert cfg.instances is not None
        assert len(cfg.instances) == 1
        inst = cfg.instances[0]
        assert inst.name == "default"
        assert inst.api_url == "http://grafana"
        assert inst.api_key == "k1"
        # Globals applied
        assert inst.verify_ssl is True
        assert inst.timeout_seconds == 30
        assert inst.max_retries == 3

    def test_legacy_url_alias_still_works(self):
        # The deprecated `url` field still maps to `api_url` at the top level.
        cfg = GrafanaConfig(url="http://grafana")  # type: ignore[call-arg]
        assert cfg.instances[0].api_url == "http://grafana"

    def test_missing_api_url_and_instances_rejected(self):
        with pytest.raises(ValidationError, match="instances.+api_url"):
            GrafanaConfig()


class TestMultiInstanceShape:
    def test_basic_multi_instance(self):
        cfg = GrafanaConfig(
            instances=[
                {"name": "prod-eu", "api_url": "http://eu"},
                {"name": "prod-us", "api_url": "http://us"},
            ]
        )
        assert [i.name for i in cfg.instances] == ["prod-eu", "prod-us"]

    def test_global_credentials_inherited(self):
        cfg = GrafanaConfig(
            username="admin",
            password="pw",
            instances=[
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b", "api_key": "k"},  # own auth
            ],
        )
        assert cfg.instances[0].username == "admin"
        assert cfg.instances[0].password == "pw"
        # Instance with its own auth doesn't inherit username/password
        assert cfg.instances[1].username is None
        assert cfg.instances[1].api_key == "k"

    def test_global_timeout_inherited_unless_overridden(self):
        cfg = GrafanaConfig(
            timeout_seconds=45,
            instances=[
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b", "timeout_seconds": 120},
            ],
        )
        assert cfg.instances[0].timeout_seconds == 45
        assert cfg.instances[1].timeout_seconds == 120

    def test_duplicate_names_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate Grafana instance name"):
            GrafanaConfig(
                instances=[
                    {"name": "dup", "api_url": "http://a"},
                    {"name": "dup", "api_url": "http://b"},
                ]
            )

    def test_top_level_api_url_with_instances_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            GrafanaConfig(
                api_url="http://ignored",
                instances=[{"name": "real", "api_url": "http://real"}],
            )
        assert any("top-level `api_url` is ignored" in m for m in caplog.messages)


class TestAuthXor:
    def test_api_key_and_basic_auth_rejected_together(self):
        with pytest.raises(ValidationError, match="api_key.+username.+password"):
            GrafanaConfig(
                instances=[
                    {
                        "name": "bad",
                        "api_url": "http://x",
                        "api_key": "k",
                        "username": "u",
                        "password": "p",
                    }
                ]
            )

    def test_username_without_password_rejected(self):
        with pytest.raises(ValidationError, match="username.+password.+together"):
            GrafanaConfig(
                instances=[{"name": "bad", "api_url": "http://x", "username": "u"}]
            )

    def test_password_without_username_rejected(self):
        with pytest.raises(ValidationError, match="username.+password.+together"):
            GrafanaConfig(
                instances=[{"name": "bad", "api_url": "http://x", "password": "p"}]
            )


class TestBuildAuth:
    def test_basic_auth_returned_when_creds_set(self):
        inst = GrafanaInstance(
            name="x", api_url="http://x", username="u", password="p"
        )
        auth = build_auth(inst)
        assert isinstance(auth, HTTPBasicAuth)
        assert auth.username == "u"
        assert auth.password == "p"

    def test_none_when_no_basic_auth(self):
        inst = GrafanaInstance(name="x", api_url="http://x", api_key="k")
        assert build_auth(inst) is None


class TestRequestConstruction:
    def test_basic_auth_on_the_wire(self):
        cfg = GrafanaConfig(
            username="u", password="p",
            instances=[{"name": "x", "api_url": "http://x.example"}],
        )
        inst = cfg.instances[0]
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, "http://x.example/probe", json={})
            requests.get(
                "http://x.example/probe",
                headers=build_headers(inst.api_key, inst.additional_headers),
                auth=build_auth(inst),
            )
            auth_header = rsps.calls[0].request.headers["Authorization"]
            assert auth_header.startswith("Basic ")
            decoded = base64.b64decode(auth_header.split(maxsplit=1)[1]).decode()
            assert decoded == "u:p"


def _toolset_with(**config: Any) -> GrafanaToolset:
    """Build a GrafanaToolset with state populated directly (no network probe)."""
    ts = GrafanaToolset()
    ts._grafana_config = GrafanaDashboardConfig(**config)
    ts._instances = {i.name: i for i in ts._grafana_config.instances}
    return ts


class TestGetInstance:
    def test_auto_select_single_instance(self):
        ts = _toolset_with(api_url="http://x")
        inst = ts._get_instance({})
        assert inst.name == "default"

    def test_multi_instance_requires_param(self):
        ts = _toolset_with(
            instances=[
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b"},
            ]
        )
        with pytest.raises(ValueError, match="required"):
            ts._get_instance({})

    def test_unknown_name_rejected(self):
        ts = _toolset_with(
            instances=[
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b"},
            ]
        )
        with pytest.raises(ValueError, match="Unknown grafana_instance"):
            ts._get_instance({"grafana_instance": "missing"})

    def test_known_name_resolves(self):
        ts = _toolset_with(
            instances=[
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b"},
            ]
        )
        inst = ts._get_instance({"grafana_instance": "b"})
        assert inst.api_url == "http://b"


class TestToolPathThreading:
    """End-to-end: a tool call uses the resolved instance's auth and URL."""

    def test_dashboard_tool_threads_basic_auth(self):
        ts = _toolset_with(
            username="admin",
            password="admin",
            instances=[{"name": "prod", "api_url": "http://prod.example"}],
        )

        get_dashboard_tags = next(
            t for t in ts.tools if t.name == "grafana_get_dashboard_tags"
        )
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "http://prod.example/api/dashboards/tags",
                json=[{"term": "production"}],
            )
            r = get_dashboard_tags._invoke({"grafana_instance": "prod"}, _ctx())
            assert r.status == StructuredToolResultStatus.SUCCESS
            auth_header = rsps.calls[0].request.headers["Authorization"]
            assert auth_header.startswith("Basic ")
            decoded = base64.b64decode(auth_header.split(maxsplit=1)[1]).decode()
            assert decoded == "admin:admin"

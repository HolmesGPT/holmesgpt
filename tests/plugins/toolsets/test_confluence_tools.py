from unittest.mock import MagicMock, patch

import pytest
import requests

from pydantic import ValidationError

from holmes.core.tools import ToolsetStatusEnum
from holmes.plugins.toolsets.confluence.confluence import (
    ATLASSIAN_GATEWAY_BASE,
    ConfluenceCloudConfig,
    ConfluenceDataCenterBasicConfig,
    ConfluenceDataCenterPATConfig,
    ConfluenceToolset,
    determine_confluence_class,
)


@pytest.fixture()
def toolset():
    ts = ConfluenceToolset()
    ts.config = ConfluenceCloudConfig(
        api_url="https://test.atlassian.net",
        user="user@test.com",
        api_key="fake-token",
    )
    ts.status = ToolsetStatusEnum.ENABLED
    return ts


# ---------------------------------------------------------------------------
# Gateway auto-detection for scoped tokens
# ---------------------------------------------------------------------------


class TestGatewayAutoDetection:
    def test_direct_url_used_when_no_gateway_needed(self, toolset):
        """When direct URL works, no gateway is activated."""
        assert toolset._effective_base() == "https://test.atlassian.net"
        assert toolset._gateway_base_url is None

    def test_gateway_activated_with_explicit_cloud_id(self):
        """When cloud_id is configured, gateway is activated during health check."""
        ts = ConfluenceToolset()
        config = {
            "api_url": "https://mycompany.atlassian.net",
            "user": "user@test.com",
            "api_key": "scoped-token",
            "cloud_id": "abc-123",
        }

        with (
            patch.object(ConfluenceToolset, "_probe_request", return_value={"results": []}),
            patch.object(ConfluenceToolset, "_setup_http_tools"),
        ):
            ok, msg = ts.prerequisites_callable(config)

        assert ok is True
        assert ts._gateway_base_url == f"{ATLASSIAN_GATEWAY_BASE}/abc-123"

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_gateway_fallback_on_auth_error(self, status_code):
        """When direct URL returns 401 or 403, auto-detect cloud_id and switch to gateway."""
        ts = ConfluenceToolset()
        config = {
            "api_url": "https://mycompany.atlassian.net",
            "user": "user@test.com",
            "api_key": "scoped-token",
        }

        # First call (direct) fails, second call (gateway) succeeds
        error_resp = MagicMock()
        error_resp.status_code = status_code
        error_resp.text = '{"message":"auth error"}'

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise requests.exceptions.HTTPError(response=error_resp)
            return {"results": []}

        tenant_resp = MagicMock()
        tenant_resp.status_code = 200
        tenant_resp.json.return_value = {"cloudId": "detected-cloud-id"}
        tenant_resp.raise_for_status.return_value = None

        with (
            patch.object(ConfluenceToolset, "_probe_request", side_effect=side_effect),
            patch("holmes.plugins.toolsets.confluence.confluence.requests.get", return_value=tenant_resp),
            patch.object(ConfluenceToolset, "_setup_http_tools"),
        ):
            ok, msg = ts.prerequisites_callable(config)

        assert ok is True
        assert "gateway" in msg.lower()
        assert ts._gateway_base_url == f"{ATLASSIAN_GATEWAY_BASE}/detected-cloud-id"

    def test_gateway_url_used_when_activated(self):
        """Once gateway is activated, effective base uses the gateway URL."""
        ts = ConfluenceToolset()
        ts.config = ConfluenceCloudConfig(
            api_url="https://mycompany.atlassian.net",
            user="user@test.com",
            api_key="token",
        )
        ts._gateway_base_url = f"{ATLASSIAN_GATEWAY_BASE}/my-cloud-id"

        assert ts._effective_base() == f"{ATLASSIAN_GATEWAY_BASE}/my-cloud-id"
        assert "mycompany.atlassian.net" not in ts._effective_base()

    def test_no_gateway_for_data_center(self):
        """Data Center URLs (non-atlassian.net) should never trigger gateway fallback."""
        ts = ConfluenceToolset()
        config = {
            "api_url": "https://confluence.mycompany.com",
            "api_key": "pat-token",
            "auth_type": "bearer",
            "api_path_prefix": "",
        }

        forbidden_resp = MagicMock()
        forbidden_resp.status_code = 403
        forbidden_resp.text = "Forbidden"

        with patch.object(
            ConfluenceToolset,
            "_probe_request",
            side_effect=requests.exceptions.HTTPError(response=forbidden_resp),
        ):
            ok, msg = ts.prerequisites_callable(config)

        assert ok is False
        assert ts._gateway_base_url is None

    def test_gateway_fallback_fails_gracefully(self):
        """If gateway fallback also fails, report the original error."""
        ts = ConfluenceToolset()
        config = {
            "api_url": "https://mycompany.atlassian.net",
            "user": "user@test.com",
            "api_key": "bad-token",
        }

        forbidden_resp = MagicMock()
        forbidden_resp.status_code = 403
        forbidden_resp.text = '{"message":"Current user not permitted to use Confluence"}'

        # Both direct and gateway calls fail with 403
        with (
            patch.object(
                ConfluenceToolset,
                "_probe_request",
                side_effect=requests.exceptions.HTTPError(response=forbidden_resp),
            ),
            patch("holmes.plugins.toolsets.confluence.confluence.requests.get") as mock_get,
        ):
            tenant_resp = MagicMock()
            tenant_resp.status_code = 200
            tenant_resp.json.return_value = {"cloudId": "some-id"}
            tenant_resp.raise_for_status.return_value = None
            mock_get.return_value = tenant_resp

            ok, msg = ts.prerequisites_callable(config)

        assert ok is False
        assert ts._gateway_base_url is None

    def test_cloud_id_resolution_failure(self):
        """If cloud_id cannot be resolved, fallback fails gracefully."""
        ts = ConfluenceToolset()
        config = {
            "api_url": "https://mycompany.atlassian.net",
            "user": "user@test.com",
            "api_key": "scoped-token",
        }

        forbidden_resp = MagicMock()
        forbidden_resp.status_code = 403
        forbidden_resp.text = '{"message":"Current user not permitted to use Confluence"}'

        with (
            patch.object(
                ConfluenceToolset,
                "_probe_request",
                side_effect=requests.exceptions.HTTPError(response=forbidden_resp),
            ),
            patch(
                "holmes.plugins.toolsets.confluence.confluence.requests.get",
                side_effect=requests.exceptions.ConnectionError("DNS failure"),
            ),
        ):
            ok, msg = ts.prerequisites_callable(config)

        assert ok is False
        assert ts._gateway_base_url is None


# ---------------------------------------------------------------------------
# HTTP toolset delegation
# ---------------------------------------------------------------------------


class TestHttpToolsetDelegation:
    def test_builds_basic_auth_endpoint(self):
        """Basic auth config produces an endpoint with basic auth."""
        ts = ConfluenceToolset()
        ts.config = ConfluenceCloudConfig(
            api_url="https://mycompany.atlassian.net",
            user="user@test.com",
            api_key="api-token",
        )
        endpoint = ts._build_endpoint_config()

        assert endpoint.auth.type == "basic"
        assert endpoint.auth.username == "user@test.com"
        assert endpoint.auth.password == "api-token"
        assert "mycompany.atlassian.net" in endpoint.hosts[0]

    def test_builds_bearer_auth_endpoint(self):
        """Bearer auth config produces an endpoint with bearer auth."""
        ts = ConfluenceToolset()
        ts.config = ConfluenceDataCenterPATConfig(
            api_url="https://confluence.internal.com",
            api_key="pat-token",
        )
        endpoint = ts._build_endpoint_config()

        assert endpoint.auth.type == "bearer"
        assert endpoint.auth.token == "pat-token"
        assert "confluence.internal.com" in endpoint.hosts[0]

    def test_gateway_produces_bearer_endpoint(self):
        """When gateway is active, endpoint uses bearer auth and gateway host."""
        ts = ConfluenceToolset()
        ts.config = ConfluenceCloudConfig(
            api_url="https://mycompany.atlassian.net",
            user="user@test.com",
            api_key="scoped-token",
        )
        ts._gateway_base_url = f"{ATLASSIAN_GATEWAY_BASE}/my-cloud-id"

        endpoint = ts._build_endpoint_config()

        assert endpoint.auth.type == "bearer"
        assert endpoint.auth.token == "scoped-token"
        assert "api.atlassian.com" in endpoint.hosts[0]
        assert "my-cloud-id" in endpoint.paths[0]

    def test_setup_http_tools_registers_tool(self):
        """After setup, the toolset has exactly one HTTP request tool."""
        ts = ConfluenceToolset()
        ts.config = ConfluenceCloudConfig(
            api_url="https://mycompany.atlassian.net",
            user="user@test.com",
            api_key="api-token",
        )

        with patch("holmes.plugins.toolsets.http.http_toolset.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_get.return_value = mock_resp
            ts._setup_http_tools()

        assert len(ts.tools) == 1
        assert ts.tools[0].name == "confluence_request"
        assert ts.llm_instructions is not None
        assert "Confluence REST API" in ts.llm_instructions

    def test_llm_instructions_contain_base_url(self):
        """LLM instructions include the effective base URL for the LLM to use."""
        ts = ConfluenceToolset()
        ts.config = ConfluenceCloudConfig(
            api_url="https://mycompany.atlassian.net",
            user="user@test.com",
            api_key="api-token",
        )
        instructions = ts._build_llm_instructions()

        assert "mycompany.atlassian.net" in instructions
        assert "/wiki/rest/api" in instructions
        assert "CQL" in instructions

    def test_llm_instructions_use_gateway_url(self):
        """When gateway is active, LLM instructions use the gateway base URL."""
        ts = ConfluenceToolset()
        ts.config = ConfluenceCloudConfig(
            api_url="https://mycompany.atlassian.net",
            user="user@test.com",
            api_key="api-token",
        )
        ts._gateway_base_url = f"{ATLASSIAN_GATEWAY_BASE}/my-cloud-id"

        instructions = ts._build_llm_instructions()

        assert "api.atlassian.com/ex/confluence/my-cloud-id" in instructions
        assert "mycompany.atlassian.net" not in instructions

    def test_data_center_empty_prefix(self):
        """Data Center with empty prefix produces correct paths."""
        ts = ConfluenceToolset()
        ts.config = ConfluenceDataCenterPATConfig(
            api_url="https://confluence.internal.com",
            api_key="pat-token",
        )
        endpoint = ts._build_endpoint_config()

        assert "/rest/api/*" in endpoint.paths[0]
        # Should NOT have /wiki prefix
        assert "/wiki" not in endpoint.paths[0]


# ---------------------------------------------------------------------------
# Variant ClassVars and field surface
# ---------------------------------------------------------------------------


class TestVariantClassVars:
    """Each variant declares auth_type and api_path_prefix as class-level
    facts, not user-config fields."""

    def test_cloud_classvars(self):
        cfg = ConfluenceCloudConfig(
            api_url="https://x.atlassian.net",
            user="u@x.com",
            api_key="k",
        )
        assert cfg.auth_type == "basic"
        assert cfg.api_path_prefix == "/wiki"

    def test_dc_pat_classvars(self):
        cfg = ConfluenceDataCenterPATConfig(
            api_url="https://confluence.x.com",
            api_key="pat",
        )
        assert cfg.auth_type == "bearer"
        assert cfg.api_path_prefix == ""

    def test_dc_basic_classvars(self):
        cfg = ConfluenceDataCenterBasicConfig(
            api_url="https://confluence.x.com",
            user="u",
            api_key="pw",
        )
        assert cfg.auth_type == "basic"
        assert cfg.api_path_prefix == ""

    def test_classvars_not_pydantic_fields(self):
        """auth_type and api_path_prefix are ClassVars, not model fields."""
        for cls in (
            ConfluenceCloudConfig,
            ConfluenceDataCenterPATConfig,
            ConfluenceDataCenterBasicConfig,
        ):
            assert "auth_type" not in cls.model_fields
            assert "api_path_prefix" not in cls.model_fields

    def test_cloud_id_only_on_cloud(self):
        """cloud_id is a real field on Cloud; not declared on DC variants."""
        assert "cloud_id" in ConfluenceCloudConfig.model_fields
        assert "cloud_id" not in ConfluenceDataCenterPATConfig.model_fields
        assert "cloud_id" not in ConfluenceDataCenterBasicConfig.model_fields


class TestVariantFieldRequirements:
    """Each variant enforces required fields via Pydantic directly — no
    hand-rolled validate_auth needed."""

    def test_cloud_requires_user(self):
        with pytest.raises(ValidationError):
            ConfluenceCloudConfig(
                api_url="https://x.atlassian.net",
                api_key="k",
            )

    def test_dc_basic_requires_user(self):
        with pytest.raises(ValidationError):
            ConfluenceDataCenterBasicConfig(
                api_url="https://confluence.x.com",
                api_key="pw",
            )

    def test_dc_pat_does_not_require_user(self):
        # Should not raise — DC PAT doesn't declare `user` at all (the PAT
        # itself identifies the owning user server-side).
        ConfluenceDataCenterPATConfig(
            api_url="https://confluence.x.com",
            api_key="pat",
        )
        assert "user" not in ConfluenceDataCenterPATConfig.model_fields


class TestVariantSchema:
    """The frontend reads each variant's JSON schema; verify hidden fields
    are stripped and ClassVars don't appear at all."""

    def _schema_for(self, variant_cls):
        ts = ConfluenceToolset()
        schemas = ts.get_config_schema()
        assert schemas is not None
        return schemas[variant_cls.__name__]["schema"]

    def test_cloud_schema_omits_classvars_and_hidden_fields(self):
        schema = self._schema_for(ConfluenceCloudConfig)
        props = set(schema.get("properties", {}).keys())
        assert "auth_type" not in props  # ClassVar, not a field
        assert "api_path_prefix" not in props  # ClassVar
        assert "cloud_id" not in props  # in _hidden_fields
        # User-facing fields remain.
        assert {"api_url", "user", "api_key"}.issubset(props)

    def test_dc_pat_schema_omits_classvars_and_user(self):
        schema = self._schema_for(ConfluenceDataCenterPATConfig)
        props = set(schema.get("properties", {}).keys())
        assert "auth_type" not in props
        assert "api_path_prefix" not in props
        assert "cloud_id" not in props  # not declared at all on DC
        assert "user" not in props  # not declared on DC PAT (PAT carries the identity)
        assert {"api_url", "api_key"}.issubset(props)

    def test_dc_basic_schema_omits_classvars(self):
        schema = self._schema_for(ConfluenceDataCenterBasicConfig)
        props = set(schema.get("properties", {}).keys())
        assert "auth_type" not in props
        assert "api_path_prefix" not in props
        assert "cloud_id" not in props
        assert {"api_url", "user", "api_key"}.issubset(props)


class TestPreSubtypeBackwardsCompat:
    """Pre-subtype YAML configs (no `subtype:` field, may include
    auth_type/api_path_prefix/cloud_id keys at the top level) must still
    route correctly via field-shape detection and the routed variant must
    expose the same runtime contract."""

    def test_pre_subtype_cloud_yaml_with_cloud_id(self):
        config = {
            "api_url": "https://acme.atlassian.net",
            "user": "alice@acme.com",
            "api_key": "token",
            "cloud_id": "abc-123",
        }
        cls = determine_confluence_class(config)
        assert cls is ConfluenceCloudConfig

        cfg = cls(**config)
        assert cfg.cloud_id == "abc-123"
        assert cfg.auth_type == "basic"
        assert cfg.api_path_prefix == "/wiki"

    def test_pre_subtype_dc_pat_yaml(self):
        # Pre-subtype DC PAT users wrote auth_type and api_path_prefix in YAML.
        config = {
            "api_url": "https://confluence.acme.com",
            "api_key": "pat-token",
            "auth_type": "bearer",
            "api_path_prefix": "",
        }
        cls = determine_confluence_class(config)
        assert cls is ConfluenceDataCenterPATConfig

        cfg = cls(**config)
        # ClassVar values, not the YAML values, but they happen to match.
        assert cfg.auth_type == "bearer"
        assert cfg.api_path_prefix == ""

    def test_pre_subtype_dc_basic_yaml(self):
        config = {
            "api_url": "https://confluence.acme.com",
            "user": "alice",
            "api_key": "password",
            "auth_type": "basic",
        }
        cls = determine_confluence_class(config)
        assert cls is ConfluenceDataCenterBasicConfig

        cfg = cls(**config)
        assert cfg.auth_type == "basic"
        assert cfg.api_path_prefix == ""

    def test_pre_subtype_dc_with_stray_cloud_id_is_ignored(self):
        """A DC config that somehow has a cloud_id key (e.g. from a saved UI
        record before the field moved) must not raise — extras are allowed."""
        config = {
            "api_url": "https://confluence.acme.com",
            "user": "alice",
            "api_key": "password",
            "cloud_id": "leftover-value",
        }
        cls = determine_confluence_class(config)
        assert cls is ConfluenceDataCenterBasicConfig

        # Should not raise — `extra="allow"` on ToolsetConfig.
        cfg = cls(**config)
        # cloud_id is not declared on DC variants, so accessing it as a
        # model attribute returns nothing (lands in model_extra).
        assert "cloud_id" not in cls.model_fields

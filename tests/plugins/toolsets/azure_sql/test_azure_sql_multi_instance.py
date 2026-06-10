"""Multi-instance proof for Azure SQL through the actual wrapper.

Azure SQL is unchanged from master; `multi_instance(AzureSQLToolset)` makes it
multi-instance. It uses the Azure SDK (no plain HTTP), so the Azure credential and
API client are patched. Verifies per-instance credential/client/database ISOLATION
and that a routed call uses the selected instance's API client.
"""

from unittest.mock import MagicMock, patch

from holmes.plugins.toolsets.azure_sql.azure_sql_toolset import AzureSQLToolset
from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from tests.conftest import create_mock_tool_invoke_context


def _cfg(tenant, client, secret, sub, rg, server, db):
    return {
        "tenant_id": tenant,
        "client_id": client,
        "client_secret": secret,
        "database": {
            "subscription_id": sub,
            "resource_group": rg,
            "server_name": server,
            "database_name": db,
        },
    }


A = _cfg("ten_a", "cli_a", "sec_a", "sub_a", "rg_a", "server_a", "db_a")
B = _cfg("ten_b", "cli_b", "sec_b", "sub_b", "rg_b", "server_b", "db_b")


def _api_factory(*args, **kwargs):
    m = MagicMock()
    m._subscription = args[1] if len(args) > 1 else kwargs.get("subscription_id")
    return m


@patch(
    "holmes.plugins.toolsets.azure_sql.azure_sql_toolset.AzureSQLAPIClient",
    side_effect=_api_factory,
)
@patch("holmes.plugins.toolsets.azure_sql.azure_sql_toolset.ClientSecretCredential")
class TestAzureSqlMultiInstance:
    def _build(self):
        ts = multi_instance(AzureSQLToolset)
        ok, _ = ts.prerequisites_callable(
            {"instances": [{"name": "a", **A}, {"name": "b", **B}]}
        )
        assert ok is True
        return ts

    def test_flat_config_backwards_compatible(self, mock_cred, mock_api):
        ts = multi_instance(AzureSQLToolset)
        ok, _ = ts.prerequisites_callable(dict(A))
        assert ok is True
        assert list(ts._children) == ["default"]
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)
        assert all(INSTANCE_PARAM_NAME not in t.parameters for t in ts.tools)

    def test_tool_surface(self, mock_cred, mock_api):
        ts = self._build()
        assert any(t.name == "azure_sql_list_instances" for t in ts.tools)
        for tool in ts.tools:
            if not isinstance(tool, ListInstancesTool):
                assert INSTANCE_PARAM_NAME in tool.parameters

    def test_per_instance_credential_and_client_isolation(self, mock_cred, mock_api):
        ts = self._build()
        # Each instance built its own service-principal credential.
        tenants = {c.kwargs.get("tenant_id") for c in mock_cred.call_args_list}
        assert {"ten_a", "ten_b"} <= tenants
        # Each child has its own API client built with its own subscription + db config.
        a, b = ts._children["a"], ts._children["b"]
        assert a._api_client._subscription == "sub_a"
        assert b._api_client._subscription == "sub_b"
        assert a._api_client is not b._api_client
        assert a._database_config.server_name == "server_a"
        assert b._database_config.server_name == "server_b"

    def test_routed_call_uses_selected_instance_client(self, mock_cred, mock_api):
        ts = self._build()
        a, b = ts._children["a"], ts._children["b"]
        a._api_client.reset_mock()
        b._api_client.reset_mock()
        tool = next(t for t in ts.tools if t.name == "analyze_database_health_status")
        tool.invoke({INSTANCE_PARAM_NAME: "a"}, create_mock_tool_invoke_context())
        # The routed call went through instance a's API client, not b's.
        assert a._api_client.method_calls, "expected instance a's API client to be used"
        assert not b._api_client.method_calls, "instance b's API client must be untouched"
        # and it queried instance a's subscription/server on the wire-equivalent call.
        all_args = str(a._api_client.method_calls)
        assert "sub_a" in all_args and "server_a" in all_args

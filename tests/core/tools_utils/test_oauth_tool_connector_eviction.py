"""Red→green tests for the OAuth expiry recovery fix.

Before the fix:
  - DalTokenStore.delete_token is NOT called (gated behind isinstance(DiskTokenStore))
  - _user_tools still holds stale tools after a 401, so the _connect placeholder
    never gets re-exposed and the LLM keeps calling dead tools.

After the fix:
  - delete_token is called for BOTH store types.
  - _user_tools[user_id][toolset.name] is cleared, so apply_user_tools falls
    through to the placeholder.
"""

from unittest.mock import MagicMock

import httpx
import pytest

from holmes.core.tools_utils.oauth_tool_connector import OAuthToolConnector
from holmes.plugins.toolsets.mcp.oauth_token_store import DalTokenStore, DiskTokenStore


def _raise_401(_ctx=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 401
    raise httpx.HTTPStatusError("401 Unauthorized", request=MagicMock(), response=resp)


def _make_toolset(name="k8s"):
    ts = MagicMock()
    ts.name = name
    ts._load_remote_tools.side_effect = _raise_401
    ts._mcp_config.oauth.authorization_url = "https://auth.example/authorize"
    ts._mcp_config.oauth.client_id = "cid"
    ts._mcp_config.oauth.token_url = "https://auth.example/token"
    return ts


@pytest.fixture
def patched_manager(monkeypatch):
    """A fake token manager wired into the connector module."""
    mgr = MagicMock()
    mgr._get_cache_key.return_value = "ck"
    monkeypatch.setattr(
        "holmes.core.tools_utils.oauth_tool_connector._get_token_manager",
        lambda: mgr,
    )
    return mgr


@pytest.mark.parametrize("store_cls", [DalTokenStore, DiskTokenStore])
def test_401_deletes_token_in_both_stores(patched_manager, store_cls):
    """Fix #1: delete_token must run for DalTokenStore too, not only DiskTokenStore."""
    store = MagicMock(spec=store_cls)
    patched_manager._store = store

    connector = OAuthToolConnector()
    toolset = _make_toolset()

    out = connector.load_tools_for_user("u1", toolset, {"user_id": "u1"})

    assert out == []
    patched_manager._cache.evict.assert_called_once_with("ck")
    store.delete_token.assert_called_once_with(
        "https://auth.example/authorize", user_id="u1"
    )


def test_401_clears_stale_user_tools(patched_manager):
    """Fix #2: after 401, _user_tools must be cleared so the _connect placeholder
    is re-exposed and the LLM stops calling dead tools."""
    patched_manager._store = MagicMock(spec=DalTokenStore)

    connector = OAuthToolConnector()
    toolset = _make_toolset()
    stale_tool = MagicMock(); stale_tool.name = "k8s_list_pods"; stale_tool.toolset = toolset
    connector.store_user_tools("u1", "k8s", [stale_tool])

    assert connector._user_tools["u1"]["k8s"] == [stale_tool]
    assert connector._user_tool_to_toolset["u1"]["k8s_list_pods"] is toolset

    connector.load_tools_for_user("u1", toolset, {"user_id": "u1"})

    assert "k8s" not in connector._user_tools.get("u1", {}), (
        "stale tools should be cleared from _user_tools on 401"
    )
    assert "k8s_list_pods" not in connector._user_tool_to_toolset.get("u1", {}), (
        "stale tool->toolset mapping should be cleared on 401"
    )


def test_401_clears_mapping_when_stored_under_different_instance(patched_manager):
    """Fix #3: _user_tool_to_toolset is purged by toolset name, not object identity.

    Toolsets get reloaded as new instances during config refresh; a stale entry
    stored under the old instance must still be evicted on 401 against a
    same-named new instance.
    """
    patched_manager._store = MagicMock(spec=DalTokenStore)
    connector = OAuthToolConnector()

    old_toolset = _make_toolset()
    stale_tool = MagicMock()
    stale_tool.name = "k8s_list_pods"
    stale_tool.toolset = old_toolset
    connector.store_user_tools("u1", "k8s", [stale_tool])

    new_toolset = _make_toolset()
    assert new_toolset is not old_toolset

    connector.load_tools_for_user("u1", new_toolset, {"user_id": "u1"})

    assert "k8s_list_pods" not in connector._user_tool_to_toolset.get("u1", {}), (
        "mapping under old instance should still be evicted via name match"
    )

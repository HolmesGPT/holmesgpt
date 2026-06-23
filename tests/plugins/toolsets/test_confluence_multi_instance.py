"""Multi-instance proof for Confluence through the actual wrapper.

Confluence is unchanged from master; `multi_instance(ConfluenceToolset)` makes it
multi-instance. Each instance builds its own internal HTTP toolset bound to that
Confluence server. Uses Data-Center PAT instances (Bearer auth, no cloud gateway).
HTTP is mocked; the routed call is asserted on the wire.
"""

import re

import pytest
import responses

from holmes.plugins.toolsets.confluence.confluence import ConfluenceToolset
from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from tests.conftest import create_mock_tool_invoke_context

A = "https://confluence-a.example.com"
B = "https://confluence-b.example.com"


def _pat_instance(name, url, key):
    return {"name": name, "api_url": url, "api_key": key, "auth_type": "bearer"}


def _mock_rest(rsps):
    # Matches health probes (/rest/api/space) and routed calls (/rest/api/*) on any host.
    rsps.add(
        responses.GET,
        re.compile(r"https://confluence-[ab]\.example\.com/rest/api/.*"),
        json={"results": [], "size": 0},
        status=200,
    )


class TestConfluenceFlat:
    def test_flat_config_backwards_compatible(self):
        ts = multi_instance(ConfluenceToolset)
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            _mock_rest(rsps)
            ok, _ = ts.prerequisites_callable(
                {"api_url": A, "api_key": "pat", "auth_type": "bearer"}
            )
        assert ok is True
        assert list(ts._children) == ["default"]
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)
        assert all(INSTANCE_PARAM_NAME not in t.parameters for t in ts.tools)


class TestConfluenceMultiInstance:
    def _build(self, rsps):
        _mock_rest(rsps)
        ts = multi_instance(ConfluenceToolset)
        ok, _ = ts.prerequisites_callable(
            {
                "instances": [
                    _pat_instance("a", A, "pat_a"),
                    _pat_instance("b", B, "pat_b"),
                ]
            }
        )
        assert ok is True
        return ts

    def test_tool_surface(self):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
        assert any(t.name == "confluence_list_instances" for t in ts.tools)
        assert any(t.name == "confluence_request" for t in ts.tools)
        for tool in ts.tools:
            if not isinstance(tool, ListInstancesTool):
                assert INSTANCE_PARAM_NAME in tool.parameters

    @pytest.mark.parametrize(
        "instance,host,token",
        [("a", A, "pat_a"), ("b", B, "pat_b")],
    )
    def test_each_instance_calls_its_own_server(self, instance, host, token):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            ts = self._build(rsps)
            tool = next(t for t in ts.tools if t.name == "confluence_request")
            tool.invoke(
                {"url": f"{host}/rest/api/content/123", INSTANCE_PARAM_NAME: instance},
                create_mock_tool_invoke_context(),
            )
            last = rsps.calls[-1].request
            assert last.url.startswith(f"{host}/rest/api/content/123")
            assert last.headers.get("Authorization") == f"Bearer {token}"

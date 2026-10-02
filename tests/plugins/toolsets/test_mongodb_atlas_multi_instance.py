"""Multi-instance proof for MongoDB Atlas through the actual wrapper.

Instances differ by Atlas project + API keys (same host, cloud.mongodb.com). This
verifies per-instance credential ISOLATION (each child must have its own digest
session) and that a routed call targets the selected project on the wire.
"""

import re

import pytest
import responses

from holmes.plugins.toolsets.atlas_mongodb.mongodb_atlas import MongoDBAtlasToolset
from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from tests.conftest import create_mock_tool_invoke_context

P1 = {"public_key": "pk1", "private_key": "sk1", "project_id": "PROJ1"}
P2 = {"public_key": "pk2", "private_key": "sk2", "project_id": "PROJ2"}


class TestAtlasFlat:
    def test_flat_config_backwards_compatible(self):
        ts = multi_instance(MongoDBAtlasToolset)
        ok, _ = ts.prerequisites_callable(dict(P1))
        assert ok is True
        assert list(ts._children) == ["default"]
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)
        assert all(INSTANCE_PARAM_NAME not in t.parameters for t in ts.tools)


class TestAtlasMultiInstance:
    def _build(self):
        ts = multi_instance(MongoDBAtlasToolset)
        ok, _ = ts.prerequisites_callable(
            {"instances": [{"name": "a", **P1}, {"name": "b", **P2}]}
        )
        assert ok is True
        return ts

    def test_tool_surface(self):
        ts = self._build()
        assert any(t.name == "MongoDBAtlas_list_instances" for t in ts.tools)
        for tool in ts.tools:
            if not isinstance(tool, ListInstancesTool):
                assert INSTANCE_PARAM_NAME in tool.parameters

    def test_each_instance_has_its_own_isolated_credentials(self):
        """Catches credential cross-wiring: each child must carry its OWN Atlas
        keys simultaneously (a shared session would leave both with the last
        instance's credentials)."""
        ts = self._build()
        assert ts._children["a"]._session.auth.username == "pk1"
        assert ts._children["b"]._session.auth.username == "pk2"
        # distinct session objects, not a single shared one
        assert ts._children["a"]._session is not ts._children["b"]._session

    @pytest.mark.parametrize(
        "instance,project",
        [("a", "PROJ1"), ("b", "PROJ2")],
    )
    def test_routed_call_targets_selected_project(self, instance, project):
        ts = self._build()
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            rsps.add(
                responses.GET,
                re.compile(r"https://cloud\.mongodb\.com/api/atlas/v2/groups/[^/]+/alerts"),
                json={"results": []},
                status=200,
            )
            tool = next(t for t in ts.tools if t.name == "atlas_return_project_alerts")
            tool.invoke({INSTANCE_PARAM_NAME: instance}, create_mock_tool_invoke_context())
            last = rsps.calls[-1].request
            assert f"/groups/{project}/alerts" in last.url

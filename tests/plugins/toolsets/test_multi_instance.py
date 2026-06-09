"""Tests for the generic multi-instance delegation wrapper.

Uses a tiny fake single-instance toolset so the wrapper's behavior (config
decomposition, global fall-through, routing, param/list-tool injection, tolerant
health) is verified independently of any real toolset.
"""

from typing import ClassVar, List, Optional, Type

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.multi_instance import (
    INSTANCE_PARAM_NAME,
    ListInstancesTool,
    multi_instance,
)
from holmes.utils.pydantic_utils import ToolsetConfig
from tests.conftest import create_mock_tool_invoke_context


class _FakeConfig(ToolsetConfig):
    api_url: str
    api_key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


class _FakeTool(Tool):
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, toolset):
        super().__init__(
            name="fake_do",
            description="do a thing",
            parameters={"q": ToolParameter(type="string", required=False)},
        )
        self._toolset = toolset

    def _invoke(self, params, context) -> StructuredToolResult:
        cfg = self._toolset.config
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={
                "api_url": cfg.get("api_url"),
                "api_key": cfg.get("api_key"),
                "username": cfg.get("username"),
                "q": params.get("q"),
            },
            params=params,
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "fake"


class _FakeToolset(Toolset):
    config_classes: ClassVar[List[Type]] = [_FakeConfig]

    def __init__(self):
        super().__init__(
            name="fake/svc",
            description="fake",
            tools=[],
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tags=[ToolsetTag.CORE],
            enabled=False,
        )
        self.tools = [_FakeTool(self)]

    def prerequisites_callable(self, config):
        # Validate + "health check" (no network): require api_url.
        if not config.get("api_url"):
            return False, "missing api_url"
        self.config = config
        return True, f"connected to {config['api_url']}"


def _wrap(config: dict):
    ts = multi_instance(_FakeToolset)
    ts.prerequisites_callable(config)
    return ts


def _call(ts, params):
    tool = next(t for t in ts.tools if t.name == "fake_do")
    return tool.invoke(params, create_mock_tool_invoke_context())


class TestFlatShape:
    def test_flat_config_single_default_instance(self):
        ts = _wrap({"api_url": "http://one"})
        assert list(ts._children) == ["default"]
        assert [t.name for t in ts.tools] == ["fake_do"]
        # no instance param, no list tool
        assert INSTANCE_PARAM_NAME not in ts.tools[0].parameters
        assert not any(isinstance(t, ListInstancesTool) for t in ts.tools)

    def test_flat_routes_to_default(self):
        ts = _wrap({"api_url": "http://one"})
        r = _call(ts, {})
        assert r.status is StructuredToolResultStatus.SUCCESS
        assert r.data["api_url"] == "http://one"

    def test_wrapper_mirrors_child_metadata(self):
        ts = multi_instance(_FakeToolset)
        assert ts.name == "fake/svc"
        # schema comes from the child's config class
        schema = ts.get_config_schema()
        assert "_FakeConfig" in schema


class TestMultiShape:
    def test_instances_expose_param_and_list_tool(self):
        ts = _wrap(
            {"instances": [
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b"},
            ]}
        )
        names = [t.name for t in ts.tools]
        assert "fake_do" in names
        assert "fake_svc_list_instances" in names
        fake = next(t for t in ts.tools if t.name == "fake_do")
        assert INSTANCE_PARAM_NAME in fake.parameters

    def test_routing_selects_the_named_child(self):
        ts = _wrap(
            {"instances": [
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b"},
            ]}
        )
        r = _call(ts, {INSTANCE_PARAM_NAME: "b"})
        assert r.data["api_url"] == "http://b"

    def test_missing_instance_param_errors(self):
        ts = _wrap(
            {"instances": [
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b"},
            ]}
        )
        r = _call(ts, {})
        assert r.status is StructuredToolResultStatus.ERROR
        assert "instance" in r.error

    def test_unknown_instance_errors(self):
        ts = _wrap({"instances": [{"name": "a", "api_url": "http://a"}]})
        # single instance -> auto-selects, so force >1 to require the param
        ts = _wrap(
            {"instances": [
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b"},
            ]}
        )
        r = _call(ts, {INSTANCE_PARAM_NAME: "zzz"})
        assert r.status is StructuredToolResultStatus.ERROR
        assert "Unknown" in r.error

    def test_list_instances_returns_summaries(self):
        ts = _wrap(
            {"instances": [
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b"},
            ]}
        )
        tool = next(t for t in ts.tools if isinstance(t, ListInstancesTool))
        r = tool._invoke({}, create_mock_tool_invoke_context())
        got = {i["name"]: i.get("api_url") for i in r.data["instances"]}
        assert got == {"a": "http://a", "b": "http://b"}

    def test_duplicate_names_rejected(self):
        ts = multi_instance(_FakeToolset)
        ok, msg = ts.prerequisites_callable(
            {"instances": [
                {"name": "dup", "api_url": "http://a"},
                {"name": "dup", "api_url": "http://b"},
            ]}
        )
        assert ok is False
        assert "Duplicate instance name" in msg


class TestGlobalFallthrough:
    def test_global_inherited_when_instance_omits(self):
        ts = _wrap(
            {"api_key": "GLOBAL", "instances": [{"name": "a", "api_url": "http://a"}]}
        )
        r = _call(ts, {})  # single instance -> auto-select
        assert r.data["api_key"] == "GLOBAL"

    def test_auth_atomic_group_not_cross_wired(self):
        # Global api_key must NOT land on an instance that picked basic auth.
        ts = _wrap(
            {
                "api_key": "GLOBAL",
                "instances": [
                    {"name": "a", "api_url": "http://a", "username": "u", "password": "p"},
                ],
            }
        )
        r = _call(ts, {})
        assert r.data["username"] == "u"
        assert r.data["api_key"] is None  # atomic group dropped the global api_key


class TestHealthAggregation:
    def test_fails_only_when_all_unhealthy(self):
        ts = multi_instance(_FakeToolset)
        ok, msg = ts.prerequisites_callable(
            {"instances": [{"name": "a"}, {"name": "b"}]}  # no api_url -> both fail
        )
        assert ok is False

    def test_passes_when_any_healthy(self):
        ts = multi_instance(_FakeToolset)
        ok, msg = ts.prerequisites_callable(
            {"instances": [{"name": "a", "api_url": "http://a"}, {"name": "b"}]}
        )
        assert ok is True
        assert "failed:" in msg


class TestOfflineInstances:
    def _wrap_one_bad(self):
        # "good" is healthy; "bad" has no api_url -> fails prereqs -> offline.
        return _wrap(
            {"instances": [
                {"name": "good", "api_url": "http://good"},
                {"name": "bad"},
            ]}
        )

    def test_offline_instance_not_routable(self):
        ts = self._wrap_one_bad()
        assert list(ts._children) == ["good"]            # only healthy is routable
        assert "bad" in ts._offline_instances

    def test_param_and_list_tool_present_despite_offline(self):
        # >1 configured (1 healthy + 1 offline) -> instance param + list tool appear
        ts = self._wrap_one_bad()
        fake = next(t for t in ts.tools if t.name == "fake_do")
        assert INSTANCE_PARAM_NAME in fake.parameters
        assert any(isinstance(t, ListInstancesTool) for t in ts.tools)

    def test_calling_offline_instance_returns_reason(self):
        ts = self._wrap_one_bad()
        r = _call(ts, {INSTANCE_PARAM_NAME: "bad"})
        assert r.status is StructuredToolResultStatus.ERROR
        assert "offline" in r.error and "missing api_url" in r.error

    def test_list_instances_separates_offline(self):
        ts = self._wrap_one_bad()
        tool = next(t for t in ts.tools if isinstance(t, ListInstancesTool))
        r = tool._invoke({}, create_mock_tool_invoke_context())
        assert [i["name"] for i in r.data["instances"]] == ["good"]
        offline = r.data["offline_instances"]
        assert offline == [{"name": "bad", "reason": "missing api_url"}]


class TestInstanceStampAndMeta:
    def test_result_stamped_with_instance_when_multi(self):
        ts = _wrap(
            {"instances": [
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b"},
            ]}
        )
        r = _call(ts, {INSTANCE_PARAM_NAME: "b"})
        assert r.params[INSTANCE_PARAM_NAME] == "b"

    def test_result_not_stamped_for_single_default(self):
        ts = _wrap({"api_url": "http://one"})
        r = _call(ts, {"q": "x"})
        assert INSTANCE_PARAM_NAME not in (r.params or {})

    def test_single_instance_publishes_no_meta_instances(self):
        # A flat/single toolset must look like a plain single toolset (the old way):
        # no per-instance meta, so the UI doesn't expand it.
        ts = _wrap({"api_url": "http://one"})
        assert "instances" not in (ts.meta or {})

    def test_meta_instances_published(self):
        ts = _wrap(
            {"instances": [
                {"name": "good", "api_url": "http://good"},
                {"name": "bad"},
            ]}
        )
        by_name = {i["name"]: i for i in ts.meta["instances"]}
        assert by_name["good"]["healthy"] is True
        assert by_name["good"]["api_url"] == "http://good"
        assert by_name["bad"]["healthy"] is False
        assert by_name["bad"]["reason"] == "missing api_url"


class TestConfigEditorCompat:
    def test_wrapper_surfaces_child_config_classes(self):
        # The CLI `toolset config` editor filters on `toolset.config_classes` and
        # reads it to build the form. The wrapper must surface the child's classes
        # so wrapped toolsets stay configurable (the flat single-instance way) and
        # don't silently disappear from the editor.
        ts = multi_instance(_FakeToolset)
        assert ts.config_classes == _FakeToolset.config_classes
        assert ts.config_classes  # non-empty

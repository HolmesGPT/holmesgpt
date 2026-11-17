"""Remote exposure (cross-cluster tool calls) of multi-instance toolsets.

Resolution per instance: the instance's own expose_remotely, then the
toolset-level value when the user set it, then the toolset's locality
heuristic (prometheus: in-cluster URL or not), then the class default.
"""

from pathlib import Path
from typing import ClassVar, List, Optional, Type
from unittest.mock import MagicMock, patch

import pytest
import yaml

from holmes.core.conversations_worker.tool_call_worker import ToolCallWorker
from holmes.core.llm import LLM
from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    Toolset,
    ToolsetStatusEnum,
    ToolsetTag,
)
from holmes.core.toolset_manager import ToolsetManager
from holmes.plugins.toolsets.multi_instance import multi_instance
from holmes.plugins.toolsets.prometheus.prometheus import PrometheusToolset
from holmes.utils.holmes_sync_toolsets import build_remote_tools_meta
from holmes.utils.pydantic_utils import ToolsetConfig
from holmes.version import get_version

IN_CLUSTER = "http://prometheus.monitoring.svc:9090"
IN_CLUSTER_2 = "http://thanos-query.monitoring.svc:9090"
SAAS = "https://prometheus-prod.grafana.net/api/prom"
PROM = "prometheus/metrics"


def _prometheus(user_config: dict) -> Toolset:
    manager = ToolsetManager(toolsets={PROM: {"enabled": True, **user_config}})
    toolset = next(
        t
        for t in manager._list_all_toolsets(check_prerequisites=False)
        if t.name == PROM
    )
    with patch.object(PrometheusToolset, "_is_healthy", return_value=(True, "")):
        toolset.check_prerequisites()
    return toolset


def _instances(*entries: dict) -> dict:
    return {"config": {"instances": list(entries)}}


def _published_instances(toolset: Toolset) -> Optional[List[str]]:
    meta = build_remote_tools_meta(toolset)
    return None if meta is None else meta["exposed_instances"]


@pytest.mark.parametrize(
    "user_config, exposed",
    [
        pytest.param(
            _instances(
                {"name": "in", "prometheus_url": IN_CLUSTER},
                {"name": "cloud", "prometheus_url": SAAS},
            ),
            ["in"],
            id="no-overrides-locality-decides",
        ),
        pytest.param(
            _instances(
                {"name": "in", "prometheus_url": IN_CLUSTER, "expose_remotely": False},
                {"name": "in2", "prometheus_url": IN_CLUSTER_2},
            ),
            ["in2"],
            id="instance-false-withholds-in-cluster",
        ),
        pytest.param(
            _instances(
                {"name": "in", "prometheus_url": IN_CLUSTER},
                {"name": "cloud", "prometheus_url": SAAS, "expose_remotely": True},
            ),
            ["in", "cloud"],
            id="instance-true-exposes-saas",
        ),
        pytest.param(
            {"expose_remotely": True, "config": {"prometheus_url": SAAS}},
            ["default"],
            id="toolset-true-exposes-saas",
        ),
        pytest.param(
            {
                "expose_remotely": False,
                **_instances(
                    {"name": "in", "prometheus_url": IN_CLUSTER},
                    {"name": "cloud", "prometheus_url": SAAS, "expose_remotely": True},
                ),
            },
            ["cloud"],
            id="instance-true-beats-toolset-false",
        ),
        pytest.param(
            {
                "expose_remotely": True,
                **_instances(
                    {"name": "in", "prometheus_url": IN_CLUSTER, "expose_remotely": False},
                    {"name": "cloud", "prometheus_url": SAAS},
                ),
            },
            ["cloud"],
            id="instance-false-beats-toolset-true",
        ),
        pytest.param(
            {"config": {"expose_remotely": True, "prometheus_url": SAAS}},
            ["default"],
            id="flat-config-value-applies-to-default-instance",
        ),
        pytest.param(
            {
                "config": {
                    "expose_remotely": False,
                    "instances": [
                        {"name": "in", "prometheus_url": IN_CLUSTER},
                        {"name": "in2", "prometheus_url": IN_CLUSTER_2, "expose_remotely": True},
                    ],
                }
            },
            ["in2"],
            id="config-level-value-is-inherited-and-overridable",
        ),
        pytest.param(
            _instances(
                {"name": "in", "prometheus_url": IN_CLUSTER, "expose_remotely": "false"},
                {"name": "cloud", "prometheus_url": SAAS, "expose_remotely": "true"},
            ),
            ["cloud"],
            id="string-booleans",
        ),
    ],
)
def test_prometheus_instance_exposure(user_config, exposed):
    toolset = _prometheus(user_config)

    assert toolset.status == ToolsetStatusEnum.ENABLED, toolset.error
    assert toolset.remote_exposed_instances() == exposed
    assert _published_instances(toolset) == exposed


@pytest.mark.parametrize(
    "user_config",
    [
        pytest.param(
            {"expose_remotely": False, "config": {"prometheus_url": IN_CLUSTER}},
            id="toolset-false-in-cluster",
        ),
        pytest.param(
            _instances(
                {"name": "in", "prometheus_url": IN_CLUSTER, "expose_remotely": False},
                {"name": "in2", "prometheus_url": IN_CLUSTER_2, "expose_remotely": False},
            ),
            id="every-instance-false",
        ),
        pytest.param({"config": {"prometheus_url": SAAS}}, id="saas-by-default"),
    ],
)
def test_prometheus_with_no_exposed_instance_publishes_nothing(user_config):
    toolset = _prometheus(user_config)

    assert toolset.remote_exposed_instances() == []
    assert build_remote_tools_meta(toolset) is None


def test_expose_remotely_does_not_reach_the_instance_config():
    toolset = _prometheus(
        {"config": {"expose_remotely": True, "prometheus_url": IN_CLUSTER}}
    )

    child_config = toolset._children["default"].config
    assert "expose_remotely" not in child_config.model_dump()
    assert "expose_remotely" not in (child_config.model_extra or {})


@pytest.mark.parametrize("value", ["maybe", 2, ["true"]])
def test_invalid_instance_value_fails_the_toolset_with_a_clear_error(value):
    toolset = _prometheus(
        _instances({"name": "in", "prometheus_url": IN_CLUSTER, "expose_remotely": value})
    )

    assert toolset.status == ToolsetStatusEnum.FAILED
    assert "Invalid prometheus/metrics configuration" in toolset.error
    assert "'in'" in toolset.error and "expose_remotely" in toolset.error


def test_offline_instance_is_never_exposed():
    manager = ToolsetManager(
        toolsets={
            PROM: {
                "enabled": True,
                **_instances(
                    {"name": "up", "prometheus_url": IN_CLUSTER},
                    {"name": "down", "prometheus_url": IN_CLUSTER_2, "expose_remotely": True},
                ),
            }
        }
    )
    toolset = next(
        t for t in manager._list_all_toolsets(check_prerequisites=False) if t.name == PROM
    )

    def healthy_unless_down(self):
        if "thanos" in self.config.prometheus_url:
            return False, "connection refused"
        return True, ""

    with patch.object(PrometheusToolset, "_is_healthy", healthy_unless_down):
        toolset.check_prerequisites()

    assert toolset.remote_exposed_instances() == ["up"]


class _FakeConfig(ToolsetConfig):
    api_url: str


class _EchoTool(Tool):
    def __init__(self, toolset: Toolset):
        super().__init__(name="fake_echo", description="echo", parameters={})
        self._owner = toolset

    def _invoke(self, params, context) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=f"ran against {self._owner.config['api_url']}",
            params=params,
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "echo"


class _FakeToolset(Toolset):
    config_classes: ClassVar[List[Type]] = [_FakeConfig]

    def __init__(self):
        super().__init__(
            name="fake/remote",
            description="fake",
            tools=[],
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tags=[ToolsetTag.CORE],
        )
        self.tools = [_EchoTool(self)]

    def prerequisites_callable(self, config):
        self.config = config
        return True, ""


class _UserOverride(Toolset):
    pass


def _fake(config: dict, expose_remotely: Optional[bool] = None) -> Toolset:
    wrapper = multi_instance(_FakeToolset)
    if expose_remotely is not None:
        wrapper.override_with(
            _UserOverride(
                name="fake/remote", description="", tools=[], expose_remotely=expose_remotely
            )
        )
    wrapper.enabled = True
    wrapper.config = config
    wrapper.check_prerequisites()
    return wrapper


def test_toolset_without_locality_heuristic_uses_the_class_default():
    wrapper = _fake({"instances": [{"name": "a", "api_url": "http://a"}]})

    assert wrapper.remote_exposed_instances() == []
    assert build_remote_tools_meta(wrapper) is None


def test_toolset_without_locality_heuristic_honours_both_levels():
    wrapper = _fake(
        {
            "instances": [
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b", "expose_remotely": False},
            ]
        },
        expose_remotely=True,
    )

    assert wrapper.remote_exposed_instances() == ["a"]
    assert _published_instances(wrapper) == ["a"]


def _worker(toolset: Toolset) -> ToolCallWorker:
    executor = MagicMock()
    executor.tools_by_name = {t.name: t for t in toolset.tools}
    executor._tool_to_toolset = {t.name: toolset for t in toolset.tools}
    config = MagicMock()
    config.create_server_tool_executor.return_value = executor
    llm = MagicMock(spec=LLM)
    llm.count_tokens.return_value = MagicMock(total_tokens=10)
    config._get_llm.return_value = llm
    return ToolCallWorker(dal=MagicMock(), config=config, holmes_id="h-test")


def _call(worker: ToolCallWorker, tool_name: str, instance: Optional[str]) -> dict:
    return worker._execute(
        {
            "id": "row-1",
            "user_id": None,
            "tool_request": {
                "tool_name": tool_name,
                "tool_params": {},
                "instance": instance,
                "tool_call_id": "call-1",
                "max_token_count": 16000,
            },
            "metadata": {"source_version": get_version()},
        }
    )


def _two_instance_fake(expose_remotely: Optional[bool]) -> Toolset:
    return _fake(
        {
            "instances": [
                {"name": "a", "api_url": "http://a"},
                {"name": "b", "api_url": "http://b", "expose_remotely": True},
            ]
        },
        expose_remotely=expose_remotely,
    )


def test_executor_runs_an_instance_exposed_against_a_toolset_level_false():
    resp = _call(_worker(_two_instance_fake(False)), "fake_echo", "b")

    assert resp["status"] == StructuredToolResultStatus.SUCCESS.value, resp
    assert resp["data"] == "ran against http://b"


def test_executor_defaults_to_the_only_exposed_instance():
    resp = _call(_worker(_two_instance_fake(False)), "fake_echo", None)

    assert resp["status"] == StructuredToolResultStatus.SUCCESS.value, resp
    assert resp["data"] == "ran against http://b"


def test_executor_refuses_a_withheld_instance():
    resp = _call(_worker(_two_instance_fake(False)), "fake_echo", "a")

    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "instance 'a' is not exposed on this cluster" in resp["error"]


def test_executor_refuses_a_toolset_with_no_exposed_instance():
    wrapper = _fake({"instances": [{"name": "a", "api_url": "http://a"}]}, False)

    resp = _call(_worker(wrapper), "fake_echo", None)

    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "toolset 'fake/remote' is not exposed for remote execution" in resp["error"]


def test_executor_refuses_a_withheld_prometheus_instance():
    toolset = _prometheus(
        _instances(
            {"name": "in", "prometheus_url": IN_CLUSTER, "expose_remotely": False},
            {"name": "in2", "prometheus_url": IN_CLUSTER_2},
        )
    )
    worker = _worker(toolset)

    resp = _call(worker, "execute_prometheus_instant_query", "in")

    assert resp["status"] == StructuredToolResultStatus.ERROR.value
    assert "instance 'in' is not exposed on this cluster" in resp["error"]


def test_the_docs_example_exposes_what_its_comments_say():
    page = (
        Path(__file__).parents[3] / "docs" / "data-sources" / "cross-cluster-tools.md"
    ).read_text()
    section = page.split("### Prometheus and other multi-instance toolsets")[1]
    example = yaml.safe_load(section.split("```yaml")[1].split("```")[0])

    toolset = _prometheus(example["toolsets"][PROM])

    assert toolset.remote_exposed_instances() == ["in-cluster", "grafana-cloud"]


@pytest.mark.parametrize("expose_remotely", [False, None], ids=["toolset-false", "toolset-default"])
def test_executor_refuses_a_withheld_instance_named_inside_the_tool_params(expose_remotely):
    worker = _worker(_two_instance_fake(expose_remotely))
    row = {
        "id": "row-1",
        "user_id": None,
        "tool_request": {
            "tool_name": "fake_echo",
            "tool_params": {"instance": "a"},
            "instance": None,
            "tool_call_id": "call-1",
            "max_token_count": 16000,
        },
        "metadata": {"source_version": get_version()},
    }

    resp = worker._execute(row)

    assert resp["status"] == StructuredToolResultStatus.ERROR.value, resp
    assert "instance 'a' is not exposed on this cluster" in resp["error"]

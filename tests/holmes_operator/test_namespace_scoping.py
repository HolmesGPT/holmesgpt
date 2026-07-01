"""Unit tests for namespace-scoped operator deployment (issue #2203).

The operator can run scoped to a single namespace instead of cluster-wide,
controlled by the HOLMES_OPERATOR_NAMESPACE env var. When set, it lists
ScheduledHealthChecks within that namespace only (namespaced RBAC); when unset,
it keeps the original cluster-wide behavior.
"""

import importlib
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import kopf
import pytest
import yaml

from holmes_operator import config as operator_config

# Import context before manager/operator: context wires up the operator package
# in the order that resolves the manager <-> job_executor <-> context import cycle.
from holmes_operator import context
from holmes_operator import operator
from holmes_operator.scheduler.manager import SchedulerManager

HELM_DIR = Path(__file__).resolve().parents[2] / "helm" / "holmes"


@pytest.fixture(autouse=True)
def _reset_config_module():
    """Reload config after each test so a monkeypatched env var can't leak.

    Runs after monkeypatch has restored the environment, so the reload picks up
    the ambient (unset) HOLMES_OPERATOR_NAMESPACE.
    """
    yield
    importlib.reload(operator_config)


class TestNamespaceConfig:
    def test_defaults_to_cluster_wide(self, monkeypatch):
        monkeypatch.delenv("HOLMES_OPERATOR_NAMESPACE", raising=False)
        reloaded = importlib.reload(operator_config)
        assert reloaded.HOLMES_OPERATOR_NAMESPACE is None
        assert reloaded.OperatorConfig.load().operator_namespace is None

    def test_empty_string_is_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("HOLMES_OPERATOR_NAMESPACE", "")
        reloaded = importlib.reload(operator_config)
        assert reloaded.HOLMES_OPERATOR_NAMESPACE is None
        assert reloaded.OperatorConfig.load().operator_namespace is None

    def test_namespace_scoped_when_set(self, monkeypatch):
        monkeypatch.setenv("HOLMES_OPERATOR_NAMESPACE", "team-a")
        reloaded = importlib.reload(operator_config)
        assert reloaded.HOLMES_OPERATOR_NAMESPACE == "team-a"
        assert reloaded.OperatorConfig.load().operator_namespace == "team-a"


class TestSchedulerNamespaceScoping:
    @staticmethod
    def _mock_api():
        api = MagicMock()
        api.list_namespaced_custom_object = MagicMock(return_value={"items": []})
        api.list_cluster_custom_object = MagicMock(return_value={"items": []})
        return api

    async def test_namespace_scoped_lists_single_namespace(self):
        api = self._mock_api()
        manager = SchedulerManager(timezone_str="UTC", k8s_api=api, namespace="team-a")

        await manager._load_existing_schedules()

        api.list_namespaced_custom_object.assert_called_once()
        _, kwargs = api.list_namespaced_custom_object.call_args
        assert kwargs["namespace"] == "team-a"
        assert kwargs["plural"] == "scheduledhealthchecks"
        api.list_cluster_custom_object.assert_not_called()

    async def test_cluster_wide_lists_all_namespaces(self):
        api = self._mock_api()
        manager = SchedulerManager(timezone_str="UTC", k8s_api=api)

        await manager._load_existing_schedules()

        api.list_cluster_custom_object.assert_called_once()
        api.list_namespaced_custom_object.assert_not_called()


class TestStartupHandlerSettings:
    """The kopf settings branch is the crux of the RBAC fix: namespace mode must
    disable cluster-scoped scanning and peering so the operator needs no
    ClusterRole; cluster-wide mode must leave them at their defaults."""

    async def _run_startup(self, monkeypatch, namespace):
        monkeypatch.setattr(operator_config, "HOLMES_OPERATOR_NAMESPACE", namespace)
        monkeypatch.setattr(context, "initialize", AsyncMock())
        settings = kopf.OperatorSettings()
        await operator.startup_handler(settings=settings)
        return settings

    async def test_namespace_mode_disables_cluster_scoped_features(self, monkeypatch):
        settings = await self._run_startup(monkeypatch, "team-a")
        assert settings.scanning.disabled is True
        assert settings.peering.standalone is True

    async def test_cluster_mode_keeps_scanning_and_peering_defaults(self, monkeypatch):
        settings = await self._run_startup(monkeypatch, None)
        assert settings.scanning.disabled is False
        assert settings.peering.standalone is False


class TestOperatorValues:
    def test_namespaced_defaults_to_false(self):
        with open(HELM_DIR / "values.yaml") as f:
            operator_values = yaml.safe_load(f)["operator"]
        assert operator_values["namespaced"] is False


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm binary not available")
class TestOperatorHelmRender:
    """Render the chart with helm and assert on the actual output, so a template
    change that (e.g.) leaks a cluster-scoped rule into the namespaced Role fails."""

    CLUSTER_SCOPED = {"namespaces", "customresourcedefinitions", "clusterkopfpeerings"}

    @staticmethod
    def _render(template, namespaced, namespace):
        result = subprocess.run(
            [
                "helm", "template", "holmes", str(HELM_DIR),
                "-n", namespace,
                "--set", "operator.enabled=true",
                "--set", f"operator.namespaced={'true' if namespaced else 'false'}",
                "--show-only", f"templates/{template}",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    @staticmethod
    def _resources(role):
        return {res for rule in role["rules"] for res in rule.get("resources", [])}

    def test_cluster_wide_renders_clusterrole_with_cluster_rules(self):
        docs = self._render("operator-rbac.yaml", namespaced=False, namespace="monitoring")
        kinds = {d["kind"] for d in docs}
        assert "ClusterRole" in kinds and "ClusterRoleBinding" in kinds
        assert "Role" not in kinds and "RoleBinding" not in kinds

        role = next(d for d in docs if d["kind"] == "ClusterRole")
        assert self.CLUSTER_SCOPED <= self._resources(role)

        binding = next(d for d in docs if d["kind"] == "ClusterRoleBinding")
        assert binding["roleRef"]["kind"] == "ClusterRole"

    def test_namespaced_renders_role_without_cluster_rules(self):
        docs = self._render("operator-rbac.yaml", namespaced=True, namespace="team-a")
        kinds = {d["kind"] for d in docs}
        assert "Role" in kinds and "RoleBinding" in kinds
        assert "ClusterRole" not in kinds and "ClusterRoleBinding" not in kinds

        role = next(d for d in docs if d["kind"] == "Role")
        assert role["metadata"]["namespace"] == "team-a"
        resources = self._resources(role)
        # No cluster-scoped rule leaks into the namespaced Role...
        assert not (self.CLUSTER_SCOPED & resources)
        # ...but it still grants the namespaced CRDs it manages.
        assert {"scheduledhealthchecks", "triggeredhealthchecks", "healthchecks"} <= resources

        binding = next(d for d in docs if d["kind"] == "RoleBinding")
        assert binding["roleRef"]["kind"] == "Role"
        assert binding["metadata"]["namespace"] == "team-a"
        assert binding["subjects"][0]["namespace"] == "team-a"

    def test_deployment_injects_namespace_env_only_when_namespaced(self):
        def _envs(namespaced, namespace):
            docs = self._render("operator-deployment.yaml", namespaced, namespace)
            container = docs[0]["spec"]["template"]["spec"]["containers"][0]
            return {e["name"]: e.get("value") for e in container.get("env", [])}

        assert _envs(True, "team-a").get("HOLMES_OPERATOR_NAMESPACE") == "team-a"
        assert "HOLMES_OPERATOR_NAMESPACE" not in _envs(False, "monitoring")

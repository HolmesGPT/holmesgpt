"""Regression checks for the Holmes pod's ingress NetworkPolicy.

Its selectors and port must stay in step with the Deployments they refer to.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
POLICY_NAME = "test-release-holmes-network-policy"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm binary not available")


def render(extra_args: Optional[List[str]] = None) -> List[dict]:
    cmd = ["helm", "template", "test-release", str(HELM_DIR)] + (extra_args or [])
    output = subprocess.check_output(cmd, text=True)
    return [doc for doc in yaml.safe_load_all(output) if doc]


def get_doc(docs: List[dict], kind: str, name: str) -> Optional[dict]:
    for doc in docs:
        if doc["kind"] == kind and doc["metadata"]["name"] == name:
            return doc
    return None


def get_policy(docs: List[dict]) -> dict:
    policy = get_doc(docs, "NetworkPolicy", POLICY_NAME)
    assert policy is not None
    return policy


def matches(peer: dict, pod_labels: dict) -> bool:
    wanted = peer["podSelector"]["matchLabels"]
    return all(pod_labels.get(key) == value for key, value in wanted.items())


def test_default_policy_selects_the_holmes_pod_on_its_api_port():
    docs = render()
    policy = get_policy(docs)
    deployment = get_doc(docs, "Deployment", "test-release-holmes")
    assert deployment is not None
    pod = deployment["spec"]["template"]

    selector = policy["spec"]["podSelector"]["matchLabels"]
    assert selector == {"app": "holmes"}
    assert all(pod["metadata"]["labels"].get(k) == v for k, v in selector.items())

    assert len(policy["spec"]["ingress"]) == 1
    container_port = pod["spec"]["containers"][0]["ports"][0]["containerPort"]
    assert policy["spec"]["ingress"][0]["ports"] == [{"protocol": "TCP", "port": container_port}]


def test_policy_does_not_restrict_egress():
    spec = get_policy(render())["spec"]
    assert spec["policyTypes"] == ["Ingress"]
    assert "egress" not in spec


def test_default_callers_are_the_runner_and_the_operator_in_any_namespace():
    peers = get_policy(render())["spec"]["ingress"][0]["from"]
    assert peers == [
        # the Robusta chart labels the runner pod robustaComponent: runner
        {"namespaceSelector": {}, "podSelector": {"matchLabels": {"robustaComponent": "runner"}}},
        {"namespaceSelector": {}, "podSelector": {"matchLabels": {"app": "holmes-operator"}}},
    ]


def test_operator_pod_labels_satisfy_the_operator_peer():
    docs = render(["--set", "operator.enabled=true"])
    operator = get_doc(docs, "Deployment", "test-release-operator")
    assert operator is not None
    operator_labels = operator["spec"]["template"]["metadata"]["labels"]

    peers = get_policy(docs)["spec"]["ingress"][0]["from"]
    assert any(matches(peer, operator_labels) for peer in peers)


def test_additional_ingress_rules_join_the_same_port_limited_rule():
    extra = [
        {"podSelector": {"matchLabels": {"app": "my-holmes-client"}}},
        {"ipBlock": {"cidr": "10.0.0.0/16"}},
    ]
    docs = render(["--set-json", f"networkPolicy.additionalIngressRules={json.dumps(extra)}"])
    ingress = get_policy(docs)["spec"]["ingress"]

    # a second rule without ports would open every port to the extra peers
    assert len(ingress) == 1
    assert ingress[0]["from"][-2:] == extra
    assert len(ingress[0]["from"]) == 4
    assert ingress[0]["ports"] == [{"protocol": "TCP", "port": 5050}]


def test_disabled_renders_no_policy():
    docs = render(["--set", "networkPolicy.enabled=false"])
    assert get_doc(docs, "NetworkPolicy", POLICY_NAME) is None


def test_null_network_policy_values_render_no_policy():
    docs = render(["--set", "networkPolicy=null"])
    assert get_doc(docs, "NetworkPolicy", POLICY_NAME) is None


def test_common_labels_and_annotations_are_applied():
    docs = render(["--set", "commonLabels.team=sre", "--set", "commonAnnotations.owner=platform"])
    metadata = get_policy(docs)["metadata"]
    assert metadata["labels"] == {"app": "holmes", "team": "sre"}
    assert metadata["annotations"] == {"owner": "platform"}

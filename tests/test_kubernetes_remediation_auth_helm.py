"""Regression checks for the kubernetes-remediation MCP bearer-token wiring.

The token Secret is created at deploy time by a hook Job, so clusterless
renders (ArgoCD, `helm template`) are deterministic and do not roll
Holmes and the MCP server on every sync (ROB-1365). The hook's shell script is
also executed here against a stub `kubectl` to cover each branch.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Dict, List, Optional

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
RELEASE = "rel"
NAMESPACE = "ns1"
SECRET_NAME = f"{RELEASE}-k8s-remediation-mcp-token"
BOOTSTRAP_NAME = f"{RELEASE}-k8s-remediation-mcp-auth-bootstrap"
HOLMES_DEPLOYMENT = f"{RELEASE}-holmes"
MCP_DEPLOYMENT = f"{RELEASE}-k8s-remediation-mcp-server"

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm binary not available"
)


def render_raw(*sets: str) -> str:
    cmd = ["helm", "template", RELEASE, str(HELM_DIR), "-n", NAMESPACE]
    cmd += ["--set", "mcpAddons.kubernetesRemediation.enabled=true"]
    for s in sets:
        cmd += ["--set", s]
    return subprocess.check_output(cmd, text=True)


def render(*sets: str) -> List[dict]:
    return [d for d in yaml.safe_load_all(render_raw(*sets)) if d]


def find(docs: List[dict], kind: str, name: str) -> Optional[dict]:
    for d in docs:
        if d["kind"] == kind and d["metadata"]["name"] == name:
            return d
    return None


def bootstrap_docs(docs: List[dict]) -> List[dict]:
    return [d for d in docs if d["metadata"]["name"] == BOOTSTRAP_NAME]


def container(deployment: dict) -> dict:
    return deployment["spec"]["template"]["spec"]["containers"][0]


def env_secret_ref(deployment: dict, env_name: str) -> Optional[str]:
    for e in container(deployment).get("env", []):
        if e["name"] == env_name:
            return e["valueFrom"]["secretKeyRef"]["name"]
    return None


def pod_annotations(deployment: dict) -> Dict[str, str]:
    return deployment["spec"]["template"]["metadata"].get("annotations") or {}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_is_deterministic():
    renders = {render_raw() for _ in range(3)}
    assert len(renders) == 1, "token-dependent output changes between renders"


def test_no_token_or_token_checksum_in_rendered_manifests():
    docs = render()
    assert find(docs, "Secret", SECRET_NAME) is None
    assert find(docs, "Secret", f"{RELEASE}-k8s-remediation-mcp-auth") is None
    for name in (HOLMES_DEPLOYMENT, MCP_DEPLOYMENT):
        annotations = pod_annotations(find(docs, "Deployment", name))
        assert not any("auth-token" in k for k in annotations), annotations


def test_both_deployments_read_the_bootstrapped_secret():
    docs = render()
    assert (
        env_secret_ref(
            find(docs, "Deployment", HOLMES_DEPLOYMENT), "K8S_REMEDIATION_MCP_TOKEN"
        )
        == SECRET_NAME
    )
    assert (
        env_secret_ref(find(docs, "Deployment", MCP_DEPLOYMENT), "MCP_AUTH_TOKEN")
        == SECRET_NAME
    )


def test_bootstrap_resources_are_ordered_hooks():
    hooks = {d["kind"]: d["metadata"]["annotations"] for d in bootstrap_docs(render())}
    assert set(hooks) == {"ServiceAccount", "Role", "RoleBinding", "Job"}
    for ann in hooks.values():
        assert ann["helm.sh/hook"] == "pre-install,pre-upgrade"
        assert (
            ann["helm.sh/hook-delete-policy"] == "before-hook-creation,hook-succeeded"
        )
    job_weight = int(hooks["Job"]["helm.sh/hook-weight"])
    for kind in ("ServiceAccount", "Role", "RoleBinding"):
        assert int(hooks[kind]["helm.sh/hook-weight"]) < job_weight


def test_bootstrap_role_is_least_privilege():
    docs = render()
    role = find(docs, "Role", BOOTSTRAP_NAME)
    assert role["metadata"]["namespace"] == NAMESPACE
    assert role["rules"] == [
        {"apiGroups": [""], "resources": ["secrets"], "verbs": ["create"]},
        {
            "apiGroups": [""],
            "resources": ["secrets"],
            "resourceNames": [SECRET_NAME],
            "verbs": ["get"],
        },
        {
            "apiGroups": ["apps"],
            "resources": ["deployments"],
            "resourceNames": [HOLMES_DEPLOYMENT, MCP_DEPLOYMENT],
            "verbs": ["get", "patch"],
        },
    ]
    binding = find(docs, "RoleBinding", BOOTSTRAP_NAME)
    assert binding["roleRef"]["name"] == BOOTSTRAP_NAME
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": BOOTSTRAP_NAME, "namespace": NAMESPACE}
    ]


def test_bootstrap_job_spec():
    docs = render()
    job = find(docs, "Job", BOOTSTRAP_NAME)
    pod = job["spec"]["template"]["spec"]
    c = pod["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["SECRET_NAME"] == SECRET_NAME
    assert env["NAMESPACE"] == NAMESPACE
    assert env["DEPLOYMENTS"].split() == [HOLMES_DEPLOYMENT, MCP_DEPLOYMENT]
    for name in env["DEPLOYMENTS"].split():
        assert find(docs, "Deployment", name) is not None
    assert pod["serviceAccountName"] == BOOTSTRAP_NAME
    assert pod["restartPolicy"] == "Never"
    assert c["image"] == container(find(docs, "Deployment", MCP_DEPLOYMENT))["image"]
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    assert c["securityContext"]["runAsNonRoot"] is True
    assert {"name": "tmp", "mountPath": env["HOME"]} in c["volumeMounts"]
    assert job["spec"]["backoffLimit"] > 0


def test_bootstrap_job_follows_scheduling_and_common_metadata():
    docs = render(
        "mcpAddons.kubernetesRemediation.nodeSelector.pool=infra",
        "mcpAddons.kubernetesRemediation.tolerations[0].key=dedicated",
        "mcpAddons.kubernetesRemediation.tolerations[0].operator=Exists",
        "commonLabels.team=sre",
        "commonAnnotations.owner=platform",
        "mcpAddons.kubernetesRemediation.registry=my.registry",
        "mcpAddons.kubernetesRemediation.image=mcp:9.9.9",
    )
    job = find(docs, "Job", BOOTSTRAP_NAME)
    pod = job["spec"]["template"]["spec"]
    assert pod["nodeSelector"] == {"pool": "infra"}
    assert pod["tolerations"] == [{"key": "dedicated", "operator": "Exists"}]
    assert pod["containers"][0]["image"] == "my.registry/mcp:9.9.9"
    for d in bootstrap_docs(docs):
        assert d["metadata"]["labels"]["team"] == "sre"
        assert d["metadata"]["annotations"]["owner"] == "platform"
        assert d["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"


def test_existing_secret_skips_bootstrap_and_is_wired_on_both_sides():
    docs = render("mcpAddons.kubernetesRemediation.auth.existingSecret=my-auth")
    assert bootstrap_docs(docs) == []
    assert [d for d in docs if d["kind"] == "Secret"] == []
    holmes = find(docs, "Deployment", HOLMES_DEPLOYMENT)
    mcp = find(docs, "Deployment", MCP_DEPLOYMENT)
    assert env_secret_ref(holmes, "K8S_REMEDIATION_MCP_TOKEN") == "my-auth"
    assert env_secret_ref(mcp, "MCP_AUTH_TOKEN") == "my-auth"
    for d in (holmes, mcp):
        assert not any("auth-token" in k for k in pod_annotations(d))


def test_auth_disabled_renders_no_token_wiring():
    docs = render("mcpAddons.kubernetesRemediation.auth.enabled=false")
    assert bootstrap_docs(docs) == []
    assert (
        env_secret_ref(
            find(docs, "Deployment", HOLMES_DEPLOYMENT), "K8S_REMEDIATION_MCP_TOKEN"
        )
        is None
    )
    assert (
        env_secret_ref(find(docs, "Deployment", MCP_DEPLOYMENT), "MCP_AUTH_TOKEN")
        is None
    )


def test_addon_disabled_renders_no_bootstrap():
    docs = render("mcpAddons.kubernetesRemediation.enabled=false")
    assert bootstrap_docs(docs) == []
    assert find(docs, "Deployment", MCP_DEPLOYMENT) is None


# ---------------------------------------------------------------------------
# Bootstrap script, run against a stub kubectl
# ---------------------------------------------------------------------------

FAKE_KUBECTL = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys
    state_path = os.environ["FAKE_KUBECTL_STATE"]
    state = json.load(open(state_path))
    args = sys.argv[1:]
    state["calls"].append(args)

    def save():
        json.dump(state, open(state_path, "w"))

    if args[:2] == ["get", "secret"]:
        if state.get("get_secret_error"):
            save(); sys.stderr.write(state["get_secret_error"]); sys.exit(1)
        if args[2] in state["secrets"]:
            print("secret/" + args[2])
    elif args[:2] == ["get", "deployment"]:
        if state.get("get_deployment_error"):
            save(); sys.stderr.write(state["get_deployment_error"]); sys.exit(1)
        refs = state["deployments"].get(args[2])
        if refs is not None:
            print(" ".join(refs), end="")
    elif args[:2] == ["create", "-f"]:
        if state.get("create_error"):
            save(); sys.stderr.write(state["create_error"]); sys.exit(1)
        manifest = json.load(open(args[2]))
        state["created"].append(manifest)
        state["secrets"].append(manifest["metadata"]["name"])
        print("secret/" + manifest["metadata"]["name"] + " created")
    elif args[:2] == ["rollout", "restart"]:
        print("deployment.apps/" + args[3] + " restarted")
    else:
        save(); sys.stderr.write("unexpected kubectl call: %r" % args); sys.exit(99)
    save()
    """
)


@pytest.fixture(scope="module")
def bootstrap_job() -> dict:
    return find(render(), "Job", BOOTSTRAP_NAME)


def run_bootstrap(job: dict, tmp_path: Path, **state) -> tuple:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(FAKE_KUBECTL.format(python=sys.executable))
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    python3 = bin_dir / "python3"
    python3.symlink_to(sys.executable)

    state_file = tmp_path / "state.json"
    state.setdefault("secrets", [])
    state.setdefault("deployments", {})
    state.update(calls=[], created=[])
    state_file.write_text(json.dumps(state))

    c = job["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    env.update(
        HOME=str(tmp_path),
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        FAKE_KUBECTL_STATE=str(state_file),
    )
    proc = subprocess.run(
        c["command"] + c["args"], env=env, capture_output=True, text=True
    )
    return proc, json.loads(state_file.read_text())


def restarted(result: dict) -> List[str]:
    return [c[3] for c in result["calls"] if c[:2] == ["rollout", "restart"]]


def test_script_keeps_an_existing_secret(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        secrets=[SECRET_NAME],
        deployments={HOLMES_DEPLOYMENT: [SECRET_NAME], MCP_DEPLOYMENT: [SECRET_NAME]},
    )
    assert proc.returncode == 0, proc.stderr
    assert "keeping its token" in proc.stdout
    assert result["created"] == []
    assert restarted(result) == []


def test_script_creates_secret_on_fresh_install(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(bootstrap_job, tmp_path)
    assert proc.returncode == 0, proc.stderr
    [secret] = result["created"]
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == SECRET_NAME
    assert secret["metadata"]["namespace"] == NAMESPACE
    assert "app.kubernetes.io/instance" not in secret["metadata"]["labels"]
    token = secret["stringData"]["token"]
    assert len(token) >= 40
    assert token.replace("-", "").replace("_", "").isalnum()
    assert restarted(result) == []


def test_script_generates_a_new_token_each_time(bootstrap_job, tmp_path):
    tokens = set()
    for i in range(2):
        run_dir = tmp_path / str(i)
        run_dir.mkdir()
        proc, result = run_bootstrap(bootstrap_job, run_dir)
        assert proc.returncode == 0, proc.stderr
        tokens.add(result["created"][0]["stringData"]["token"])
    assert len(tokens) == 2


def test_script_restarts_deployments_reading_the_secret_on_rotation(
    bootstrap_job, tmp_path
):
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        deployments={
            HOLMES_DEPLOYMENT: ["robusta-ui-token", SECRET_NAME],
            MCP_DEPLOYMENT: [SECRET_NAME],
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert len(result["created"]) == 1
    assert restarted(result) == [HOLMES_DEPLOYMENT, MCP_DEPLOYMENT]


def test_script_does_not_restart_deployments_still_on_the_old_secret(
    bootstrap_job, tmp_path
):
    old = f"{RELEASE}-k8s-remediation-mcp-auth"
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        deployments={HOLMES_DEPLOYMENT: [old], MCP_DEPLOYMENT: [f"{old}-extra"]},
    )
    assert proc.returncode == 0, proc.stderr
    assert len(result["created"]) == 1
    assert restarted(result) == []


def test_script_tolerates_a_concurrent_create(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        create_error=f'Error from server (AlreadyExists): secrets "{SECRET_NAME}" already exists',
        deployments={HOLMES_DEPLOYMENT: [SECRET_NAME]},
    )
    assert proc.returncode == 0, proc.stderr
    assert "created concurrently" in proc.stdout
    assert restarted(result) == []


def test_script_fails_when_create_is_rejected(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        create_error="Error from server (Forbidden): secrets is forbidden: exceeded quota",
        deployments={HOLMES_DEPLOYMENT: [SECRET_NAME]},
    )
    assert proc.returncode == 1
    assert "exceeded quota" in proc.stderr
    assert restarted(result) == []


def test_script_fails_when_secret_cannot_be_read(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        get_secret_error='Error from server (Forbidden): secrets "x" is forbidden',
    )
    assert proc.returncode != 0
    assert not any(c[:1] == ["create"] for c in result["calls"])


def test_script_fails_when_a_deployment_cannot_be_read(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        get_deployment_error='Error from server (Forbidden): deployments.apps "x" is forbidden',
    )
    assert proc.returncode != 0
    assert len(result["created"]) == 1
    assert restarted(result) == []

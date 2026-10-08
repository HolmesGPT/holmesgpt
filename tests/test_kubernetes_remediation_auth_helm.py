"""Regression checks for the kubernetes-remediation MCP bearer-token wiring.

The token Secret is created at deploy time by a hook Job, so clusterless
renders (ArgoCD, `helm template`) are deterministic and do not roll
Holmes and the MCP server on every sync (ROB-1365). The hook's inline Python
program is also executed here against a stub `kubectl` to cover each branch.
"""

import hashlib
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
LEGACY_SECRET_NAME = f"{RELEASE}-k8s-remediation-mcp-auth"
ROTATION_KEY = "robusta.dev/k8s-remediation-token-rotation"
HOOKS = "pre-install,pre-upgrade,pre-rollback"

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
        assert ann["helm.sh/hook"] == HOOKS
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
            "verbs": ["get", "delete"],
        },
        {
            "apiGroups": [""],
            "resources": ["secrets"],
            "resourceNames": [LEGACY_SECRET_NAME],
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
    assert env["LEGACY_SECRET_NAME"] == LEGACY_SECRET_NAME
    assert env["ROTATION"] == "0"
    assert json.loads(env["SECRET_LABELS"]) == {}
    assert json.loads(env["SECRET_ANNOTATIONS"]) == {}
    for name in env["DEPLOYMENTS"].split():
        assert find(docs, "Deployment", name) is not None
    assert pod["serviceAccountName"] == BOOTSTRAP_NAME
    assert pod["restartPolicy"] == "Never"
    assert c["image"] == container(find(docs, "Deployment", MCP_DEPLOYMENT))["image"]
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    assert c["securityContext"]["runAsNonRoot"] is True
    assert {"name": "tmp", "mountPath": env["HOME"]} in c["volumeMounts"]
    assert job["spec"]["backoffLimit"] > 0
    assert job["spec"]["ttlSecondsAfterFinished"] > 0
    assert "imagePullSecrets" not in pod


def test_bootstrap_job_uses_the_chart_image_pull_secrets():
    pod = find(render("imagePullSecrets[0].name=regcred"), "Job", BOOTSTRAP_NAME)[
        "spec"
    ]["template"]["spec"]
    assert pod["imagePullSecrets"] == [{"name": "regcred"}]


def test_bootstrap_names_fit_kubernetes_limits_for_long_release_names():
    release = "r" * 53
    out = subprocess.check_output(
        [
            "helm",
            "template",
            release,
            str(HELM_DIR),
            "--set",
            "mcpAddons.kubernetesRemediation.enabled=true",
        ],
        text=True,
    )
    docs = [d for d in yaml.safe_load_all(out) if d]
    hooks = [
        d for d in docs if "helm.sh/hook" in (d["metadata"].get("annotations") or {})
    ]
    assert {d["kind"] for d in hooks} == {
        "ServiceAccount",
        "Role",
        "RoleBinding",
        "Job",
    }
    names = {d["metadata"]["name"] for d in hooks}
    assert len(names) == 1
    [name] = names
    assert len(name) <= 63 and not name.endswith("-")
    binding = next(d for d in hooks if d["kind"] == "RoleBinding")
    assert binding["roleRef"]["name"] == name
    assert binding["subjects"][0]["name"] == name


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
    env = {e["name"]: e["value"] for e in pod["containers"][0]["env"]}
    assert json.loads(env["SECRET_LABELS"]) == {"team": "sre"}
    assert json.loads(env["SECRET_ANNOTATIONS"]) == {"owner": "platform"}
    for d in bootstrap_docs(docs):
        assert d["metadata"]["labels"]["team"] == "sre"
        assert d["metadata"]["annotations"]["owner"] == "platform"
        assert d["metadata"]["annotations"]["helm.sh/hook"] == HOOKS


def test_rotation_is_rendered_into_both_pod_templates():
    for sets, expected in (
        ((), "0"),
        (("mcpAddons.kubernetesRemediation.auth.rotation=7",), "7"),
    ):
        docs = render(*sets)
        job_env = {
            e["name"]: e["value"]
            for e in find(docs, "Job", BOOTSTRAP_NAME)["spec"]["template"]["spec"][
                "containers"
            ][0]["env"]
        }
        assert job_env["ROTATION"] == expected
        for name in (HOLMES_DEPLOYMENT, MCP_DEPLOYMENT):
            assert (
                pod_annotations(find(docs, "Deployment", name))[ROTATION_KEY]
                == expected
            )


def test_existing_secret_skips_bootstrap_and_is_wired_on_both_sides():
    docs = render("mcpAddons.kubernetesRemediation.auth.existingSecret=my-auth")
    assert bootstrap_docs(docs) == []
    assert [d for d in docs if d["kind"] == "Secret"] == []
    holmes = find(docs, "Deployment", HOLMES_DEPLOYMENT)
    mcp = find(docs, "Deployment", MCP_DEPLOYMENT)
    assert env_secret_ref(holmes, "K8S_REMEDIATION_MCP_TOKEN") == "my-auth"
    assert env_secret_ref(mcp, "MCP_AUTH_TOKEN") == "my-auth"
    for d in (holmes, mcp):
        annotations = pod_annotations(d)
        assert annotations[ROTATION_KEY] == "0"
        # lookup is empty under `helm template`, so the checksum is constant.
        assert annotations["checksum/k8s-remediation-auth-token"] == (
            hashlib.sha256(b"").hexdigest()
        )


def test_auth_disabled_renders_no_token_wiring():
    docs = render("mcpAddons.kubernetesRemediation.auth.enabled=false")
    assert bootstrap_docs(docs) == []
    for name in (HOLMES_DEPLOYMENT, MCP_DEPLOYMENT):
        assert ROTATION_KEY not in pod_annotations(find(docs, "Deployment", name))
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
# Bootstrap program, run against a stub kubectl
# ---------------------------------------------------------------------------

FAKE_KUBECTL = textwrap.dedent(
    """\
    #!{python}
    import base64, json, os, sys
    state_path = os.environ["FAKE_KUBECTL_STATE"]
    state = json.load(open(state_path))
    args = sys.argv[1:]
    state["calls"].append(args)

    def done(out="", err="", code=0):
        json.dump(state, open(state_path, "w"))
        sys.stdout.write(out)
        sys.stderr.write(err)
        sys.exit(code)

    def fail(key):
        if state.get(key):
            done(err=state[key], code=1)

    verb, kind = args[0], args[1]
    if verb == "get" and kind == "secret":
        fail("get_secret_error")
        s = state["secrets"].get(args[2])
        if s is None:
            done()
        done(json.dumps({{
            "metadata": {{"name": args[2], "annotations": s.get("annotations")}},
            "data": {{"token": base64.b64encode(s["token"].encode()).decode()}},
        }}))
    if verb == "get" and kind == "deployment":
        fail("get_deployment_error")
        d = state["deployments"].get(args[2])
        if d is None:
            done()
        env = [{{"name": "T", "valueFrom": {{"secretKeyRef": {{"name": r, "key": "token"}}}}}} for r in d["refs"]]
        env.append({{"name": "PLAIN", "value": "x"}})
        annotations = {{}} if d.get("rotation") is None else {{"{rotation_key}": d["rotation"]}}
        done(json.dumps({{"spec": {{"template": {{
            "metadata": {{"annotations": annotations}},
            "spec": {{"containers": [{{"env": env}}]}},
        }}}}}}))
    if verb == "delete" and kind == "secret":
        fail("delete_error")
        state["secrets"].pop(args[2], None)
        done("secret " + args[2] + " deleted")
    if verb == "create":
        manifest = json.loads(sys.stdin.read())
        state["created"].append(manifest)
        name = manifest["metadata"]["name"]
        if "AlreadyExists" in state.get("create_error", ""):
            state["secrets"][name] = {{"token": "winner", "annotations": {{}}}}
        fail("create_error")
        state["secrets"][name] = {{
            "token": manifest["stringData"]["token"],
            "annotations": manifest["metadata"]["annotations"],
        }}
        done("secret/" + name + " created")
    if verb == "rollout" and kind == "restart":
        fail("restart_error")
        done("deployment.apps/" + args[3] + " restarted")
    done(err="unexpected kubectl call: %r" % args, code=99)
    """
)


@pytest.fixture(scope="module")
def bootstrap_job() -> dict:
    return find(render(), "Job", BOOTSTRAP_NAME)


@pytest.fixture(scope="module")
def bootstrap_job_rotation_2() -> dict:
    return find(
        render(
            "mcpAddons.kubernetesRemediation.auth.rotation=2",
            "commonLabels.team=sre",
            "commonAnnotations.owner=platform",
        ),
        "Job",
        BOOTSTRAP_NAME,
    )


def run_bootstrap(job: dict, tmp_path: Path, **state) -> tuple:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(
        FAKE_KUBECTL.format(python=sys.executable, rotation_key=ROTATION_KEY)
    )
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    (bin_dir / "python3").symlink_to(sys.executable)

    state_file = tmp_path / "state.json"
    state.setdefault("secrets", {})
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


def secret(token: str = "old-token", rotation: Optional[str] = "0") -> dict:
    return {
        "token": token,
        "annotations": {} if rotation is None else {ROTATION_KEY: rotation},
    }


def both_reading(name: str = SECRET_NAME, rotation: Optional[str] = "0") -> dict:
    return {
        HOLMES_DEPLOYMENT: {"refs": ["robusta-ui-token", name], "rotation": rotation},
        MCP_DEPLOYMENT: {"refs": [name], "rotation": rotation},
    }


def restarted(result: dict) -> List[str]:
    return [c[3] for c in result["calls"] if c[:2] == ["rollout", "restart"]]


def mutations(result: dict) -> List[str]:
    return [c[0] for c in result["calls"] if c[0] in ("create", "delete", "rollout")]


def test_script_keeps_a_secret_at_the_current_rotation(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        secrets={SECRET_NAME: secret()},
        deployments=both_reading(),
    )
    assert proc.returncode == 0, proc.stderr
    assert "keeping its token" in proc.stdout
    assert mutations(result) == []


def test_script_creates_secret_on_fresh_install(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(bootstrap_job, tmp_path)
    assert proc.returncode == 0, proc.stderr
    [created] = result["created"]
    meta = created["metadata"]
    assert meta["name"] == SECRET_NAME
    assert meta["namespace"] == NAMESPACE
    assert meta["annotations"] == {ROTATION_KEY: "0"}
    assert meta["labels"] == {
        "app.kubernetes.io/component": "mcp-server-auth",
        "app.kubernetes.io/part-of": "holmes",
    }
    token = created["stringData"]["token"]
    assert len(token) >= 40
    assert token.replace("-", "").replace("_", "").isalnum()
    assert token not in proc.stdout + proc.stderr
    assert restarted(result) == []


def test_script_secret_carries_common_metadata(bootstrap_job_rotation_2, tmp_path):
    proc, result = run_bootstrap(bootstrap_job_rotation_2, tmp_path)
    assert proc.returncode == 0, proc.stderr
    meta = result["created"][0]["metadata"]
    assert meta["labels"]["team"] == "sre"
    assert meta["annotations"] == {"owner": "platform", ROTATION_KEY: "2"}


def test_script_generates_a_new_token_each_time(bootstrap_job, tmp_path):
    tokens = set()
    for i in range(2):
        run_dir = tmp_path / str(i)
        run_dir.mkdir()
        proc, result = run_bootstrap(bootstrap_job, run_dir)
        assert proc.returncode == 0, proc.stderr
        tokens.add(result["created"][0]["stringData"]["token"])
    assert len(tokens) == 2


@pytest.mark.parametrize("previous", ["0", "1", None])
def test_rotation_replaces_the_token_and_leaves_the_rollout_to_the_release(
    bootstrap_job_rotation_2, tmp_path, previous
):
    proc, result = run_bootstrap(
        bootstrap_job_rotation_2,
        tmp_path,
        secrets={SECRET_NAME: secret(rotation=previous)},
        deployments=both_reading(rotation=previous),
    )
    assert proc.returncode == 0, proc.stderr
    assert mutations(result) == ["delete", "create"]
    new = result["secrets"][SECRET_NAME]
    assert new["annotations"][ROTATION_KEY] == "2"
    assert new["token"] != "old-token"


def test_rotation_restarts_deployments_the_release_will_not_roll(
    bootstrap_job, tmp_path
):
    # e.g. an --atomic rollback to rotation 0 after the hook already rotated to 1
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        secrets={SECRET_NAME: secret(rotation="1")},
        deployments=both_reading(rotation="0"),
    )
    assert proc.returncode == 0, proc.stderr
    assert mutations(result) == ["delete", "rollout", "rollout", "create"]
    assert result["secrets"][SECRET_NAME]["annotations"][ROTATION_KEY] == "0"


def test_a_crash_mid_rotation_is_finished_by_the_next_run(bootstrap_job, tmp_path):
    # The previous run deleted the Secret and died before creating it.
    proc, result = run_bootstrap(
        bootstrap_job, tmp_path, deployments=both_reading(rotation="0")
    )
    assert proc.returncode == 0, proc.stderr
    assert mutations(result) == ["rollout", "rollout", "create"]


def test_a_deleted_secret_is_recreated_after_restarting_its_readers(
    bootstrap_job, tmp_path
):
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        deployments={
            HOLMES_DEPLOYMENT: {"refs": [SECRET_NAME], "rotation": "0"},
            MCP_DEPLOYMENT: {"refs": ["other"], "rotation": "0"},
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert mutations(result) == ["rollout", "create"]
    assert restarted(result) == [HOLMES_DEPLOYMENT]


def test_upgrade_from_the_rendered_secret_keeps_its_token(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        secrets={LEGACY_SECRET_NAME: {"token": "legacy-token"}},
        deployments=both_reading(LEGACY_SECRET_NAME, rotation=None),
    )
    assert proc.returncode == 0, proc.stderr
    assert "previous chart" in proc.stdout
    assert result["created"][0]["stringData"]["token"] == "legacy-token"
    assert "legacy-token" not in proc.stdout + proc.stderr
    assert restarted(result) == []


def test_rotation_never_reuses_the_legacy_token(bootstrap_job_rotation_2, tmp_path):
    proc, result = run_bootstrap(
        bootstrap_job_rotation_2,
        tmp_path,
        secrets={
            SECRET_NAME: secret(rotation="0"),
            LEGACY_SECRET_NAME: {"token": "legacy-token"},
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert result["secrets"][SECRET_NAME]["token"] not in ("legacy-token", "old-token")


def test_a_lingering_legacy_secret_is_not_reused_after_migration(
    bootstrap_job, tmp_path
):
    # The Secret was deleted (or a rotation crashed) and an unpruned legacy
    # Secret is still around: the readers must get a fresh token.
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        secrets={LEGACY_SECRET_NAME: {"token": "legacy-token"}},
        deployments=both_reading(),
    )
    assert proc.returncode == 0, proc.stderr
    assert result["secrets"][SECRET_NAME]["token"] != "legacy-token"
    assert restarted(result) == [HOLMES_DEPLOYMENT, MCP_DEPLOYMENT]


def test_script_tolerates_a_concurrent_create(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        create_error=f'Error from server (AlreadyExists): secrets "{SECRET_NAME}" already exists',
    )
    assert proc.returncode == 0, proc.stderr
    assert "created concurrently" in proc.stdout


@pytest.mark.parametrize(
    "error_key,message,state",
    [
        ("create_error", "Error from server (Forbidden): exceeded quota", {}),
        ("get_secret_error", 'Error from server (Forbidden): secrets "x"', {}),
        (
            "get_deployment_error",
            "Error from server (Forbidden): deployments.apps",
            {},
        ),
        (
            "restart_error",
            "Error from server (Conflict): deployments.apps",
            {"deployments": both_reading()},
        ),
        (
            "delete_error",
            "Error from server (Forbidden): secrets is forbidden",
            {"secrets": {SECRET_NAME: secret(rotation="9")}},
        ),
    ],
)
def test_script_fails_loudly_on_api_errors(
    bootstrap_job, tmp_path, error_key, message, state
):
    proc, result = run_bootstrap(
        bootstrap_job, tmp_path, **state, **{error_key: message}
    )
    assert proc.returncode != 0
    assert message in proc.stderr
    if error_key != "create_error":
        assert result["created"] == []

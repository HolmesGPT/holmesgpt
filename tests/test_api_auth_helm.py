"""Helm wiring of the Holmes default API key (values.yaml `apiAuth`).

The key Secret is created at deploy time by a hook Job and mounted as an
optional file, so clusterless renders (ArgoCD, `helm template`) are
deterministic and never roll the pods (the ROB-1365 failure mode), and a pod
that starts before the Secret exists still starts. The hook's inline Python
program is also run here against a stub `kubectl`.
"""

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import List, Optional

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
RELEASE = "rel"
NAMESPACE = "ns1"
SECRET_NAME = f"{RELEASE}-holmes-api-key"
BOOTSTRAP_NAME = f"{RELEASE}-holmes-api-key-bootstrap"
HOLMES_DEPLOYMENT = f"{RELEASE}-holmes"
OPERATOR_DEPLOYMENT = f"{RELEASE}-operator"
SERVER_MOUNT_DIR = "/etc/holmes/api-key"
CLIENT_MOUNT_DIR = "/var/run/secrets/holmes-api-key"
HOOKS = "pre-install,pre-upgrade,pre-rollback"

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm binary not available"
)


def render_raw(*sets: str, release: str = RELEASE) -> str:
    cmd = ["helm", "template", release, str(HELM_DIR), "-n", NAMESPACE]
    cmd += ["--set", "operator.enabled=true"]
    for s in sets:
        cmd += ["--set", s]
    return subprocess.check_output(cmd, text=True)


def render(*sets: str, release: str = RELEASE) -> List[dict]:
    return [d for d in yaml.safe_load_all(render_raw(*sets, release=release)) if d]


def find(docs: List[dict], kind: str, name: str) -> Optional[dict]:
    for d in docs:
        if d["kind"] == kind and d["metadata"]["name"] == name:
            return d
    return None


def bootstrap_docs(docs: List[dict]) -> List[dict]:
    return [d for d in docs if d["metadata"]["name"] == BOOTSTRAP_NAME]


def pod_spec(deployment: dict) -> dict:
    return deployment["spec"]["template"]["spec"]


def container(deployment: dict, name: Optional[str] = None) -> dict:
    containers = pod_spec(deployment)["containers"]
    if name is None:
        return containers[0]
    return next(c for c in containers if c["name"] == name)


def env_value(c: dict, name: str) -> Optional[str]:
    for e in c.get("env") or []:
        if e["name"] == name:
            return e.get("value")
    return None


def api_key_volume(deployment: dict) -> Optional[dict]:
    for v in pod_spec(deployment).get("volumes") or []:
        if v["name"] == "holmes-api-key":
            return v
    return None


def api_key_mount(c: dict) -> Optional[dict]:
    for m in c.get("volumeMounts") or []:
        if m["name"] == "holmes-api-key":
            return m
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_is_deterministic():
    """ROB-1365: a random value in the render rolls the pods on every ArgoCD sync."""
    assert render_raw() == render_raw()


def test_no_key_value_in_rendered_manifests():
    docs = render()
    assert find(docs, "Secret", SECRET_NAME) is None
    for d in docs:
        if d["kind"] == "Deployment":
            for key in (d["spec"]["template"]["metadata"].get("annotations") or {}):
                assert "api-key" not in key, f"{d['metadata']['name']}: {key}"


@pytest.mark.parametrize(
    "deployment,container_name,env_name,mount_dir,item",
    [
        # Holmes only verifies keys: it gets the hash, never the key, so a
        # tool reading files in the Holmes container cannot find the key.
        (HOLMES_DEPLOYMENT, "holmes", "HOLMES_DEFAULT_API_KEY_SHA256_FILE", SERVER_MOUNT_DIR, "token-sha256"),
        # Callers send the key; it sits under /var/run/secrets/, which the
        # remediation MCP's file reader refuses.
        (OPERATOR_DEPLOYMENT, "operator", "HOLMES_API_KEY_FILE", CLIENT_MOUNT_DIR, "token"),
    ],
)
def test_each_pod_mounts_only_the_item_it_needs_as_an_optional_file(
    deployment, container_name, env_name, mount_dir, item
):
    d = find(render(), "Deployment", deployment)
    c = container(d, container_name)
    assert env_value(c, env_name) == f"{mount_dir}/{item}"
    assert api_key_mount(c) == {
        "name": "holmes-api-key",
        "mountPath": mount_dir,
        "readOnly": True,
    }
    assert api_key_volume(d)["secret"] == {
        "secretName": SECRET_NAME,
        # A pod that starts before the hook created the Secret must still start.
        "optional": True,
        "items": [{"key": item, "path": item}],
    }


def test_holmes_pod_never_mounts_the_plaintext_key():
    holmes = find(render(), "Deployment", HOLMES_DEPLOYMENT)
    for v in pod_spec(holmes).get("volumes") or []:
        secret = v.get("secret") or {}
        if secret.get("secretName") == SECRET_NAME:
            assert [i["key"] for i in secret["items"]] == ["token-sha256"]


def test_holmes_api_key_is_never_set_by_the_chart():
    """The chart's key is separate, so it never overrides a user's HOLMES_API_KEY."""
    for d in render():
        if d["kind"] != "Deployment":
            continue
        for c in pod_spec(d)["containers"]:
            assert env_value(c, "HOLMES_API_KEY") is None


def test_user_holmes_api_key_is_kept_alongside_the_default_key():
    d = find(
        render(
            "additionalEnvVars[0].name=HOLMES_API_KEY",
            "additionalEnvVars[0].value=mine",
        ),
        "Deployment",
        HOLMES_DEPLOYMENT,
    )
    c = container(d, "holmes")
    assert env_value(c, "HOLMES_API_KEY") == "mine"
    assert env_value(c, "HOLMES_DEFAULT_API_KEY_SHA256_FILE") == (
        f"{SERVER_MOUNT_DIR}/token-sha256"
    )


def test_bootstrap_resources_are_ordered_hooks():
    docs = bootstrap_docs(render())
    assert sorted(d["kind"] for d in docs) == [
        "Job",
        "Role",
        "RoleBinding",
        "ServiceAccount",
    ]
    for d in docs:
        annotations = d["metadata"]["annotations"]
        assert annotations["helm.sh/hook"] == HOOKS
        assert annotations["helm.sh/hook-delete-policy"] == (
            "before-hook-creation,hook-succeeded"
        )
        expected_weight = "-5" if d["kind"] == "Job" else "-10"
        assert annotations["helm.sh/hook-weight"] == expected_weight


def test_bootstrap_role_is_least_privilege():
    role = find(render(), "Role", BOOTSTRAP_NAME)
    assert role["rules"] == [
        {"apiGroups": [""], "resources": ["secrets"], "verbs": ["create"]},
        {
            "apiGroups": [""],
            "resources": ["secrets"],
            "resourceNames": [SECRET_NAME],
            "verbs": ["get", "patch"],
        },
    ]


def test_bootstrap_job_spec():
    job = find(
        render(
            "imagePullSecrets[0].name=regcred",
            "nodeSelector.pool=system",
            "commonLabels.team=sre",
        ),
        "Job",
        BOOTSTRAP_NAME,
    )
    spec = job["spec"]["template"]["spec"]
    c = spec["containers"][0]
    assert c["image"] == "robustadev/holmes:0.0.0"
    assert c["command"] == ["python3", "-c"]
    assert "secrets.token_urlsafe" in c["args"][0]
    assert env_value(c, "SECRET_NAME") == SECRET_NAME
    assert json.loads(env_value(c, "SECRET_LABELS"))["team"] == "sre"
    assert spec["serviceAccountName"] == BOOTSTRAP_NAME
    assert spec["imagePullSecrets"] == [{"name": "regcred"}]
    assert spec["nodeSelector"] == {"pool": "system"}
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    assert c["securityContext"]["runAsNonRoot"] is True
    assert c["securityContext"]["runAsUser"] == 1000
    assert c["securityContext"]["allowPrivilegeEscalation"] is False
    assert c["resources"]["limits"]["cpu"]
    # Pod Security "restricted" requires a seccomp profile.
    assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    # A mesh sidecar would keep the pod running and the hook would never finish.
    annotations = job["spec"]["template"]["metadata"]["annotations"]
    assert annotations["sidecar.istio.io/inject"] == "false"
    assert annotations["linkerd.io/inject"] == "disabled"


def render_job(*sets: str, api_versions: Optional[str] = None) -> dict:
    cmd = ["helm", "template", RELEASE, str(HELM_DIR), "-n", NAMESPACE]
    for v in sets:
        cmd += ["--set", v]
    if api_versions:
        cmd += ["--api-versions", api_versions]
    docs = [d for d in yaml.safe_load_all(subprocess.check_output(cmd, text=True)) if d]
    return find(docs, "Job", BOOTSTRAP_NAME)


@pytest.mark.parametrize(
    "sets,api_versions",
    [(("openshift=true",), None), ((), "security.openshift.io/v1")],
)
def test_bootstrap_sets_no_fixed_uid_on_openshift(sets, api_versions):
    """OpenShift's restricted SCC rejects a fixed UID outside the namespace's
    range, which would fail the hook and with it every install and upgrade."""
    c = render_job(*sets, api_versions=api_versions)["spec"]["template"]["spec"]["containers"][0]
    assert "runAsUser" not in c["securityContext"]
    assert "runAsNonRoot" not in c["securityContext"]
    assert c["securityContext"]["readOnlyRootFilesystem"] is True


def test_bootstrap_pod_follows_the_holmes_pod_settings():
    """Admitted wherever the Holmes pod is: same podSecurityContext,
    containerSecurityContext, podAnnotations and priorityClassName; the
    sidecar opt-out and the read-only root filesystem cannot be overridden."""
    job = render_job(
        "podSecurityContext.runAsUser=2000",
        "podSecurityContext.runAsNonRoot=true",
        "containerSecurityContext.readOnlyRootFilesystem=false",
        "containerSecurityContext.runAsGroup=3000",
        "podAnnotations.sidecar\\.istio\\.io/inject=true",
        "podAnnotations.team=sre",
        "priorityClassName=high",
    )
    tpl = job["spec"]["template"]
    spec = tpl["spec"]
    c = spec["containers"][0]
    assert spec["securityContext"] == {
        "runAsUser": 2000,
        "runAsNonRoot": True,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    # The user's UID is used: no container-level UID overrides it.
    assert "runAsUser" not in c["securityContext"]
    assert c["securityContext"]["runAsGroup"] == 3000
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    assert spec["priorityClassName"] == "high"
    assert tpl["metadata"]["annotations"]["team"] == "sre"
    assert tpl["metadata"]["annotations"]["sidecar.istio.io/inject"] == "false"


def test_bootstrap_names_fit_kubernetes_limits_for_long_release_names():
    release = "r" * 53
    docs = render(release=release)
    job = next(d for d in docs if d["kind"] == "Job")
    hook_docs = [d for d in docs if d["metadata"]["name"] == job["metadata"]["name"]]
    assert len(hook_docs) == 4
    for d in hook_docs:
        assert len(d["metadata"]["name"]) <= 63, d["metadata"]["name"]
    holmes = find(docs, "Deployment", f"{release}-holmes")
    secret_name = api_key_volume(holmes)["secret"]["secretName"]
    assert len(secret_name) <= 63
    assert api_key_volume(holmes)["secret"]["secretName"] == env_value(
        job["spec"]["template"]["spec"]["containers"][0], "SECRET_NAME"
    )


def test_existing_secret_skips_bootstrap_and_is_wired_everywhere():
    docs = render("apiAuth.existingSecret=my-key")
    assert bootstrap_docs(docs) == []
    for name in (HOLMES_DEPLOYMENT, OPERATOR_DEPLOYMENT):
        d = find(docs, "Deployment", name)
        assert api_key_volume(d)["secret"]["secretName"] == "my-key"


def test_default_key_off_renders_no_key_wiring():
    docs = render("apiAuth.defaultKey=false")
    assert bootstrap_docs(docs) == []
    holmes = find(docs, "Deployment", HOLMES_DEPLOYMENT)
    assert api_key_volume(holmes) is None
    assert env_value(container(holmes, "holmes"), "HOLMES_DEFAULT_API_KEY_SHA256_FILE") is None
    operator = find(docs, "Deployment", OPERATOR_DEPLOYMENT)
    assert api_key_volume(operator) is None


# ---------------------------------------------------------------------------
# Bootstrap program, run against a stub kubectl
# ---------------------------------------------------------------------------

FAKE_KUBECTL = textwrap.dedent(
    """\
    #!{python}
    import base64, hashlib, json, os, sys
    sha = lambda k: hashlib.sha256(k.encode()).hexdigest()
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
        data = {{k: v[4:] if v.startswith("b64:") else base64.b64encode(v.encode()).decode() for k, v in s.items()}}
        rv = state.setdefault("rv", {{}}).setdefault(args[2], "1")
        if state.get("change_after_get"):
            # Another run changes the Secret between this run's get and its write.
            state["rv"][args[2]] = str(int(rv) + 1)
        done(json.dumps({{"metadata": {{"name": args[2], "resourceVersion": rv}}, "data": data}}))
    if verb == "patch" and kind == "secret":
        fail("patch_error")
        body = json.loads(args[args.index("-p") + 1])
        current = state.setdefault("rv", {{}}).setdefault(args[2], "1")
        if body["metadata"]["resourceVersion"] != current:
            done(err="Error from server (Conflict): the object has been modified", code=1)
        state["secrets"][args[2]].update(body["stringData"])
        state["rv"][args[2]] = str(int(current) + 1)
        done("secret/" + args[2] + " patched")
    if verb == "create":
        manifest = json.loads(sys.stdin.read())
        state["created"].append(manifest)
        name = manifest["metadata"]["name"]
        if "AlreadyExists" in state.get("create_error", ""):
            state["secrets"][name] = {{"token": "winner", "token-sha256": sha("winner")}}
        fail("create_error")
        state["secrets"][name] = dict(manifest["stringData"])
        done("secret/" + name + " created")
    done(err="unexpected kubectl call: %r" % args, code=99)
    """
)


@pytest.fixture(scope="module")
def bootstrap_job() -> dict:
    return find(render("commonAnnotations.owner=platform"), "Job", BOOTSTRAP_NAME)


def run_bootstrap(job: dict, tmp_path: Path, **state) -> tuple:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(FAKE_KUBECTL.format(python=sys.executable))
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    (bin_dir / "python3").symlink_to(sys.executable)

    state_file = tmp_path / "state.json"
    state.setdefault("secrets", {})
    state.update(calls=[], created=[])
    state_file.write_text(json.dumps(state))

    c = job["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    env.update(
        HOME=str(tmp_path),
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        FAKE_KUBECTL_STATE=str(state_file),
    )
    proc = subprocess.run(c["command"] + c["args"], env=env, capture_output=True, text=True)
    return proc, json.loads(state_file.read_text())


def mutations(result: dict) -> List[str]:
    return [c[0] for c in result["calls"] if c[0] in ("create", "delete", "patch", "replace")]


def sha256_hex(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def complete(key: str) -> dict:
    return {"token": key, "token-sha256": sha256_hex(key)}


def test_script_keeps_an_existing_key(bootstrap_job, tmp_path):
    """Every later upgrade and ArgoCD sync runs the hook: it must change nothing."""
    proc, result = run_bootstrap(bootstrap_job, tmp_path, secrets={SECRET_NAME: complete("kept")})
    assert proc.returncode == 0, proc.stderr
    assert mutations(result) == []
    assert result["secrets"][SECRET_NAME] == complete("kept")
    assert "kept" not in proc.stdout


@pytest.mark.parametrize("stale_hash", [None, "", "0" * 64])
def test_script_adds_a_missing_or_wrong_hash_and_keeps_the_key(bootstrap_job, tmp_path, stale_hash):
    """A Secret from before the hash existed (or with a wrong hash) gets the
    right token-sha256; the key itself, which callers already use, is kept."""
    secret = {"token": "existing-key-7f3a"}
    if stale_hash is not None:
        secret["token-sha256"] = stale_hash
    proc, result = run_bootstrap(bootstrap_job, tmp_path, secrets={SECRET_NAME: secret})
    assert proc.returncode == 0, proc.stderr
    assert mutations(result) == ["patch"]
    assert result["secrets"][SECRET_NAME] == complete("existing-key-7f3a")
    assert "existing-key-7f3a" not in proc.stdout + proc.stderr


def test_script_creates_the_secret_on_fresh_install(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(bootstrap_job, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert mutations(result) == ["create"]
    created = result["created"][0]
    token = created["stringData"]["token"]
    assert len(token) >= 40
    assert created["stringData"]["token-sha256"] == sha256_hex(token)
    assert token not in proc.stdout + proc.stderr
    meta = created["metadata"]
    assert meta["name"] == SECRET_NAME and meta["namespace"] == NAMESPACE
    # ArgoCD label tracking would prune a Secret carrying the instance label.
    assert "app.kubernetes.io/instance" not in meta["labels"]
    assert meta["labels"]["app.kubernetes.io/part-of"] == "holmes"
    assert meta["annotations"] == {"owner": "platform"}


def test_script_generates_a_new_key_each_time(bootstrap_job, tmp_path):
    _, first = run_bootstrap(bootstrap_job, tmp_path / "a")
    _, second = run_bootstrap(bootstrap_job, tmp_path / "b")
    assert first["secrets"][SECRET_NAME]["token"] != second["secrets"][SECRET_NAME]["token"]


@pytest.mark.parametrize("secret", [{}, {"token": "  \n"}])
def test_script_gives_a_keyless_secret_a_key_in_one_conditional_patch(bootstrap_job, tmp_path, secret):
    """No delete-then-create: a failed create could not leave the Secret gone."""
    proc, result = run_bootstrap(bootstrap_job, tmp_path, secrets={SECRET_NAME: secret})
    assert proc.returncode == 0, proc.stderr
    assert mutations(result) == ["patch"]
    new = result["secrets"][SECRET_NAME]
    assert new == complete(new["token"])
    assert new["token"].strip()


@pytest.mark.parametrize("secret", [{}, {"token": "existing-key-7f3a"}])
def test_script_does_not_overwrite_a_secret_changed_since_it_was_read(bootstrap_job, tmp_path, secret):
    """The patch carries the resourceVersion that was read: if another run
    changed the Secret in between, the write is refused (the Job retries)."""
    proc, result = run_bootstrap(
        bootstrap_job, tmp_path, secrets={SECRET_NAME: dict(secret)}, change_after_get=True
    )
    assert proc.returncode != 0
    assert "Conflict" in proc.stderr
    assert result["secrets"][SECRET_NAME] == secret


def test_script_repairs_a_hash_that_is_not_text(bootstrap_job, tmp_path):
    """Invalid bytes in token-sha256 are repaired, not a crash on every retry."""
    secret = {"token": "existing-key-7f3a", "token-sha256": "b64:" + base64.b64encode(b"\xff\xfe").decode()}
    proc, result = run_bootstrap(bootstrap_job, tmp_path, secrets={SECRET_NAME: secret})
    assert proc.returncode == 0, proc.stderr
    assert mutations(result) == ["patch"]
    assert result["secrets"][SECRET_NAME] == complete("existing-key-7f3a")


def test_script_tolerates_a_concurrent_create(bootstrap_job, tmp_path):
    proc, result = run_bootstrap(
        bootstrap_job,
        tmp_path,
        create_error=f'Error from server (AlreadyExists): secrets "{SECRET_NAME}" already exists',
    )
    assert proc.returncode == 0, proc.stderr
    assert result["secrets"][SECRET_NAME]["token"] == "winner"
    assert "created concurrently" in proc.stdout


@pytest.mark.parametrize(
    "error_key", ["get_secret_error", "create_error", "patch_error"]
)
def test_script_fails_loudly_on_api_errors(bootstrap_job, tmp_path, error_key):
    secrets_state = {"patch_error": {SECRET_NAME: {"token": "kept"}}}.get(error_key, {})
    proc, _ = run_bootstrap(
        bootstrap_job,
        tmp_path,
        secrets=secrets_state,
        **{error_key: "Error from server (Forbidden): denied"},
    )
    assert proc.returncode != 0
    assert "Forbidden" in proc.stderr


# ---------------------------------------------------------------------------
# The operator follows Holmes' tls setting
# ---------------------------------------------------------------------------


def operator_container(*sets: str) -> tuple:
    d = find(render(*sets), "Deployment", OPERATOR_DEPLOYMENT)
    return d, container(d, "operator")


def test_operator_uses_http_when_holmes_has_no_tls():
    d, c = operator_container()
    assert env_value(c, "HOLMES_API_URL") == f"http://{RELEASE}-holmes:80"
    assert env_value(c, "HOLMES_API_CA_FILE") is None
    assert [v["name"] for v in pod_spec(d)["volumes"]] == ["holmes-api-key"]


@pytest.mark.parametrize(
    "extra,ca_key",
    [((), "ca.crt"), (("operator.holmesApiCa.key=tls.crt",), "tls.crt")],
)
def test_operator_calls_holmes_over_https_and_trusts_its_ca(extra, ca_key):
    """With tls on, Holmes serves HTTPS on the same Service port: the operator
    must use https (plain http fails) and verify against Holmes' CA, so the
    key never travels unencrypted."""
    d, c = operator_container("tls.enabled=true", "tls.secretName=holmes-tls", *extra)
    assert env_value(c, "HOLMES_API_URL") == f"https://{RELEASE}-holmes:80"
    assert env_value(c, "HOLMES_API_CA_FILE") == f"/etc/holmes-operator/holmes-ca/{ca_key}"
    ca = next(v for v in pod_spec(d)["volumes"] if v["name"] == "holmes-api-ca")
    assert ca["secret"] == {
        "secretName": "holmes-tls",
        # Optional: without the key, the system trust store is used.
        "optional": True,
        "items": [{"key": ca_key, "path": ca_key}],
    }


def test_explicit_operator_url_and_ca_secret_are_kept():
    _, c = operator_container(
        "operator.holmesApiUrl=https://holmes.other:443",
        "operator.holmesApiCa.secretName=my-ca",
        "operator.holmesApiCa.key=bundle.pem",
    )
    assert env_value(c, "HOLMES_API_URL") == "https://holmes.other:443"
    assert env_value(c, "HOLMES_API_CA_FILE") == "/etc/holmes-operator/holmes-ca/bundle.pem"

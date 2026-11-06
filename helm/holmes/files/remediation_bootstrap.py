# Inlined into the auth-bootstrap.yaml hook Job via .Files.Get.
import base64, json, os, secrets, subprocess, sys

NS, NAME = os.environ["NAMESPACE"], os.environ["SECRET_NAME"]
ROTATION = os.environ["ROTATION"]
ROTATION_KEY = "robusta.dev/k8s-remediation-token-rotation"

def kubectl(*args, stdin=None, ok_if=None):
    p = subprocess.run(["kubectl", *args, "-n", NS], input=stdin, capture_output=True, text=True)
    if p.returncode != 0 and not (ok_if and ok_if in p.stderr):
        sys.exit(f"kubectl {args[0]} {args[1]} failed: {p.stderr.strip()}")
    return p

def get(kind, name):
    out = kubectl("get", kind, name, "--ignore-not-found", "-o", "json").stdout
    return json.loads(out) if out.strip() else None

def rotation_of(metadata):
    return (metadata.get("annotations") or {}).get(ROTATION_KEY)

secret = get("secret", NAME)
if secret is not None:
    if rotation_of(secret["metadata"]) == ROTATION and (secret.get("data") or {}).get("token"):
        print(f"Secret {NAME} is at rotation {ROTATION}; keeping its token")
        sys.exit(0)
    # Deleting first makes the steps below the only path that writes a
    # token.
    kubectl("delete", "secret", NAME, "--ignore-not-found")
    print(f"Rotating the token in Secret {NAME} to rotation {ROTATION}")

# Pods read the token at start, so every Deployment already reading
# this Secret holds one that is gone. Restart before creating: the new
# pods wait for the Secret instead of reading a stale one, a crash at
# any point leaves the Secret missing for the next run to redo, and a
# restart always gets a fresh ReplicaSet (a rollback alone may revive
# an old one with the old token).
readers = 0
for name in os.environ["DEPLOYMENTS"].split():
    deployment = get("deployment", name)
    if deployment is None:
        continue
    refs = {
        (env.get("valueFrom") or {}).get("secretKeyRef", {}).get("name")
        for c in deployment["spec"]["template"]["spec"]["containers"]
        for env in c.get("env") or []
    }
    if NAME in refs:
        readers += 1
        print(kubectl("rollout", "restart", "deployment", name).stdout.strip())

# Upgrading from the chart-rendered Secret (nothing reads this one yet)
# keeps its token, so the two sides never disagree mid-rollout, unless
# the same upgrade also asks for a rotation.
migrating = secret is None and readers == 0 and ROTATION == "0"
legacy = get("secret", os.environ["LEGACY_SECRET_NAME"]) if migrating else None
legacy_token = ((legacy or {}).get("data") or {}).get("token")
token = base64.b64decode(legacy_token).decode() if legacy_token else secrets.token_urlsafe(36)

# No app.kubernetes.io/instance label: ArgoCD label tracking would
# treat the Secret as part of the app and prune it.
labels = json.loads(os.environ["SECRET_LABELS"])
labels.update({"app.kubernetes.io/component": "mcp-server-auth", "app.kubernetes.io/part-of": "holmes"})
annotations = json.loads(os.environ["SECRET_ANNOTATIONS"])
annotations[ROTATION_KEY] = ROTATION
manifest = json.dumps({
    "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
    "metadata": {"name": NAME, "namespace": NS, "labels": labels, "annotations": annotations},
    "stringData": {"token": token},
})
created = kubectl("create", "-f", "-", stdin=manifest, ok_if="AlreadyExists").returncode == 0
source = "the previous chart's Secret" if legacy_token else "a new token"
print(f"Created Secret {NAME} from {source}" if created else f"Secret {NAME} was created concurrently; keeping it")

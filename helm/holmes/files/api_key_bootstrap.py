# Inlined into the api-key-bootstrap.yaml hook Job via .Files.Get.
# Creates the Holmes default API key Secret once. Every reader mounts it as a
# file and re-reads it, so this never restarts anything: a recreated Secret
# reaches the running pods through kubelet.
import json, os, secrets, subprocess, sys

NS, NAME = os.environ["NAMESPACE"], os.environ["SECRET_NAME"]

def kubectl(*args, stdin=None, ok_if=None):
    p = subprocess.run(["kubectl", *args, "-n", NS], input=stdin, capture_output=True, text=True)
    if p.returncode != 0 and not (ok_if and ok_if in p.stderr):
        sys.exit(f"kubectl {args[0]} {args[1]} failed: {p.stderr.strip()}")
    return p

out = kubectl("get", "secret", NAME, "--ignore-not-found", "-o", "json").stdout
secret = json.loads(out) if out.strip() else None
if secret is not None:
    if (secret.get("data") or {}).get("token"):
        print(f"Secret {NAME} exists; keeping its key")
        sys.exit(0)
    # A Secret without a key would leave every caller at 401: replace it.
    kubectl("delete", "secret", NAME, "--ignore-not-found")
    print(f"Secret {NAME} has no key; replacing it")

# No app.kubernetes.io/instance label: ArgoCD label tracking would treat the
# Secret as part of the app and prune it, since it is not in the render.
labels = json.loads(os.environ["SECRET_LABELS"])
labels.update({"app.kubernetes.io/component": "api-auth", "app.kubernetes.io/part-of": "holmes"})
manifest = json.dumps({
    "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
    "metadata": {"name": NAME, "namespace": NS, "labels": labels,
                 "annotations": json.loads(os.environ["SECRET_ANNOTATIONS"])},
    "stringData": {"token": secrets.token_urlsafe(36)},
})
created = kubectl("create", "-f", "-", stdin=manifest, ok_if="AlreadyExists").returncode == 0
print(f"Created Secret {NAME} with a new key" if created else f"Secret {NAME} was created concurrently; keeping it")

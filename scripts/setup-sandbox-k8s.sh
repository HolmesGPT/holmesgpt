#!/usr/bin/env bash
# Bring up a working k3s-in-Docker cluster for HolmesGPT k8s evals inside the
# Claude Code sandbox. Idempotent — safe to re-run within a session.
#
# Two non-obvious workarounds that this script bakes in:
#
# 1. The sandbox MITMs HTTPS to public registries with its own CA
#    (egress-gateway-ca-*.crt / swp-ca-*.crt are in /etc/ssl/certs). k3s's
#    inner containerd doesn't trust those, so image pulls fail with
#    "x509: certificate signed by unknown authority". We mount the host CA
#    bundle at both /etc/ssl/certs/ca-certificates.crt (Debian/Ubuntu path)
#    and /etc/pki/tls/certs/ca-bundle.crt (RHEL path) inside k3s, and write
#    a registries.yaml that points containerd at it explicitly.
#
# 2. The sandbox revokes CAP_SYS_RESOURCE, so writing a negative
#    oom_score_adj fails with EPERM. kubelet sets oomScoreAdj=-998 on
#    every pause container; without intervention runc's nsexec dies on
#    that write and surfaces the misleading "can't get final child's PID
#    from pipe: EOF". We replace /bin/runc inside the k3s container with
#    a wrapper that strips .process.oomScoreAdj from the OCI config
#    before invoking the real runc.
#
# After this script finishes you can run the regression suite with:
#
#   unset BRAINTRUST_API_KEY BRAINTRUST_SERVICE_TOKEN
#   export KUBECONFIG=/tmp/k3s-output/kubeconfig.yaml
#   export RUN_LIVE=true OPENAI_API_KEY=dummy
#   export MODEL=openrouter/openai/gpt-4.1-mini
#   export CLASSIFIER_MODEL=openrouter/openai/gpt-4.1
#   poetry run pytest tests/llm/test_ask_holmes.py \
#     -m "llm and regression and not network" \
#     --no-cov -n 4 -p no:cacheprovider
#
# `not network` skips 176_network_policy_blocking_traffic_no_skills, which
# is unrunnable here (the kernel restricts ipset and /sys/fs isn't a
# shared mount, so neither k3s's kube-router NP controller nor Calico's
# felix can enforce NetworkPolicies). All other regression evals pass.

set -euo pipefail

K3S_IMAGE="${K3S_IMAGE:-rancher/k3s:v1.31.4-k3s1}"
K3S_OUTPUT_DIR="${K3S_OUTPUT_DIR:-/tmp/k3s-output}"
HELM_VERSION="${HELM_VERSION:-v3.17.0}"
JQ_VERSION="${JQ_VERSION:-jq-1.7.1}"
KUBECONFIG="${K3S_OUTPUT_DIR}/kubeconfig.yaml"
export KUBECONFIG

log() { echo "[setup-k8s] $*"; }

# 1. Docker daemon
if ! docker info >/dev/null 2>&1; then
  log "Starting dockerd..."
  sudo dockerd >/tmp/dockerd.log 2>&1 &
  for _ in $(seq 1 20); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
  docker info >/dev/null 2>&1 || { log "dockerd failed to start"; tail /tmp/dockerd.log; exit 1; }
fi

# 2. kubectl
if ! command -v kubectl >/dev/null; then
  log "Installing kubectl..."
  KUBECTL_VER=$(curl -sfL https://dl.k8s.io/release/stable.txt)
  sudo curl -sfLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VER}/bin/linux/amd64/kubectl"
  sudo chmod +x /usr/local/bin/kubectl
fi

# 3. helm (the helm/core toolset's prereq check fails the eval otherwise)
if ! command -v helm >/dev/null; then
  log "Installing helm..."
  curl -sfL "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" | tar xz -C /tmp/
  sudo mv /tmp/linux-amd64/helm /usr/local/bin/helm
  sudo chmod +x /usr/local/bin/helm
  rm -rf /tmp/linux-amd64
fi

# 4. Static jq on host so we can copy it into the k3s container for the runc wrapper
if [ ! -x /tmp/jq ]; then
  log "Downloading static jq for the runc wrapper inside k3s..."
  curl -sfL -o /tmp/jq "https://github.com/jqlang/jq/releases/download/${JQ_VERSION}/jq-linux-amd64"
  chmod +x /tmp/jq
fi

mkdir -p "$K3S_OUTPUT_DIR"

# 5. Sandbox CA bundle: containerd inside k3s must trust the proxy CA
cp /etc/ssl/certs/ca-certificates.crt "$K3S_OUTPUT_DIR/ca-certs.crt"

# 6. registries.yaml — CA trust + mirror docker.io through mirror.gcr.io to
# dodge Docker Hub's anonymous-pull rate limit (100/6h per outbound IP). The
# sandbox shares an outbound IP across sessions, so concurrent test runs
# quickly hit 429 ToomanyRequests on python:3.9-slim / busybox / etc. Google
# runs mirror.gcr.io as a pull-through cache for Docker Hub's library/* and
# k8s ecosystem images with no rate limit; containerd will fall back to
# registry-1.docker.io for anything the mirror doesn't have.
cat > "$K3S_OUTPUT_DIR/registries.yaml" << 'EOF'
mirrors:
  "docker.io":
    endpoint:
      - "https://mirror.gcr.io"
      - "https://registry-1.docker.io"
configs:
  "mirror.gcr.io":
    tls:
      ca_file: /etc/ssl/certs/ca-certificates.crt
  "registry-1.docker.io":
    tls:
      ca_file: /etc/ssl/certs/ca-certificates.crt
  "registry.k8s.io":
    tls:
      ca_file: /etc/ssl/certs/ca-certificates.crt
  "quay.io":
    tls:
      ca_file: /etc/ssl/certs/ca-certificates.crt
  "ghcr.io":
    tls:
      ca_file: /etc/ssl/certs/ca-certificates.crt
EOF

# 7. (Re)start k3s container
if docker ps --format '{{.Names}}' | grep -q '^k3s-server$'; then
  log "k3s-server already running"
else
  log "Starting k3s-server container..."
  docker rm -f k3s-server >/dev/null 2>&1 || true
  for attempt in 1 2 3 4; do
    if docker image inspect "$K3S_IMAGE" >/dev/null 2>&1; then
      break
    fi
    docker pull "$K3S_IMAGE" && break
    log "pull attempt $attempt failed, retrying..."
    sleep $((attempt*2))
  done

  docker run -d --privileged --name k3s-server \
    --cgroupns=host \
    --security-opt seccomp=unconfined \
    --security-opt apparmor=unconfined \
    -p 6443:6443 \
    -e K3S_KUBECONFIG_OUTPUT=/output/kubeconfig.yaml \
    -e K3S_KUBECONFIG_MODE=666 \
    -v "$K3S_OUTPUT_DIR:/output" \
    -v "$K3S_OUTPUT_DIR/ca-certs.crt:/etc/ssl/certs/ca-certificates.crt:ro" \
    -v "$K3S_OUTPUT_DIR/ca-certs.crt:/etc/pki/tls/certs/ca-bundle.crt:ro" \
    -v "$K3S_OUTPUT_DIR/registries.yaml:/etc/rancher/k3s/registries.yaml:ro" \
    "$K3S_IMAGE" server \
      --disable=traefik --disable=metrics-server --disable=servicelb >/dev/null
fi

# 8. Install the oomScoreAdj-stripping runc wrapper inside the k3s container.
#    `docker rm` of k3s-server destroys this — re-running the script reinstalls.
if ! docker exec k3s-server test -f /bin/runc.real 2>/dev/null; then
  log "Installing oomScoreAdj-stripping runc wrapper inside k3s-server..."
  for _ in $(seq 1 30); do
    docker exec k3s-server test -f /bin/runc 2>/dev/null && break
    sleep 1
  done
  docker cp /tmp/jq k3s-server:/bin/jq
  docker exec k3s-server sh -c '
    mv /bin/runc /bin/runc.real
    cat > /bin/runc <<WRAPPER
#!/bin/sh
case "\$*" in
  *create*)
    BUNDLE=\$(echo "\$@" | grep -oE -- "--bundle [^ ]+" | awk "{print \\\$2}")
    if [ -n "\$BUNDLE" ] && [ -f "\$BUNDLE/config.json" ]; then
      /bin/jq "del(.process.oomScoreAdj)" "\$BUNDLE/config.json" > "\$BUNDLE/config.json.tmp" && mv "\$BUNDLE/config.json.tmp" "\$BUNDLE/config.json"
    fi
    ;;
esac
exec /bin/runc.real "\$@"
WRAPPER
    chmod +x /bin/runc
  '
fi

# 9. Wait for API server, point kubeconfig at localhost
log "Waiting for cluster API..."
for _ in $(seq 1 60); do
  [ -f "$KUBECONFIG" ] && kubectl get nodes 2>/dev/null | grep -q ' Ready ' && break
  sleep 2
done
sed -i 's|127.0.0.1|localhost|' "$KUBECONFIG"
kubectl wait --for=condition=Ready node --all --timeout=120s

# 10. Force-recreate kube-system pods that came up before the wrapper was active.
#     Otherwise they retry forever with the EOF error.
for _ in $(seq 1 30); do
  COUNT=$(kubectl get pods -n kube-system --no-headers 2>/dev/null | wc -l)
  [ "$COUNT" -ge 2 ] && break
  sleep 1
done
BAD=$(kubectl get pods -n kube-system --no-headers 2>/dev/null | awk '$3 != "Running" && $3 != "Completed" {print $1}')
if [ -n "$BAD" ]; then
  log "Recreating stuck system pods: $BAD"
  for name in $BAD; do
    kubectl delete pod -n kube-system "$name" --grace-period=0 --force >/dev/null 2>&1 || true
  done
fi
kubectl wait --for=condition=Ready pods --all -n kube-system --timeout=120s || true

log "Cluster ready."
kubectl get nodes
echo
echo "To run evals:"
echo "  export KUBECONFIG=$KUBECONFIG"
echo "  unset BRAINTRUST_API_KEY BRAINTRUST_SERVICE_TOKEN"
echo "  export RUN_LIVE=true OPENAI_API_KEY=dummy"
echo "  export MODEL=openrouter/openai/gpt-4.1-mini"
echo "  export CLASSIFIER_MODEL=openrouter/openai/gpt-4.1"
echo "  poetry run pytest tests/llm/test_ask_holmes.py \\"
echo "    -m 'llm and regression and not network' \\"
echo "    --no-cov -n 4 -p no:cacheprovider"

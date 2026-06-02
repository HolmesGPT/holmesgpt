#!/usr/bin/env bash
# Set up a working k3s-in-docker cluster + helm + kubectl for running HolmesGPT evals
# inside this sandbox environment. Idempotent: safe to re-run.
#
# Workaround: the sandbox blocks setting oom_score_adj below 0, so the kubelet's
# pause-container oomScoreAdj=-998 makes runc exit with
#   "nsexec: failed to update /proc/self/oom_score_adj: Permission denied"
# We replace /bin/runc inside k3s with a wrapper that strips .process.oomScoreAdj
# from each container's config.json before invoking the real runc.

set -euo pipefail

K3S_IMAGE="${K3S_IMAGE:-rancher/k3s:v1.31.4-k3s1}"
K3S_OUTPUT_DIR="${K3S_OUTPUT_DIR:-/tmp/k3s-output}"
export KUBECONFIG="${K3S_OUTPUT_DIR}/kubeconfig.yaml"

log() { echo "[setup-k8s] $*"; }

# 1. Docker daemon
if ! docker info >/dev/null 2>&1; then
  log "Starting dockerd..."
  dockerd >/tmp/dockerd.log 2>&1 &
  for _ in $(seq 1 20); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
  docker info >/dev/null 2>&1 || { log "dockerd failed to start"; tail /tmp/dockerd.log; exit 1; }
fi

# 2. kubectl + kind (kind only used for parity; not used for cluster here)
if ! command -v kubectl >/dev/null; then
  log "Installing kubectl..."
  KUBECTL_VER=$(curl -sL https://dl.k8s.io/release/stable.txt)
  curl -sLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VER}/bin/linux/amd64/kubectl"
  chmod +x /usr/local/bin/kubectl
fi

# 3. helm (required for many toolset prerequisite checks)
if ! command -v helm >/dev/null; then
  log "Installing helm..."
  curl -sL https://get.helm.sh/helm-v3.17.0-linux-amd64.tar.gz | tar xz -C /tmp/
  mv /tmp/linux-amd64/helm /usr/local/bin/helm
  chmod +x /usr/local/bin/helm
  rm -rf /tmp/linux-amd64
fi

# 4. jq on host so we can copy it into the k3s container for the runc wrapper
if [ ! -x /tmp/jq ]; then
  log "Downloading jq..."
  curl -sL -o /tmp/jq https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-amd64
  chmod +x /tmp/jq
fi

mkdir -p "$K3S_OUTPUT_DIR"
# 5. Sandbox CA bundle: containerd inside k3s must trust the proxy CA
cp /etc/ssl/certs/ca-certificates.crt "$K3S_OUTPUT_DIR/ca-certs.crt"

# 6. (Re)start k3s container
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
    "$K3S_IMAGE" server \
      --disable=traefik --disable=metrics-server --disable=servicelb >/dev/null
fi

# 7. Patch runc inside the container (idempotent)
if ! docker exec k3s-server test -f /bin/runc.real; then
  log "Installing oom_score_adj runc wrapper inside k3s-server..."
  docker cp /tmp/jq k3s-server:/bin/jq
  docker exec k3s-server sh -c '
    mv /bin/runc /bin/runc.real
    cat > /bin/runc <<EOF
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
EOF
    chmod +x /bin/runc
  '
fi

# 8. Wait for API server and node ready
log "Waiting for cluster to be ready..."
for _ in $(seq 1 60); do
  if [ -f "$KUBECONFIG" ] && kubectl get nodes 2>/dev/null | grep -q ' Ready '; then
    break
  fi
  sleep 2
done

kubectl wait --for=condition=Ready node --all --timeout=120s
# Wait for kube-system pods to be created by the bootstrap controllers
for _ in $(seq 1 30); do
  COUNT=$(kubectl get pods -n kube-system --no-headers 2>/dev/null | wc -l)
  [ "$COUNT" -ge 2 ] && break
  sleep 1
done
# Force-recreate any kube-system pods that came up before the runc wrapper was installed
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
echo "Run evals with:"
echo "  export KUBECONFIG=$KUBECONFIG"
echo "  export MODEL=openrouter/openai/gpt-4.1-mini"
echo "  export CLASSIFIER_MODEL=openrouter/openai/gpt-4.1"
echo "  poetry run pytest -k '01_how_many_pods' --no-cov"

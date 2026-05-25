#!/usr/bin/env bash
# Deploy two single-node Elasticsearch clusters into avi-test-cluster2 so
# multi-instance HolmesGPT config can be tested end-to-end.
#
# Usage:
#   bash scripts/deploy-multi-es-test.sh                  # deploy ES clusters only (port-forward path)
#   bash scripts/deploy-multi-es-test.sh --with-holmes    # also deploy HolmesGPT in-cluster
#   bash scripts/deploy-multi-es-test.sh --cleanup        # delete all namespaces
#
# Each ES cluster lives in its own namespace (holmes-es-a, holmes-es-b) to
# keep them fully isolated. Both expose HTTP on port 9200 in-cluster.
#
# `--with-holmes` additionally deploys HolmesGPT into a `holmes` namespace
# using the user-supplied image, pre-configured against both ES clusters
# via in-cluster service DNS — no port-forwarding required. The script
# prints the kubectl exec command to drive it.

set -euo pipefail

CONTEXT="${KUBECTL_CONTEXT:-avi-test-cluster2}"
NS_A="holmes-es-a"
NS_B="holmes-es-b"
NS_HOLMES="holmes"
ES_IMAGE="docker.elastic.co/elasticsearch/elasticsearch:8.15.3"
HOLMES_IMAGE="${HOLMES_IMAGE:-us-central1-docker.pkg.dev/genuine-flight-317411/devel/holmes:multi-elastic}"
# Distinct passwords let the sample config demonstrate the per-instance override.
PASSWORD_A="holmes-cluster-a-pass"
PASSWORD_B="holmes-cluster-b-pass"

WITH_HOLMES=false

cleanup() {
  echo "==> Deleting namespaces $NS_A, $NS_B, and $NS_HOLMES from context $CONTEXT"
  kubectl --context "$CONTEXT" delete namespace "$NS_A" --ignore-not-found
  kubectl --context "$CONTEXT" delete namespace "$NS_B" --ignore-not-found
  kubectl --context "$CONTEXT" delete namespace "$NS_HOLMES" --ignore-not-found
  echo "✅ Cleanup complete"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cleanup)
      cleanup
      exit 0
      ;;
    --with-holmes)
      WITH_HOLMES=true
      shift
      ;;
    *)
      echo "Unknown flag: $1" >&2
      echo "Usage: $0 [--with-holmes] [--cleanup]" >&2
      exit 1
      ;;
  esac
done

echo "==> Using kubectl context: $CONTEXT"
kubectl config use-context "$CONTEXT"

deploy_cluster() {
  local ns="$1"
  local password="$2"

  echo "==> Deploying Elasticsearch into namespace $ns"
  kubectl --context "$CONTEXT" create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -

  kubectl --context "$CONTEXT" -n "$ns" create secret generic elastic-credentials \
    --from-literal=ELASTIC_PASSWORD="$password" \
    --dry-run=client -o yaml | kubectl apply -f -

  kubectl --context "$CONTEXT" -n "$ns" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: elasticsearch
  labels:
    app: elasticsearch
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: elasticsearch
  template:
    metadata:
      labels:
        app: elasticsearch
    spec:
      # ES recommends max_map_count=262144; sysctl is namespaced on most CNIs
      # but we keep the requirement low via single-node mode and skip init
      # containers — works on AKS/EKS test clusters without privileged setup.
      containers:
        - name: elasticsearch
          image: ${ES_IMAGE}
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 9200
          env:
            - name: discovery.type
              value: single-node
            - name: xpack.security.enabled
              value: "true"
            - name: xpack.security.http.ssl.enabled
              value: "false"
            - name: xpack.security.transport.ssl.enabled
              value: "false"
            - name: ES_JAVA_OPTS
              value: "-Xms512m -Xmx512m"
            - name: ELASTIC_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: elastic-credentials
                  key: ELASTIC_PASSWORD
          resources:
            requests:
              memory: "768Mi"
              cpu: "200m"
            limits:
              memory: "1.5Gi"
              cpu: "1000m"
          readinessProbe:
            httpGet:
              path: /_cluster/health
              port: 9200
              httpHeaders:
                - name: Authorization
                  value: Basic $(printf 'elastic:%s' "$password" | base64)
            initialDelaySeconds: 20
            periodSeconds: 5
            failureThreshold: 30
---
apiVersion: v1
kind: Service
metadata:
  name: elasticsearch
spec:
  selector:
    app: elasticsearch
  ports:
    - name: http
      port: 9200
      targetPort: 9200
  type: ClusterIP
EOF
}

wait_for_cluster() {
  local ns="$1"
  echo "==> Waiting for elasticsearch pod in $ns to become ready"
  local ready=false
  for _ in $(seq 1 60); do
    if kubectl --context "$CONTEXT" -n "$ns" wait --for=condition=ready pod \
        -l app=elasticsearch --timeout=5s >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 2
  done
  if [[ "$ready" != "true" ]]; then
    echo "❌ Pod in $ns never became ready"
    kubectl --context "$CONTEXT" -n "$ns" get pods
    kubectl --context "$CONTEXT" -n "$ns" describe pod -l app=elasticsearch | tail -40
    kubectl --context "$CONTEXT" -n "$ns" logs -l app=elasticsearch --tail=40 || true
    exit 1
  fi
}

probe_cluster() {
  local ns="$1"
  local password="$2"
  echo "==> Probing /_cluster/health in $ns"
  local ok=false
  for _ in $(seq 1 30); do
    if kubectl --context "$CONTEXT" -n "$ns" exec deploy/elasticsearch -- \
        curl -fsS -u "elastic:${password}" http://localhost:9200/_cluster/health \
        >/dev/null 2>&1; then
      ok=true
      break
    fi
    sleep 2
  done
  if [[ "$ok" != "true" ]]; then
    echo "❌ Health check failed in $ns"
    kubectl --context "$CONTEXT" -n "$ns" logs deploy/elasticsearch --tail=40 || true
    exit 1
  fi
  echo "✅ $ns elasticsearch is healthy"
}

deploy_holmes() {
  echo "==> Deploying HolmesGPT into namespace $NS_HOLMES with image $HOLMES_IMAGE"
  kubectl --context "$CONTEXT" create namespace "$NS_HOLMES" --dry-run=client -o yaml | kubectl apply -f -

  # Cross-namespace ES URLs (in-cluster service DNS) and the same distinct
  # passwords used by the ES deployments above, so Holmes can hit both
  # clusters without any port-forwarding from the user's laptop.
  kubectl --context "$CONTEXT" -n "$NS_HOLMES" create configmap holmes-config \
    --from-literal=config.yaml="$(cat <<YAML
toolsets:
  elasticsearch/cluster:
    enabled: true
    config:
      username: elastic
      password: ${PASSWORD_A}
      verify_ssl: false
      timeout_seconds: 15
      instances:
        - name: cluster-a
          api_url: http://elasticsearch.${NS_A}.svc:9200
        - name: cluster-b
          api_url: http://elasticsearch.${NS_B}.svc:9200
          username: elastic
          password: ${PASSWORD_B}
  elasticsearch/data:
    enabled: true
    config:
      username: elastic
      password: ${PASSWORD_A}
      verify_ssl: false
      instances:
        - name: cluster-a
          api_url: http://elasticsearch.${NS_A}.svc:9200
        - name: cluster-b
          api_url: http://elasticsearch.${NS_B}.svc:9200
          username: elastic
          password: ${PASSWORD_B}
YAML
)" --dry-run=client -o yaml | kubectl apply -f -

  kubectl --context "$CONTEXT" -n "$NS_HOLMES" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: holmes
  labels:
    app: holmes
spec:
  replicas: 1
  selector:
    matchLabels:
      app: holmes
  template:
    metadata:
      labels:
        app: holmes
    spec:
      containers:
        - name: holmes
          image: ${HOLMES_IMAGE}
          imagePullPolicy: Always
          # Holmes reads ~/.holmes/config.yaml by default; mount the configmap there.
          command: ["sleep", "infinity"]
          volumeMounts:
            - name: holmes-config
              mountPath: /root/.holmes
          resources:
            requests:
              memory: "512Mi"
              cpu: "200m"
            limits:
              memory: "2Gi"
              cpu: "2000m"
      volumes:
        - name: holmes-config
          configMap:
            name: holmes-config
            items:
              - key: config.yaml
                path: config.yaml
EOF

  echo "==> Waiting for Holmes pod"
  local ready=false
  for _ in $(seq 1 30); do
    if kubectl --context "$CONTEXT" -n "$NS_HOLMES" wait --for=condition=ready pod \
        -l app=holmes --timeout=5s >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 2
  done
  if [[ "$ready" != "true" ]]; then
    echo "❌ Holmes pod never became ready"
    kubectl --context "$CONTEXT" -n "$NS_HOLMES" describe pod -l app=holmes | tail -40
    exit 1
  fi
  echo "✅ Holmes is running in $NS_HOLMES"
}

deploy_cluster "$NS_A" "$PASSWORD_A"
deploy_cluster "$NS_B" "$PASSWORD_B"

wait_for_cluster "$NS_A"
wait_for_cluster "$NS_B"

probe_cluster "$NS_A" "$PASSWORD_A"
probe_cluster "$NS_B" "$PASSWORD_B"

if [[ "$WITH_HOLMES" == "true" ]]; then
  deploy_holmes
  cat <<EOM

==========================================================================
✅ HolmesGPT is running in-cluster pointed at both ES instances.

Drive it via kubectl exec — no port-forwarding needed:

  kubectl --context $CONTEXT -n $NS_HOLMES exec -it deploy/holmes -- \\
    holmes ask "List the configured Elasticsearch instances and report each cluster health"

Target a specific instance:

  kubectl --context $CONTEXT -n $NS_HOLMES exec -it deploy/holmes -- \\
    holmes ask "Get the cluster health for cluster-b"

Show the running config:

  kubectl --context $CONTEXT -n $NS_HOLMES exec deploy/holmes -- cat /root/.holmes/config.yaml

Image: $HOLMES_IMAGE
(Override with HOLMES_IMAGE=... when re-running this script.)

When you're done:

  bash scripts/deploy-multi-es-test.sh --cleanup
==========================================================================
EOM
else
  cat <<EOM

==========================================================================
✅ Both Elasticsearch clusters are running on context '$CONTEXT'.

Two ways to test multi-instance HolmesGPT support:

Option A — local Holmes via port-forward:
1. Open two terminals and keep these port-forwards running:

     kubectl --context $CONTEXT -n $NS_A port-forward svc/elasticsearch 9200:9200
     kubectl --context $CONTEXT -n $NS_B port-forward svc/elasticsearch 9201:9200

2. Run Holmes against the sample config:

     holmes --config scripts/multi-es-test-config.yaml ask \\
       "List the configured Elasticsearch instances and report each cluster health"

   (Or copy the file to ~/.holmes/config.yaml.)

Option B — in-cluster Holmes (no port-forwarding):

     bash scripts/deploy-multi-es-test.sh --with-holmes

When you're done:

     bash scripts/deploy-multi-es-test.sh --cleanup
==========================================================================
EOM
fi

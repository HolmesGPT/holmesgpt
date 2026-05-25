#!/usr/bin/env bash
# Deploy two single-node Elasticsearch clusters into avi-test-cluster2 so
# multi-instance HolmesGPT config can be tested end-to-end.
#
# Usage:
#   bash scripts/deploy-multi-es-test.sh             # deploy
#   bash scripts/deploy-multi-es-test.sh --cleanup   # delete namespaces
#
# Each cluster lives in its own namespace (holmes-es-a, holmes-es-b) to keep
# them fully isolated. Both expose HTTP on port 9200 in-cluster; the script
# prints the kubectl port-forward commands at the end so HolmesGPT (running
# locally) can talk to them on localhost:9200 / localhost:9201.

set -euo pipefail

CONTEXT="${KUBECTL_CONTEXT:-avi-test-cluster2}"
NS_A="holmes-es-a"
NS_B="holmes-es-b"
ES_IMAGE="docker.elastic.co/elasticsearch/elasticsearch:8.15.3"
# Distinct passwords let the sample config demonstrate the per-instance override.
PASSWORD_A="holmes-cluster-a-pass"
PASSWORD_B="holmes-cluster-b-pass"

cleanup() {
  echo "==> Deleting namespaces $NS_A and $NS_B from context $CONTEXT"
  kubectl --context "$CONTEXT" delete namespace "$NS_A" --ignore-not-found
  kubectl --context "$CONTEXT" delete namespace "$NS_B" --ignore-not-found
  echo "✅ Cleanup complete"
}

if [[ "${1:-}" == "--cleanup" ]]; then
  cleanup
  exit 0
fi

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

deploy_cluster "$NS_A" "$PASSWORD_A"
deploy_cluster "$NS_B" "$PASSWORD_B"

wait_for_cluster "$NS_A"
wait_for_cluster "$NS_B"

probe_cluster "$NS_A" "$PASSWORD_A"
probe_cluster "$NS_B" "$PASSWORD_B"

cat <<EOM

==========================================================================
✅ Both Elasticsearch clusters are running on context '$CONTEXT'.

Next steps to test multi-instance HolmesGPT support:

1. Open two terminals and start port-forwards (they need to keep running):

     kubectl --context $CONTEXT -n $NS_A port-forward svc/elasticsearch 9200:9200
     kubectl --context $CONTEXT -n $NS_B port-forward svc/elasticsearch 9201:9200

2. Point HolmesGPT at the sample config in this repo:

     holmes --config scripts/multi-es-test-config.yaml ask \\
       "List the configured Elasticsearch instances and report each cluster health"

   (Or copy the file to ~/.holmes/config.yaml.)

3. Try targeting a specific instance:

     holmes --config scripts/multi-es-test-config.yaml ask \\
       "Get the cluster health for cluster-b"

When you're done:

     bash scripts/deploy-multi-es-test.sh --cleanup
==========================================================================
EOM

#!/bin/bash
set -e

NAMESPACE="ask-holmes-namespace-163"

echo "Creating namespace $NAMESPACE..."
kubectl create namespace $NAMESPACE || true

echo "Deploying 8 pods in the namespace..."

# Pod 1: web-frontend (mentioned in conversation history)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: web-frontend
  namespace: $NAMESPACE
  labels:
    app: frontend
spec:
  containers:
  - name: nginx
    image: nginx:alpine
    resources:
      requests:
        cpu: "50m"
        memory: "64Mi"
      limits:
        cpu: "100m"
        memory: "128Mi"
EOF

# Pod 2: auth-service (mentioned in conversation history)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: auth-service
  namespace: $NAMESPACE
  labels:
    app: auth
spec:
  containers:
  - name: service
    image: busybox:1.36
    command: ["sh", "-c", "while true; do echo 'Processing authentication...'; sleep 10; done"]
    resources:
      requests:
        cpu: "50m"
        memory: "64Mi"
      limits:
        cpu: "100m"
        memory: "128Mi"
EOF

# Pod 3: database-replica (mentioned in conversation history)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: database-replica
  namespace: $NAMESPACE
  labels:
    app: database
spec:
  containers:
  - name: postgres
    image: busybox:1.36
    command: ["sh", "-c", "while true; do echo 'Replicating data...'; sleep 15; done"]
    resources:
      requests:
        cpu: "100m"
        memory: "128Mi"
      limits:
        cpu: "200m"
        memory: "256Mi"
EOF

# Pod 4: cache-server (NOT mentioned in conversation history)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: cache-server
  namespace: $NAMESPACE
  labels:
    app: cache
spec:
  containers:
  - name: redis
    image: busybox:1.36
    command: ["sh", "-c", "while true; do echo 'Caching data...'; sleep 5; done"]
    resources:
      requests:
        cpu: "50m"
        memory: "64Mi"
      limits:
        cpu: "100m"
        memory: "128Mi"
EOF

# Pod 5: metrics-collector (NOT mentioned in conversation history)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: metrics-collector
  namespace: $NAMESPACE
  labels:
    app: metrics
spec:
  containers:
  - name: collector
    image: busybox:1.36
    command: ["sh", "-c", "while true; do echo 'Collecting metrics...'; sleep 8; done"]
    resources:
      requests:
        cpu: "25m"
        memory: "64Mi"
      limits:
        cpu: "50m"
        memory: "128Mi"
EOF

# Pod 6: log-aggregator (NOT mentioned in conversation history)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: log-aggregator
  namespace: $NAMESPACE
  labels:
    app: logging
spec:
  containers:
  - name: aggregator
    image: busybox:1.36
    command: ["sh", "-c", "while true; do echo 'Aggregating logs...'; sleep 12; done"]
    resources:
      requests:
        cpu: "25m"
        memory: "64Mi"
      limits:
        cpu: "50m"
        memory: "128Mi"
EOF

# Pod 7: queue-worker (NOT mentioned in conversation history)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: queue-worker
  namespace: $NAMESPACE
  labels:
    app: worker
spec:
  containers:
  - name: worker
    image: busybox:1.36
    command: ["sh", "-c", "while true; do echo 'Processing queue...'; sleep 6; done"]
    resources:
      requests:
        cpu: "50m"
        memory: "64Mi"
      limits:
        cpu: "100m"
        memory: "128Mi"
EOF

# Pod 8: api-gateway (NOT mentioned in conversation history)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: api-gateway
  namespace: $NAMESPACE
  labels:
    app: gateway
spec:
  containers:
  - name: gateway
    image: busybox:1.36
    command: ["sh", "-c", "while true; do echo 'Handling API requests...'; sleep 4; done"]
    resources:
      requests:
        cpu: "75m"
        memory: "128Mi"
      limits:
        cpu: "150m"
        memory: "256Mi"
EOF

echo "Waiting for all pods to be running..."
POD_READY=false
for attempt in {1..60}; do
  RUNNING_COUNT=$(kubectl get pods -n $NAMESPACE --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l)
  echo "⏳ Attempt $attempt/60: $RUNNING_COUNT/8 pods running..."

  if [ "$RUNNING_COUNT" -eq 8 ]; then
    echo "✅ All 8 pods are running!"
    POD_READY=true
    break
  fi
  sleep 5
done

if [ "$POD_READY" = false ]; then
  echo "❌ Not all pods became ready after 300 seconds"
  kubectl get pods -n $NAMESPACE
  exit 1
fi

echo "✅ Setup complete! All 8 pods are running in namespace $NAMESPACE."
kubectl get pods -n $NAMESPACE

#!/bin/bash
set -e

NAMESPACE="app-161"

echo "Creating namespace $NAMESPACE..."
kubectl create namespace $NAMESPACE || true

echo "Deploying 10 pods with varying CPU usage patterns..."

# Deploy 10 pods with different CPU usage levels (from lowest to highest)
# Pod 1: Extremely low CPU (5m) - This should be clearly the lowest
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: analytics-worker
  namespace: $NAMESPACE
  labels:
    app: analytics
spec:
  containers:
  - name: worker
    image: busybox:1.36
    command: ["sh", "-c"]
    args:
      - |
        echo "Starting analytics worker with minimal CPU..."
        while true; do
          sleep 30
        done
    resources:
      requests:
        cpu: "5m"
        memory: "64Mi"
      limits:
        cpu: "20m"
        memory: "128Mi"
EOF

# Pod 2: Low CPU (25m)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: notification-service
  namespace: $NAMESPACE
  labels:
    app: notifications
spec:
  containers:
  - name: service
    image: busybox:1.36
    command: ["sh", "-c"]
    args:
      - |
        echo "Starting notification service..."
        while true; do
          sleep 5
          echo "Processing notifications..."
        done
    resources:
      requests:
        cpu: "25m"
        memory: "64Mi"
      limits:
        cpu: "100m"
        memory: "128Mi"
EOF

# Pod 3: Medium-low CPU (50m)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: cache-manager
  namespace: $NAMESPACE
  labels:
    app: cache
spec:
  containers:
  - name: manager
    image: busybox:1.36
    command: ["sh", "-c"]
    args:
      - |
        echo "Starting cache manager..."
        while true; do
          for i in \$(seq 1 5); do
            echo "Cache operation \$i"
          done
          sleep 3
        done
    resources:
      requests:
        cpu: "50m"
        memory: "128Mi"
      limits:
        cpu: "150m"
        memory: "256Mi"
EOF

# Pod 4: Medium CPU (75m)
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
    command: ["sh", "-c"]
    args:
      - |
        echo "Starting log aggregator..."
        while true; do
          for i in \$(seq 1 10); do
            echo "Processing log batch \$i"
          done
          sleep 2
        done
    resources:
      requests:
        cpu: "75m"
        memory: "128Mi"
      limits:
        cpu: "200m"
        memory: "256Mi"
EOF

# Pod 5: Medium-high CPU (100m)
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
    command: ["sh", "-c"]
    args:
      - |
        echo "Starting metrics collector..."
        while true; do
          for i in \$(seq 1 20); do
            echo "Collecting metric \$i"
          done
          sleep 1
        done
    resources:
      requests:
        cpu: "100m"
        memory: "128Mi"
      limits:
        cpu: "250m"
        memory: "256Mi"
EOF

# Pod 6: High CPU (150m)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: data-processor
  namespace: $NAMESPACE
  labels:
    app: dataproc
spec:
  containers:
  - name: processor
    image: busybox:1.36
    command: ["sh", "-c"]
    args:
      - |
        echo "Starting data processor..."
        while true; do
          for i in \$(seq 1 30); do
            echo "Processing data chunk \$i"
          done
          sleep 1
        done
    resources:
      requests:
        cpu: "150m"
        memory: "256Mi"
      limits:
        cpu: "300m"
        memory: "512Mi"
EOF

# Pod 7: Higher CPU (200m)
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
    command: ["sh", "-c"]
    args:
      - |
        echo "Starting API gateway..."
        while true; do
          for i in \$(seq 1 50); do
            echo "Handling API request \$i"
          done
          sleep 1
        done
    resources:
      requests:
        cpu: "200m"
        memory: "256Mi"
      limits:
        cpu: "400m"
        memory: "512Mi"
EOF

# Pod 8: Very high CPU (300m)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: search-indexer
  namespace: $NAMESPACE
  labels:
    app: search
spec:
  containers:
  - name: indexer
    image: busybox:1.36
    command: ["sh", "-c"]
    args:
      - |
        echo "Starting search indexer..."
        while true; do
          for i in \$(seq 1 100); do
            echo "Indexing document \$i"
          done
        done
    resources:
      requests:
        cpu: "300m"
        memory: "512Mi"
      limits:
        cpu: "500m"
        memory: "1Gi"
EOF

# Pod 9: Extremely high CPU (400m)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ml-training
  namespace: $NAMESPACE
  labels:
    app: mltraining
spec:
  containers:
  - name: trainer
    image: busybox:1.36
    command: ["sh", "-c"]
    args:
      - |
        echo "Starting ML training job..."
        while true; do
          for i in \$(seq 1 200); do
            echo "Training iteration \$i"
          done
        done
    resources:
      requests:
        cpu: "400m"
        memory: "1Gi"
      limits:
        cpu: "600m"
        memory: "2Gi"
EOF

# Pod 10: Maximum CPU (500m)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: video-encoder
  namespace: $NAMESPACE
  labels:
    app: encoder
spec:
  containers:
  - name: encoder
    image: busybox:1.36
    command: ["sh", "-c"]
    args:
      - |
        echo "Starting video encoder..."
        while true; do
          for i in \$(seq 1 500); do
            echo "Encoding frame \$i"
          done
        done
    resources:
      requests:
        cpu: "500m"
        memory: "1Gi"
      limits:
        cpu: "800m"
        memory: "2Gi"
EOF

echo "Waiting for all pods to be running..."
POD_READY=false
for attempt in {1..60}; do
  RUNNING_COUNT=$(kubectl get pods -n $NAMESPACE --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l)
  echo "⏳ Attempt $attempt/60: $RUNNING_COUNT/10 pods running..."

  if [ "$RUNNING_COUNT" -eq 10 ]; then
    echo "✅ All 10 pods are running!"
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

echo "Waiting additional 30 seconds for CPU metrics to stabilize..."
sleep 30

echo "✅ Setup complete! All pods are running with varying CPU usage."
kubectl get pods -n $NAMESPACE

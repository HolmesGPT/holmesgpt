#!/usr/bin/env bash

set -euo pipefail

NS="${NS:-app-161}"
TARGET_REPLICAS=3  # We expect at least 3 replicas after scaling
MAX_WAIT=120  # Maximum 2 minutes to wait for scaling

echo ">>> Waiting for HPA to scale up the deployment..."

start_time=$(date +%s)
while true; do
  current_replicas=$(kubectl get deployment bidder -n "${NS}" -o jsonpath='{.status.replicas}')

  echo "Current replicas: ${current_replicas}"

  if [ "${current_replicas}" -ge "${TARGET_REPLICAS}" ]; then
    echo "✓ Deployment has scaled to ${current_replicas} replicas"
    break
  fi

  elapsed=$(($(date +%s) - start_time))
  if [ "${elapsed}" -gt "${MAX_WAIT}" ]; then
    echo "⚠️ WARNING: Deployment hasn't scaled to ${TARGET_REPLICAS} replicas after ${MAX_WAIT} seconds"
    echo "Current replicas: ${current_replicas}"
    echo "Continuing anyway..."
    break
  fi

  echo "Waiting for scaling... (${elapsed}s elapsed)"
  sleep 5
done

# Show HPA status
echo ""
echo ">>> HPA Status:"
kubectl get hpa bidder-hpa -n "${NS}"
kubectl describe hpa bidder-hpa -n "${NS}" | grep -A 5 "Metrics:"

echo ""
echo ">>> Deployment Status:"
kubectl get deployment bidder -n "${NS}" -o wide

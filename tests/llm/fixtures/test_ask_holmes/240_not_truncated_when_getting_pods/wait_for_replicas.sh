#!/bin/bash

# Usage:
# ./check_pods.sh <deployment-name> <target-replicas> <namespace> [timeout-seconds]
#
# Example:
# ./check_pods.sh massive-pod-deployment-with-very-long-name 2000 default 600

set -e

DEPLOYMENT_NAME="$1"
TARGET_REPLICAS="$2"
NAMESPACE="$3"
TIMEOUT="${4:-300}"   # default 5 minutes

if [ -z "$DEPLOYMENT_NAME" ] || [ -z "$TARGET_REPLICAS" ] || [ -z "$NAMESPACE" ]; then
  echo "Usage: $0 <deployment-name> <target-replicas> <namespace> [timeout-seconds]"
  exit 1
fi

echo "🔎 Checking for $TARGET_REPLICAS pods in namespace '$NAMESPACE' for deployment '$DEPLOYMENT_NAME' with timeout ${TIMEOUT}s..."

START_TIME=$(date +%s)

while true; do
  POD_COUNT=$(kubectl get pods -n "$NAMESPACE" --no-headers | grep "$DEPLOYMENT_NAME" | wc -l)

  echo "Currently detected $POD_COUNT pods (target $TARGET_REPLICAS)..."

  if [ "$POD_COUNT" -eq "$TARGET_REPLICAS" ]; then
    echo "✅ Found the expected $TARGET_REPLICAS pods."
    exit 0
  fi

  CURRENT_TIME=$(date +%s)
  ELAPSED=$((CURRENT_TIME - START_TIME))

  if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
    echo "❌ Timed out after ${TIMEOUT}s without reaching $TARGET_REPLICAS pods."
    exit 1
  fi

  sleep 5
done

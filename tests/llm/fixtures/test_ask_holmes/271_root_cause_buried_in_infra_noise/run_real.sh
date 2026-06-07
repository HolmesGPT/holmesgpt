#!/bin/bash
# Runs the 3 real index-to-es task pods to completion, confirms they actually
# ran (genuine Scheduled/Pulled/Started events), then deletes them to mimic the
# KubernetesPodOperator's default on-finish cleanup. The lifecycle events
# survive the pod deletion and become the only in-cluster proof that the pods
# ran -- which is the crux of the scenario.
set -e
NS=app-271
PODS="log-archival-index-to-es-7q4w9z-1 log-archival-index-to-es-7q4w9z-2 log-archival-index-to-es-7q4w9z-3"

kubectl apply -f task_pods.yaml

# Wait for every task pod to reach a terminal phase (they exit 1 -> Failed).
for pod in $PODS; do
  ok=false
  for i in $(seq 1 120); do
    phase="$(kubectl get pod "$pod" -n "$NS" -o jsonpath='{.status.phase}' 2>/dev/null || echo '')"
    if [ "$phase" = "Failed" ] || [ "$phase" = "Succeeded" ]; then ok=true; break; fi
    sleep 1
  done
  if [ "$ok" = false ]; then
    echo "ERROR: $pod did not reach a terminal phase"
    kubectl describe pod "$pod" -n "$NS" | tail -40 || true
    exit 1
  fi
done

# Confirm the "proof it ran" Started event exists for the task pods BEFORE we
# delete them (field selector on reason + involvedObject name).
if ! kubectl get events -n "$NS" \
      --field-selector reason=Started,involvedObject.name=log-archival-index-to-es-7q4w9z-1 \
      -o jsonpath='{.items[*].reason}' 2>/dev/null | grep -q Started; then
  echo "ERROR: expected Started lifecycle event for task pod not found"
  kubectl get events -n "$NS" | head -60 || true
  exit 1
fi

# Mimic the operator deleting the ephemeral pods once the task finishes.
kubectl delete -f task_pods.yaml --wait=true

# kubectl logs must now be unavailable (pods gone) while events remain.
if kubectl get pod log-archival-index-to-es-7q4w9z-1 -n "$NS" >/dev/null 2>&1; then
  echo "ERROR: task pod still exists after deletion"
  exit 1
fi
if ! kubectl get events -n "$NS" \
      --field-selector reason=Started,involvedObject.name=log-archival-index-to-es-7q4w9z-1 \
      -o jsonpath='{.items[*].reason}' 2>/dev/null | grep -q Started; then
  echo "ERROR: lifecycle events did not survive task pod deletion"
  exit 1
fi

echo "OK: task pods ran, failed at the application layer, and were deleted; lifecycle events retained."

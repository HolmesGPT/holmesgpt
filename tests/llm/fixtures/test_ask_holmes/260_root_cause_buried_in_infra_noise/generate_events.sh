#!/bin/bash
# Builds the event landscape for the "root cause buried in infra noise" eval.
#
# Two very different kinds of events coexist:
#
#  1. NORMAL lifecycle events for the 3 ephemeral KubernetesPodOperator task
#     pods (log-archival-index-to-es-7q4w9z-{1,2,3}). These pods have since been
#     deleted by the operator (its default on-finish behavior), so `kubectl
#     logs` returns nothing for them -- but their events remain and prove they
#     were Scheduled, pulled their image, and Started successfully. In other
#     words infrastructure did NOT stop them from running; they ran and then
#     failed at the application layer (whose detail lives in the Airflow task
#     logs, not in kubectl).
#
#  2. A loud storm of unrelated WARNING infra events affecting OTHER workloads:
#     AWS-CNI IP-address-exhaustion sandbox failures and node draining /
#     Karpenter churn. This is real but unrelated cluster noise. The bug being
#     reproduced is Holmes blaming THIS noise for the task failures instead of
#     recognizing the task pods actually ran.
set -e
NOW="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
OUT="$(mktemp /tmp/app260-events.XXXXXX.yaml)"

emit_lifecycle() {
  # $1 = pod name, $2 = event-name suffix
  local pod="$1" sfx="$2"
  cat <<EOF
  - apiVersion: v1
    kind: Event
    metadata: {name: ${sfx}-scheduled, namespace: app-260}
    type: Normal
    reason: Scheduled
    count: 1
    firstTimestamp: "${NOW}"
    lastTimestamp: "${NOW}"
    source: {component: default-scheduler}
    reportingComponent: default-scheduler
    reportingInstance: default-scheduler
    involvedObject: {apiVersion: v1, kind: Pod, namespace: app-260, name: ${pod}}
    message: "Successfully assigned app-260/${pod} to ip-10-0-5-12.ec2.internal"
  - apiVersion: v1
    kind: Event
    metadata: {name: ${sfx}-pulled, namespace: app-260}
    type: Normal
    reason: Pulled
    count: 1
    firstTimestamp: "${NOW}"
    lastTimestamp: "${NOW}"
    source: {component: kubelet, host: ip-10-0-5-12.ec2.internal}
    reportingComponent: kubelet
    reportingInstance: ip-10-0-5-12.ec2.internal
    involvedObject: {apiVersion: v1, kind: Pod, namespace: app-260, name: ${pod}}
    message: "Container image \"registry.internal/log-archival/indexer:2.3.1\" already present on machine"
  - apiVersion: v1
    kind: Event
    metadata: {name: ${sfx}-started, namespace: app-260}
    type: Normal
    reason: Started
    count: 1
    firstTimestamp: "${NOW}"
    lastTimestamp: "${NOW}"
    source: {component: kubelet, host: ip-10-0-5-12.ec2.internal}
    reportingComponent: kubelet
    reportingInstance: ip-10-0-5-12.ec2.internal
    involvedObject: {apiVersion: v1, kind: Pod, namespace: app-260, name: ${pod}}
    message: "Started container base"
EOF
}

{
  echo "apiVersion: v1"
  echo "kind: List"
  echo "items:"

  # 1. Lifecycle events proving each of the 3 deleted task pods actually ran.
  emit_lifecycle "log-archival-index-to-es-7q4w9z-1" "task1"
  emit_lifecycle "log-archival-index-to-es-7q4w9z-2" "task2"
  emit_lifecycle "log-archival-index-to-es-7q4w9z-3" "task3"

  # 2a. ~40 FailedCreatePodSandBox events on OTHER pods (AWS CNI IP exhaustion).
  for n in $(seq 0 39); do
    cat <<EOF
  - apiVersion: v1
    kind: Event
    metadata: {name: ipexhaust-${n}, namespace: app-260}
    type: Warning
    reason: FailedCreatePodSandBox
    count: 17
    firstTimestamp: "${NOW}"
    lastTimestamp: "${NOW}"
    source: {component: kubelet, host: ip-10-0-${n}-42.ec2.internal}
    reportingComponent: kubelet
    reportingInstance: ip-10-0-${n}-42.ec2.internal
    involvedObject: {apiVersion: v1, kind: Pod, namespace: app-260, name: data-pipeline-${n}}
    message: "Failed to create pod sandbox: rpc error: code = Unknown desc = failed to setup network for sandbox: plugin type=\"aws-cni\" name=\"aws-cni\" failed (add): add cmd: failed to assign an IP address to container: InsufficientFreeAddressesInSubnet: subnet-0ab12cd34ef has no available IP addresses"
EOF
  done

  # 2b. ~10 node FailedDraining / Karpenter disruption events (cluster-scoped).
  for n in $(seq 0 9); do
    cat <<EOF
  - apiVersion: v1
    kind: Event
    metadata: {name: nodedrain-${n}, namespace: default}
    type: Warning
    reason: FailedDraining
    count: 4
    firstTimestamp: "${NOW}"
    lastTimestamp: "${NOW}"
    source: {component: karpenter}
    reportingComponent: karpenter
    reportingInstance: karpenter-0
    involvedObject: {apiVersion: v1, kind: Node, name: ip-10-0-${n}-42.ec2.internal}
    message: "Karpenter disruption: failed draining node ip-10-0-${n}-42.ec2.internal during consolidation; eviction blocked by PodDisruptionBudget; node will be force-terminated"
EOF
  done
} > "$OUT"

kubectl apply -f "$OUT"
rm -f "$OUT"

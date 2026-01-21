# KubeSchedulerDown / KubeControllerManagerDown False Positive Detection

This runbook helps identify false positive alerts for KubeSchedulerDown and KubeControllerManagerDown in managed Kubernetes clusters.

## When to Use

Use this runbook ONLY when investigating alerts with these EXACT names:
- `KubeSchedulerDown`
- `KubeControllerManagerDown`

## Investigation Steps

1. **Check if this is a managed Kubernetes cluster**
   - Fetch the cluster nodes and examine their labels
   - Look for labels indicating a managed cluster (e.g., EKS, GKE, AKS labels)
   - Common indicators:
     - `eks.amazonaws.com/nodegroup` (EKS)
     - `cloud.google.com/gke-nodepool` (GKE)
     - `kubernetes.azure.com/agentpool` (AKS)

2. **If managed cluster (EKS, GKE, AKS, etc.)**
   - This is likely a **known false positive** in kube-prometheus-stack
   - The scheduler and controller-manager are managed by the cloud provider
   - Prometheus cannot scrape these components because they run in the provider's control plane
   - **Recommendation**: Consider disabling or modifying these alerts for managed clusters

3. **If self-managed Kubernetes**
   - Either the scheduler/controller-manager is actually down (unlikely)
   - Or Prometheus cannot scrape it due to:
     - Network policies blocking access
     - Missing ServiceMonitor configuration
     - Incorrect endpoint configuration
   - Check the component's actual status using `kubectl get componentstatuses`
   - Review Prometheus targets to see if scraping is failing

## Root Cause Summary

For managed clusters, the control plane components (scheduler, controller-manager) run in the cloud provider's infrastructure and are not directly accessible for scraping by Prometheus deployed in the cluster.

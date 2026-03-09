# Holmes Operator - Overview & Installation

Holmes Operator extends HolmesGPT with Kubernetes-native, LLM-driven health checks using Custom Resource Definitions (CRDs). It provides a declarative way to define and schedule LLM-driven investigations, proactively notifying your team when it finds issues.

!!! warning "Holmes Operator - Alpha Release"

    **Important Considerations:**

    - **Status**: Holmes Operator is in **alpha** and subject to breaking changes
    - **AI Usage Costs**: Each health check triggers an LLM call (at least 1). Schedule checks cautiously to manage costs
    - **Recommendation**: Begin with infrequent schedules (e.g., hourly or daily) and monitor usage before scaling up

## Features

- **One-time Health Checks**: Create `HealthCheck` resources that run immediately and report results
- **Scheduled Health Checks**: Create `ScheduledHealthCheck` resources that run on cron schedules
- **Kubernetes-native**: Uses standard CRDs with kubectl support
- **Status Tracking**: Full execution history and results stored in resource status
- **Alert Integration**: Send notifications to Slack and other destinations on failures
- **Cost Management**: Configurable cleanup and history management


## Prerequisites

Before installing Holmes Operator, ensure you have:

- **Kubernetes cluster** (version 1.19+)
- **Helm 3** installed
- **Existing HolmesGPT deployment** - The operator requires a running Holmes API service. If you haven't installed Holmes yet, see the [Helm Chart installation guide](../installation/kubernetes-installation.md)
- **kubectl** configured to access your cluster
- **Supported AI Provider** configured (see [AI Providers](../ai-providers/index.md))

!!! info "RBAC Permissions"

    The Holmes Operator automatically creates a ServiceAccount with the necessary permissions to manage HealthCheck and ScheduledHealthCheck resources and access the Holmes API service.

## Installation

### 1. Update Helm Values

Add the operator configuration to your existing `values.yaml` file:

```yaml
# values.yaml
operator:
  enabled: true  # Enable the operator deployment

  # Optional: Customize operator settings
  holmesApiTimeout: 300  # API timeout in seconds
  maxHistoryItems: 10  # History entries per ScheduledHealthCheck

  # Optional: Resource limits
  resources:
    requests:
      memory: 256Mi
      cpu: 100m
    limits:
      memory: 512Mi
```

For a complete list of configuration options, see the [Configuration](configuration.md) page.

### 2. Install or Upgrade Holmes with Operator

If this is a new installation:

```bash
helm install holmesgpt robusta/holmes -f values.yaml
```

If upgrading an existing installation:

```bash
helm repo update
helm upgrade holmesgpt robusta/holmes -f values.yaml
```

### 3. Verify Installation

Check that the operator pod is running:

```bash
# Check operator deployment
kubectl get deployment -l app.kubernetes.io/name=holmes-operator

# Check operator pod status
kubectl get pods -l app.kubernetes.io/name=holmes-operator

# View operator logs
kubectl logs -l app.kubernetes.io/name=holmes-operator --tail=50
```

Verify that the CRDs are installed:

```bash
# List Holmes CRDs
kubectl get crd | grep holmesgpt.dev

# Should show:
# healthchecks.holmesgpt.dev
# scheduledhealthchecks.holmesgpt.dev
```

You can also verify the CRD details:

```bash
# View HealthCheck CRD
kubectl get crd healthchecks.holmesgpt.dev

# View ScheduledHealthCheck CRD
kubectl get crd scheduledhealthchecks.holmesgpt.dev
```

## Quick Start

Now that the operator is installed, you can create your first health check:

### Create a Simple Health Check

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: example-check
  namespace: default
spec:
  query: "Are all pods in the default namespace running?"
  timeout: 30
```

Apply it and check the results:

```bash
# Create the health check
kubectl apply -f healthcheck.yaml

# Check status (short name: hc)
kubectl get hc

# View detailed results
kubectl describe hc example-check
```

## Next Steps

- **[Health Checks](health-checks.md)** - Learn how to create and manage one-time HealthCheck resources
- **[Scheduled Health Checks](scheduled-health-checks.md)** - Set up recurring health checks with cron schedules
- **[Alert Destinations](destinations.md)** - Configure Slack and PagerDuty notifications
- **[Configuration](configuration.md)** - Explore advanced configuration options
- **[Development Guide](development.md)** - Build and test operator changes locally

## Architecture

The Holmes Operator follows the Kubernetes Job/CronJob pattern:

- **HealthCheck**: One-time execution (like a Job)
- **ScheduledHealthCheck**: Creates HealthCheck resources on a schedule (like a CronJob)
- **Operator**: Watches CRDs and orchestrates check execution
- **Holmes API**: Executes the actual health check logic using LLM

The operator uses a distributed architecture: a lightweight kopf-based controller handles CRD orchestration and scheduling, while stateless Holmes API servers execute the actual checks.

### Key Design Decisions

- **Job/CronJob pattern**: Separate HealthCheck and ScheduledHealthCheck CRDs provide clear semantics — one-time checks give immediate feedback, scheduled checks manage recurrence. This mirrors the familiar Kubernetes Job/CronJob model.
- **Distributed operator + API servers**: The operator is a lightweight controller that delegates check execution to stateless Holmes API servers via HTTP. This allows API servers to scale horizontally and isolates scheduling concerns from execution.
- **APScheduler over CronJobs**: Scheduling uses APScheduler within the operator rather than Kubernetes CronJobs. This is more efficient for frequent checks and avoids pod startup overhead per execution.
- **HealthCheck resources as history**: ScheduledHealthCheck creates HealthCheck resources for each run, providing a natural audit trail queryable with standard kubectl commands.

## Need Help?

- **[Join our Slack](https://cloud-native.slack.com/archives/C0A1SPQM5PZ)** - Get help from the community
- **[Request features on GitHub](https://github.com/HolmesGPT/holmesgpt/issues)** - Suggest improvements or report bugs

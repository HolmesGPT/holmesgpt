# Health Checks

HealthCheck resources provide one-time health check execution in Kubernetes. When you create a HealthCheck, the Holmes Operator immediately executes it using the Holmes API and stores the results in the resource's status.

## What is a HealthCheck?

A HealthCheck is a Kubernetes Custom Resource that:

- Runs immediately when created
- Executes a natural language query using an LLM
- Stores results (pass/fail/error) in its status
- Can optionally send alerts to configured destinations
- Maintains an audit trail of check execution
- Can be re-run on demand using annotations

## Creating a Simple Health Check

The simplest HealthCheck requires only a natural language query:

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: check-pod-health
  namespace: default
spec:
  query: "Are all pods in namespace 'default' healthy and running?"
```

Apply this check and view its status:

```bash
# Create the check
kubectl apply -f healthcheck.yaml

# View check status (short name: hc)
kubectl get hc

# Get detailed results
kubectl describe hc check-pod-health
```

## Health Check with Alert Mode

To send notifications when a check fails, use `alert` mode with destinations:

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: frontend-deployment-check
  namespace: production
spec:
  query: "Is the 'frontend' deployment healthy with at least 3 ready replicas?"
  timeout: 60
  mode: alert
  destinations:
    - type: slack
      config:
        channel: "#production-alerts"
```

## Spec Fields Reference

### Required Fields

**query** (string, required)

Natural language question about system health. The LLM will analyze your cluster and answer this question.

- Min length: 1 character
- Max length: 5000 characters
- Example: `"Are all pods in deployment 'api' ready and not crash-looping?"`

### Optional Fields

**timeout** (integer, optional)

Maximum execution time in seconds before the check is terminated.

- Default: 30 seconds
- Minimum: 1 second
- Maximum: 300 seconds (5 minutes)
- Example: `timeout: 120`

**mode** (string, optional)

Execution mode that determines whether alerts are sent:

- `monitor` (default): Results are stored but no alerts are sent
- `alert`: Sends notifications to configured destinations on check failure

**model** (string, optional)

Override the default LLM model for this specific check. Useful for testing different models or controlling costs.

- Example: `model: "anthropic/claude-sonnet-4-5-20250929"`
- See [AI Providers](../ai-providers/index.md) for supported models

**destinations** (array, optional)

List of alert destinations. Only used when `mode: alert`.

Each destination requires:

- `type`: Destination type (e.g., "slack", "pagerduty")
- `config`: Destination-specific configuration object

Example:

```yaml
destinations:
  - type: slack
    config:
      channel: "#alerts"
  - type: pagerduty
    config:
      integration_key: "your-integration-key"
```

## Status Fields

After execution, the HealthCheck status contains:

### Execution Tracking

**phase** (string)

Current execution state:

- `Pending`: Check created, waiting to start
- `Running`: Check execution in progress
- `Completed`: Check finished successfully
- `Failed`: Check execution failed due to error

**startTime** (timestamp)

ISO 8601 timestamp when execution started.

**completionTime** (timestamp)

ISO 8601 timestamp when execution finished.

**duration** (number)

Total execution time in seconds.

### Results

**result** (string)

The check outcome:

- `pass`: System is healthy based on the query
- `fail`: System is unhealthy based on the query
- `error`: Execution failed (network issue, timeout, etc.)

**message** (string)

Brief human-readable summary of the result.

Example: `"All 3 replicas of 'frontend' deployment are ready"`

**rationale** (string)

Detailed LLM explanation of the decision, including evidence and reasoning.

**error** (string)

Error details if `phase: Failed` or `result: error`.

**modelUsed** (string)

The actual LLM model used for execution.

### Notifications

**notifications** (array)

Status of alert delivery attempts when using `mode: alert`:

```yaml
notifications:
  - type: slack
    channel: "#alerts"
    status: sent  # sent, failed, or skipped
```

### Conditions

Standard Kubernetes conditions track the check lifecycle:

```yaml
conditions:
  - type: Complete
    status: "True"
    lastTransitionTime: "2024-01-01T00:00:00Z"
    reason: Pass
    message: "Check completed successfully"
```

## Viewing Results

List all checks in a namespace:

```bash
# Using full name
kubectl get healthchecks -n default

# Using short name
kubectl get hc -n default

# All namespaces
kubectl get hc --all-namespaces
```

View detailed check results:

```bash
# Describe shows full status including rationale
kubectl describe hc check-pod-health

# Get status as YAML
kubectl get hc check-pod-health -o yaml
```

Check specific fields:

```bash
# View just the result
kubectl get hc check-pod-health -o jsonpath='{.status.result}'

# View the message
kubectl get hc check-pod-health -o jsonpath='{.status.message}'

# View the rationale
kubectl get hc check-pod-health -o jsonpath='{.status.rationale}'
```

## Re-running Checks

To re-execute a check, add the rerun annotation:

```bash
kubectl annotate hc check-pod-health holmesgpt.dev/rerun=true --overwrite
```

This triggers a new execution while preserving the original resource. The status will be updated with new results.

## Practical Examples

### Check Deployment Replicas

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: check-api-replicas
spec:
  query: "Does the 'api' deployment in 'production' namespace have at least 5 ready replicas?"
  timeout: 30
```

### Check Pod Status

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: check-crashlooping-pods
spec:
  query: "Are there any CrashLoopBackOff pods in the 'production' namespace?"
  timeout: 45
```

### Check Resource Usage

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: check-node-memory
spec:
  query: "Are any nodes in the cluster using more than 90% memory?"
  timeout: 60
```

### Check with Alert

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: check-critical-pods
spec:
  query: "Are all pods with label 'tier=critical' in 'production' namespace running and ready?"
  timeout: 60
  mode: alert
  destinations:
    - type: slack
      config:
        channel: "#critical-alerts"
```

### Custom Model for Cost Control

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: check-with-cheaper-model
spec:
  query: "Are all pods in 'staging' namespace healthy?"
  model: "anthropic/claude-sonnet-4-5-20250929"
  timeout: 30
```

## Labels and Selectors

Use labels to organize and query HealthChecks:

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: frontend-check
  labels:
    app: frontend
    environment: production
    team: platform
spec:
  query: "Is the frontend deployment healthy?"
```

Query by labels:

```bash
# Find all production checks
kubectl get hc -l environment=production

# Find checks for a specific app
kubectl get hc -l app=frontend

# Find checks by team
kubectl get hc -l team=platform
```

## Next Steps

- **[Scheduled Health Checks](scheduled-health-checks.md)** - Set up recurring checks with cron schedules
- **[Alert Destinations](destinations.md)** - Configure Slack and PagerDuty notifications
- **[Configuration](configuration.md)** - Advanced configuration options

# Scheduled Health Checks

ScheduledHealthCheck resources provide recurring health check execution based on cron schedules. They automatically create HealthCheck resources at scheduled intervals, making them ideal for continuous monitoring.

## What is a ScheduledHealthCheck?

A ScheduledHealthCheck is a Kubernetes Custom Resource that:

- Creates HealthCheck resources on a cron schedule
- Tracks execution history for recent runs
- Maintains status of active (running) checks
- Can be enabled/disabled without deletion
- Follows the Kubernetes CronJob pattern
- Records last execution time and results

!!! warning "Cost Management"

    Each scheduled execution creates at least one LLM API call. A schedule running every 5 minutes = 288 API calls per day. Start with infrequent schedules (hourly or daily) and monitor costs before increasing frequency.

## Creating a Scheduled Check

The simplest ScheduledHealthCheck requires a cron schedule and a query:

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: ScheduledHealthCheck
metadata:
  name: hourly-pod-check
  namespace: default
spec:
  schedule: "0 * * * *"  # Every hour at :00
  query: "Are all pods in namespace 'default' healthy and running?"
```

Apply this check:

```bash
# Create the scheduled check
kubectl apply -f scheduled-check.yaml

# View status (short name: shc)
kubectl get shc

# Get detailed information
kubectl describe shc hourly-pod-check
```

## Scheduled Check with Alerts

Send notifications when checks fail:

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: ScheduledHealthCheck
metadata:
  name: production-monitor
  namespace: production
spec:
  schedule: "*/15 * * * *"  # Every 15 minutes
  query: "Are all 'critical' labeled pods in 'production' namespace healthy?"
  timeout: 60
  mode: alert
  destinations:
    - type: slack
      config:
        channel: "#production-alerts"
```

## Cron Schedule Syntax

Cron expressions use five fields:

```
┌───────────── minute (0 - 59)
│ ┌───────────── hour (0 - 23)
│ │ ┌───────────── day of month (1 - 31)
│ │ │ ┌───────────── month (1 - 12)
│ │ │ │ ┌───────────── day of week (0 - 6) (Sunday to Saturday)
│ │ │ │ │
│ │ │ │ │
* * * * *
```

!!! tip "Testing Schedules"

    Use [crontab.guru](https://crontab.guru) to validate and understand cron expressions.

## Spec Fields Reference

### Required Fields

**schedule** (string, required)

Cron expression defining when to create health checks.

- Must be valid cron syntax
- Uses UTC timezone
- Example: `"*/15 * * * *"` (every 15 minutes)

**query** (string, required)

Natural language question about system health.

- Min length: 1 character
- Max length: 5000 characters
- Example: `"Are all pods with label 'app=api' ready?"`

### Optional Fields

**enabled** (boolean, optional)

Whether the schedule is active.

- Default: `true`
- Set to `false` to disable without deleting the resource
- Existing HealthCheck resources are not affected

**timeout** (integer, optional)

Maximum execution time per check in seconds.

- Default: 30 seconds
- Minimum: 1 second
- Maximum: 300 seconds (5 minutes)

**mode** (string, optional)

Execution mode for alert delivery:

- `monitor` (default): Results stored but no alerts sent
- `alert`: Sends notifications to destinations on failure

**model** (string, optional)

Override default LLM model for all scheduled checks.

- Example: `model: "anthropic/claude-sonnet-4-5-20250929"`
- See [AI Providers](../ai-providers/index.md) for options

**destinations** (array, optional)

Alert destinations (only used with `mode: alert`).

Example:

```yaml
destinations:
  - type: slack
    config:
      channel: "#alerts"
```

## Status Fields

### Execution Tracking

**lastScheduleTime** (timestamp)

ISO 8601 timestamp of the most recent scheduled execution.

**lastSuccessfulTime** (timestamp)

ISO 8601 timestamp of the most recent successful (pass) execution.

**lastResult** (string)

Result of the most recent execution:

- `pass`: Check passed
- `fail`: Check failed
- `error`: Execution error

**message** (string)

Brief message from the most recent execution.

### Active Checks

**active** (array)

List of currently running HealthCheck resources created by this schedule:

```yaml
active:
  - name: hourly-pod-check-20240101-120000-abc123
    namespace: default
    uid: 12345-67890
    startTime: "2024-01-01T12:00:00Z"
```

### Execution History

**history** (array)

Recent execution records (limited to `maxHistoryItems` from operator config, default 10):

```yaml
history:
  - executionTime: "2024-01-01T12:00:00Z"
    result: pass
    duration: 2.5
    checkName: hourly-pod-check-20240101-120000-abc123
    message: "All pods healthy"
  - executionTime: "2024-01-01T11:00:00Z"
    result: pass
    duration: 3.1
    checkName: hourly-pod-check-20240101-110000-def456
    message: "All pods healthy"
```

### Conditions

Standard Kubernetes conditions:

```yaml
conditions:
  - type: ScheduleRegistered
    status: "True"
    lastTransitionTime: "2024-01-01T10:00:00Z"
    reason: ScheduleActive
    message: "Schedule successfully registered"
```

## Managing Schedules

### Viewing Schedules

List all scheduled checks:

```bash
# Using full name
kubectl get scheduledhealthchecks -n default

# Using short name
kubectl get shc -n default

# All namespaces
kubectl get shc --all-namespaces
```

View detailed status:

```bash
# Full details including history
kubectl describe shc hourly-pod-check

# Get as YAML
kubectl get shc hourly-pod-check -o yaml
```

### Enabling and Disabling

Temporarily disable a schedule:

```bash
kubectl patch shc hourly-pod-check --type='merge' -p '{"spec":{"enabled":false}}'
```

Re-enable a schedule:

```bash
kubectl patch shc hourly-pod-check --type='merge' -p '{"spec":{"enabled":true}}'
```

!!! note

    Disabling a schedule stops future executions but does not affect currently running checks. Existing HealthCheck resources remain.

### Updating Schedule

Change the cron schedule:

```bash
kubectl patch shc hourly-pod-check --type='merge' -p '{"spec":{"schedule":"0 */2 * * *"}}'
```

This updates the schedule to run every 2 hours instead of hourly.

### Viewing Execution History

Check recent executions:

```bash
# View history field
kubectl get shc hourly-pod-check -o jsonpath='{.status.history}' | jq

# View last result
kubectl get shc hourly-pod-check -o jsonpath='{.status.lastResult}'

# View last schedule time
kubectl get shc hourly-pod-check -o jsonpath='{.status.lastScheduleTime}'
```

## Next Steps

- **[Health Checks](health-checks.md)** - Learn more about the underlying HealthCheck resources
- **[Alert Destinations](destinations.md)** - Configure Slack and PagerDuty notifications
- **[Configuration](configuration.md)** - Configure schedule history limits and cleanup policies

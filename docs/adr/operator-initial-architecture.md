# Holmes Operator Architecture

## Overview

The Holmes Operator extends Holmes with Kubernetes-native health check capabilities using Custom Resource Definitions (CRDs). Following the Kubernetes Job/CronJob pattern, it provides two CRD types:

- **HealthCheck**: One-time execution checks that run immediately when created
- **ScheduledHealthCheck**: Recurring checks that create HealthCheck resources on a cron schedule

The architecture maintains separation of concerns with a lightweight operator handling orchestration while stateless Holmes API servers perform the actual check execution.

## Architecture Components

### 1. Holmes Operator

A lightweight Kubernetes controller built with [kopf](https://kopf.readthedocs.io/) that:

- Watches HealthCheck CRDs
- Manages check scheduling using APScheduler
- Makes HTTP calls to Holmes API servers for check execution
- Updates CRD status with results
- Handles retry logic and error recovery

### 2. Holmes API Servers

Stateless FastAPI servers that:

- Execute health checks via `/api/check/execute` endpoint
- Reuse existing CheckRunner implementation
- Scale horizontally based on load
- Share LLM rate limits and resource pools

### 3. HealthCheck CRD

One-time execution resource that:

- Runs immediately upon creation
- Stores execution results in status
- Can be re-run via annotation
- Maintains audit trail of checks

### 4. ScheduledHealthCheck CRD

Recurring check resource that:

- Defines cron schedule for checks
- Creates HealthCheck resources at scheduled times
- Tracks execution history
- Manages concurrency and history limits

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     Kubernetes Cluster                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────────────────┐                             │
│  │   Holmes Operator (kopf)    │                             │
│  │  - CRD Watching             │                             │
│  │  - Scheduling (APScheduler) │                             │
│  │  - Status Management        │                             │
│  │  - Retry Logic              │                             │
│  └──────┬───────────┬──────────┘                            │
│         │           │                                        │
│         │           │ Watches/Updates                        │
│         │           │                                        │
│         │           ▼                                        │
│         │  ┌────────────────────────────┐                   │
│         │  │    HealthCheck CRDs        │                   │
│         │  │    (One-time execution)    │                   │
│         │  └────────────────────────────┘                   │
│         │           │                                        │
│         │           ▼                                        │
│         │  ┌────────────────────────────┐                   │
│         │  │  ScheduledHealthCheck CRDs │                   │
│         │  │     (Recurring checks)     │                   │
│         │  └────────────────────────────┘                   │
│         │                                                    │
│         │ HTTP API Calls                                     │
│         │                                                    │
│         ▼                                                    │
│  ┌─────────────────────────────────────────┐                │
│  │        Service: holmes-api              │                │
│  ├─────────────────────────────────────────┤                │
│  │   ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │   │ server.py│  │ server.py│  │ server.py│   (Stateless)│
│  │   │ Pod 1    │  │ Pod 2    │  │ Pod 3    │              │
│  │   └──────────┘  └──────────┘  └──────────┘             │
│  └─────────────────────────────────────────┘                │
└─────────────────────────────────────────────────────────────┘
```

## CRD Specification

### HealthCheck Resource (One-time)

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: frontend-health-check
  namespace: default
  labels:
    # Set by ScheduledHealthCheck if created by schedule
    holmesgpt.dev/scheduled-by: frontend-schedule
spec:
  # Check definition
  query: "Are all pods in deployment 'frontend' healthy with at least 2 ready replicas?"
  timeout: 30  # seconds
  mode: alert  # or "monitor" (no alerts)
  destinations:
    - type: slack
      config:
        channel: "#alerts"

status: 
  # Execution phase
  phase: Completed  # Pending/Running/Completed
  startTime: "2024-01-01T00:00:00Z"
  completionTime: "2024-01-01T00:00:02Z"

  # Result
  result: pass  # pass/fail/error
  message: "All pods healthy with 3/3 replicas ready"
  rationale: "Deployment 'frontend' has 3 ready replicas out of 3 desired."
  duration: 2.0

  # Conditions
  conditions:
    - type: Complete
      status: "True"
      lastTransitionTime: "2024-01-01T00:00:02Z"
      reason: Pass
      message: "Check completed successfully"
```

### ScheduledHealthCheck Resource (Recurring)

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: ScheduledHealthCheck
metadata:
  name: frontend-schedule
  namespace: default
spec:
  # Schedule
  schedule: "*/5 * * * *"  # Cron format (every 5 minutes)
  query: "Are all pods in deployment 'frontend' healthy with at least 2 ready replicas?"
  timeout: 30
  mode: alert
  destinations:
    - type: slack
      config:
        channel: "#alerts"

  # Schedule control
  enabled: true  # Enable or disable the schedule
  # Note: The operator automatically:
  #   - Prevents concurrent runs (max_instances=1)
  #   - Maintains last 10 history items (hardcoded limit)
  #   - Coalesces missed jobs (if schedule was missed while disabled)

status:
  # Last execution
  lastScheduleTime: "2024-01-01T00:05:00Z"
  lastSuccessfulTime: "2024-01-01T00:05:00Z"
  lastResult: pass
  message: "Check completed successfully"

  # Active checks
  active:
    - name: frontend-schedule-20240101-000500-abc123
      namespace: default
      uid: "12345-67890"

  # History (references to created HealthChecks)
  history:
    - executionTime: "2024-01-01T00:05:00Z"
      result: pass
      duration: 2.5
      checkName: frontend-schedule-20240101-000500-abc123
    - executionTime: "2024-01-01T00:00:00Z"
      result: pass
      duration: 2.3
      checkName: frontend-schedule-20240101-000000-def456
```

## API Integration

### Check Execution Endpoint

The operator calls the Holmes API server to execute checks:

```http
POST /api/check/execute
Content-Type: application/json
X-Check-Name: default/frontend-health

{
  "query": "Are all pods in deployment 'frontend' healthy?",
  "timeout": 30,
  "mode": "alert",
  "destinations": [
    {
      "type": "slack",
      "config": {
        "channel": "#alerts"
      }
    }
  ]
}
```

Response:
```json
{
  "status": "pass",
  "message": "All pods are healthy with 3/3 replicas ready",
  "duration": 2.5,
  "rationale": "Deployment 'frontend' has 3 ready replicas out of 3 desired.",
  "error": null
}
```

## Deployment

### Operator Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: holmes-operator
  namespace: holmes
spec:
  replicas: 1
  selector:
    matchLabels:
      app: holmes-operator
  template:
    metadata:
      labels:
        app: holmes-operator
    spec:
      serviceAccountName: holmes-operator
      containers:
      - name: operator
        image: robusta/holmes-operator:latest
        command: ["kopf", "run", "-A", "--standalone", "/app/operator.py"]
        env:
        - name: HOLMES_API_URL
          value: "http://holmes-api:8080"
        - name: LOG_LEVEL
          value: "INFO"
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
```

### RBAC Configuration

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: holmes-operator
  namespace: holmes
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: holmes-operator
rules:
- apiGroups: ["holmesgpt.dev"]
  resources: ["healthchecks"]
  verbs: ["get", "list", "watch", "patch", "update"]
- apiGroups: [""]
  resources: ["events"]
  verbs: ["create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: holmes-operator
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: holmes-operator
subjects:
- kind: ServiceAccount
  name: holmes-operator
  namespace: holmes
```

## Key Design Decisions

### 1. Job/CronJob Pattern

- **Decision**: Separate HealthCheck and ScheduledHealthCheck CRDs
- **Rationale**: Clear semantics, immediate feedback for one-time checks, follows K8s patterns
- **Trade-off**: Two CRDs instead of one, but clearer user experience

### 2. Distributed Architecture

- **Decision**: Separate operator from API servers
- **Rationale**: Better scalability, fault isolation, and resource efficiency
- **Trade-off**: Slightly more complex deployment

### 3. HTTP Communication

- **Decision**: Operator calls API via HTTP instead of direct execution
- **Rationale**: Reuses existing API infrastructure, enables horizontal scaling
- **Trade-off**: Network latency, requires service discovery

### 4. APScheduler for Scheduling

- **Decision**: Use APScheduler library instead of Kubernetes CronJobs
- **Rationale**: More efficient for frequent checks, better resource pooling
- **Trade-off**: Scheduling logic in operator instead of native K8s

### 5. HealthCheck Resources as History

- **Decision**: ScheduledHealthCheck creates HealthCheck resources
- **Rationale**: Natural audit trail, reusable checks, clear execution records
- **Trade-off**: More resources created, requires cleanup strategy

## Monitoring and Observability

### Metrics

The operator exposes Prometheus metrics on port 8080 at `/metrics`:

- `holmes_checks_scheduled_total` - Total number of scheduled checks (labels: namespace, name)
- `holmes_checks_executed_total` - Total number of executed checks (labels: namespace, name, type)
- `holmes_checks_failed_total` - Total number of failed checks (labels: namespace, name, type)
- `holmes_check_duration_seconds` - Check execution duration histogram (labels: namespace, name, type)
- `holmes_scheduled_checks_active` - Number of currently active scheduled checks (gauge)

### Logging

- Operator logs: Scheduling, CRD events, API calls
- API server logs: Check execution, tool calls, errors

### kubectl Integration
```bash
# List one-time checks
kubectl get healthcheck

# List scheduled checks
kubectl get scheduledhealthcheck

# View check execution
kubectl describe healthcheck frontend-health-check

# View schedule status
kubectl describe scheduledhealthcheck frontend-schedule

# Get checks created by a schedule
kubectl get healthcheck -l holmesgpt.dev/scheduled-by=frontend-schedule

# Disable a schedule
kubectl patch scheduledhealthcheck frontend-schedule --type='merge' -p '{"spec":{"enabled":false}}'
```

## Security Considerations

### Authentication

- Operator uses Kubernetes service account for API authentication
- API servers validate requests from operator
- Secrets for destinations stored in Kubernetes secrets

### Network Policies

- Restrict operator to API server communication
- Limit API server egress for tool execution

### Resource Limits

- Operator has strict resource limits
- Timeout enforcement for check execution (configurable, max 300s)

### Known Limitations

- **No built-in rate limiting**: The operator does not limit the rate of check creation or execution. Consider using ResourceQuotas or ValidatingAdmissionWebhooks to prevent DoS
- **No query validation**: Check queries are passed directly to the LLM without content validation. Ensure trusted users only
- **Cluster-wide RBAC**: The operator has cluster-wide access to HealthCheck CRDs

## Future Enhancements

### Phase 1 (MVP)

- [x] Basic CRD and operator
- [x] Check execution via API
- [x] Status updates
- [x] Cron scheduling

### Phase 2 (Future Enhancements)

- [ ] High availability with leader election
- [ ] Check dependencies
- [ ] Webhook validation
- [ ] Result persistence to external storage
- [ ] Configurable concurrency policies
- [ ] Configurable history retention limits

### Phase 3

- [ ] Multi-tenancy support
- [ ] Check templates
- [ ] Grafana dashboard integration
- [ ] Cost tracking per check


## Troubleshooting

### Common Issues

1. **Check not executing**

   - Verify operator is running: `kubectl logs -l app=holmes-operator`
   - Check CRD status: `kubectl get healthcheck <name> -o yaml`
   - Ensure API service is accessible

2. **Authentication errors**
   - Verify service account permissions
   - Check RBAC configuration
   - Validate API server logs

3. **Scheduling issues**

   - Check operator logs for scheduler errors
   - Verify cron expression syntax
   - Check for suspended status

## References

- [Kopf Documentation](https://kopf.readthedocs.io/)
- [APScheduler Documentation](https://apscheduler.readthedocs.io/)
- [Kubernetes CRD Best Practices](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)
- [Holmes Check Documentation](./health-checks.md)

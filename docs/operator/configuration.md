# Configuration

This page covers advanced configuration options for the Holmes Operator, including Helm values, resource management, and per-check customization.

## Operator Helm Configuration

The operator is configured through the `operator` section in your Helm `values.yaml` file. All settings are optional and have sensible defaults.

### Basic Configuration

```yaml
operator:
  enabled: true  # Deploy the operator

  # Holmes API connection
  holmesApiUrl: ""  # Defaults to "http://<release-name>-holmes:80"
  holmesApiTimeout: 300  # API timeout in seconds

  # Logging
  logLevel: INFO  # DEBUG, INFO, WARNING, ERROR

  # History management
  maxHistoryItems: 10  # Number of history entries per ScheduledHealthCheck
  cleanupCompletedChecks: false  # Auto-delete completed checks
  completedCheckTTLHours: 24  # TTL for cleanup (when enabled)
```

### Complete Configuration Example

```yaml
operator:
  enabled: true

  # Container image
  image: holmes-operator:0.0.0
  registry: robustadev
  imagePullPolicy: IfNotPresent

  # Holmes API connection
  holmesApiUrl: "http://holmes-api:80"
  holmesApiTimeout: 300

  # Logging
  logLevel: DEBUG

  # History management
  maxHistoryItems: 20
  cleanupCompletedChecks: true
  completedCheckTTLHours: 48

  # Resources
  resources:
    requests:
      memory: 512Mi
      cpu: 200m
    limits:
      memory: 1Gi

  # Scheduling
  nodeSelector:
    workload-type: system
  tolerations:
    - key: "dedicated"
      operator: "Equal"
      value: "system"
      effect: "NoSchedule"
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: node-role.kubernetes.io/control-plane
                operator: DoesNotExist

  # Additional environment variables
  additionalEnvVars:
    - name: CUSTOM_VAR
      value: "custom-value"
```

## Configuration Fields Reference

### Connection Settings

**holmesApiUrl** (string)

Base URL for the Holmes API service that executes health checks.

- Default: `http://<release-name>-holmes:80` (internal service)
- Use custom URL for external Holmes API or non-standard deployments
- Must be accessible from the operator pod

Example:

```yaml
holmesApiUrl: "http://holmes-api.monitoring:80"
```

**holmesApiTimeout** (integer)

Maximum time in seconds to wait for Holmes API responses.

- Default: 300 seconds (5 minutes)
- Minimum: 1 second
- Should be greater than the longest expected check timeout
- Increase for checks with large data gathering requirements

### Logging

**logLevel** (string)

Operator logging verbosity.

- Default: `INFO`
- Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`
- Use `DEBUG` for troubleshooting schedule execution and API calls
- Use `ERROR` for production to reduce log volume

```yaml
logLevel: DEBUG
```

### History Management

**maxHistoryItems** (integer)

Number of execution history entries to maintain per ScheduledHealthCheck.

- Default: 10
- Minimum: 1
- Higher values provide more history but use more etcd storage
- Each history entry is ~500 bytes

Cost calculation:

```
# Storage per ScheduledHealthCheck
maxHistoryItems * 500 bytes = storage used

# Example: 20 history items
20 * 500 bytes = 10 KB per schedule
```

**cleanupCompletedChecks** (boolean)

Whether to automatically delete completed HealthCheck resources.

- Default: `false`
- When `true`, completed checks are deleted after `completedCheckTTLHours`
- Useful for managing cluster resource usage
- Historical data is preserved in ScheduledHealthCheck history

**completedCheckTTLHours** (integer)

Time-to-live in hours for completed HealthCheck resources (when cleanup is enabled).

- Default: 24 hours
- Only applies when `cleanupCompletedChecks: true`
- Checks in `Completed` or `Failed` phase are cleaned up
- Running checks are never deleted

Example for aggressive cleanup:

```yaml
cleanupCompletedChecks: true
completedCheckTTLHours: 6  # Delete after 6 hours
```

### Resource Management

**resources** (object)

Kubernetes resource requests and limits for the operator pod.

Default:

```yaml
resources:
  requests:
    memory: 256Mi
    cpu: 100m
  limits:
    memory: 512Mi
```


### Pod Scheduling

**nodeSelector** (object)

Node selector for operator pod placement.

```yaml
nodeSelector:
  workload-type: system
```

**tolerations** (array)

Tolerations for tainted nodes.

```yaml
tolerations:
  - key: "dedicated"
    operator: "Equal"
    value: "system"
    effect: "NoSchedule"
```

**affinity** (object)

Node and pod affinity rules.

```yaml
affinity:
  nodeAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        preference:
          matchExpressions:
            - key: node-role.kubernetes.io/control-plane
              operator: DoesNotExist
```

### Environment Variables

**additionalEnvVars** (array)

Additional environment variables for the operator container.

```yaml
additionalEnvVars:
  - name: TZ
    value: "America/New_York"
  - name: CUSTOM_SETTING
    value: "value"
```

## RBAC and Permissions

The operator requires specific Kubernetes permissions to function.

### ServiceAccount

The operator uses a ServiceAccount with permissions to:

- Create, read, update, and delete HealthCheck resources
- Create, read, update, and delete ScheduledHealthCheck resources
- Access the Holmes API service
- Watch and list pods (for health checks)

### ClusterRole

The operator's ClusterRole includes:

```yaml
rules:
  # HealthCheck CRD
  - apiGroups: ["holmesgpt.dev"]
    resources: ["healthchecks"]
    verbs: ["create", "get", "list", "watch", "update", "patch", "delete"]

  - apiGroups: ["holmesgpt.dev"]
    resources: ["healthchecks/status"]
    verbs: ["get", "update", "patch"]

  # ScheduledHealthCheck CRD
  - apiGroups: ["holmesgpt.dev"]
    resources: ["scheduledhealthchecks"]
    verbs: ["get", "list", "watch", "update", "patch"]

  - apiGroups: ["holmesgpt.dev"]
    resources: ["scheduledhealthchecks/status"]
    verbs: ["get", "update", "patch"]
```

These permissions are automatically created by the Helm chart.

## Next Steps

- **[Development Guide](development.md)** - Build operator with custom configuration
- **[Helm Configuration Reference](../reference/helm-configuration.md)** - Complete Helm values documentation

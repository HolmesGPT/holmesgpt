# Troubleshooting

This guide covers common issues with Holmes Operator and how to diagnose and resolve them.

## Quick Diagnostic Commands

```bash
# Check if operator is running
kubectl get deployment -l app.kubernetes.io/name=holmes-operator
kubectl get pods -l app.kubernetes.io/name=holmes-operator

# View operator logs
kubectl logs -l app.kubernetes.io/name=holmes-operator --tail=100 --follow

# Check if CRDs are installed
kubectl get crd | grep holmesgpt.dev

# List all health checks and their status
kubectl get healthcheck --all-namespaces
kubectl get scheduledhealthcheck --all-namespaces

# Check specific check details
kubectl describe healthcheck <name>
kubectl describe scheduledhealthcheck <name>

# View operator events
kubectl get events --field-selector involvedObject.kind=Pod

# Test Holmes API connectivity from operator
kubectl exec -it deployment/holmes-operator -- curl http://holmes-api:80/health

# Check operator resource usage
kubectl top pod -l app.kubernetes.io/name=holmes-operator
```

## Common Issues

### Operator Pod Not Running

**Symptoms:**

- Operator pod in `CrashLoopBackOff`, `Error`, or `Pending` state
- No HealthCheck resources being processed

**Diagnosis:**

```bash
# Check pod status
kubectl get pods -l app.kubernetes.io/name=holmes-operator

# View pod events
kubectl describe pod -l app.kubernetes.io/name=holmes-operator

# Check logs if pod is running
kubectl logs -l app.kubernetes.io/name=holmes-operator --tail=50
```

**Common Causes and Solutions:**

**1. Insufficient Resources**

```bash
# Check if pod is OOMKilled
kubectl get pod -l app.kubernetes.io/name=holmes-operator -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.reason}'

# Solution: Increase memory in values.yaml
operator:
  resources:
    requests:
      memory: 512Mi
    limits:
      memory: 1Gi
```

**2. Image Pull Errors**

```bash
# Check image pull status
kubectl describe pod -l app.kubernetes.io/name=holmes-operator | grep -A5 "Events:"

# Solution: Verify image name and registry access
operator:
  image: holmes-operator:0.0.0
  registry: robustadev
  imagePullPolicy: IfNotPresent
```

**3. RBAC Permissions Missing**

```bash
# Check if ServiceAccount exists
kubectl get serviceaccount holmes-operator

# Check if ClusterRole bindings exist
kubectl get clusterrolebinding | grep holmes

# Solution: Reinstall with Helm to create RBAC resources
helm upgrade holmesgpt robusta/holmes -f values.yaml
```

### HealthCheck Stuck in Pending

**Symptoms:**

- HealthCheck phase remains `Pending` for extended time
- No progress after creation

**Diagnosis:**

```bash
# Check check status
kubectl get hc <name> -o jsonpath='{.status.phase}'

# View operator logs for errors
kubectl logs -l app.kubernetes.io/name=holmes-operator --tail=100 | grep <name>

# Check if operator is processing the check
kubectl describe hc <name>
```

**Common Causes and Solutions:**

**1. Operator Not Running**

See "Operator Pod Not Running" section above.

**2. Holmes API Unreachable**

```bash
# Test API connectivity
kubectl exec -it deployment/holmes-operator -- curl http://holmes-api:80/health

# Check if Holmes API pods are running
kubectl get pods -l app.kubernetes.io/name=holmes

# Solution: Verify holmesApiUrl in values.yaml
operator:
  holmesApiUrl: "http://holmes-api:80"  # Must match service name
```

**3. API Timeout**

```bash
# Check for timeout errors in operator logs
kubectl logs -l app.kubernetes.io/name=holmes-operator | grep -i timeout

# Solution: Increase API timeout
operator:
  holmesApiTimeout: 600  # 10 minutes
```

### Schedule Not Executing

**Symptoms:**

- ScheduledHealthCheck exists but no HealthCheck resources being created
- `lastScheduleTime` not updating

**Diagnosis:**

```bash
# Check schedule status
kubectl get shc <name> -o yaml

# Verify schedule is enabled
kubectl get shc <name> -o jsonpath='{.spec.enabled}'

# Check operator logs for schedule errors
kubectl logs -l app.kubernetes.io/name=holmes-operator | grep "schedule"

# Verify cron expression
kubectl get shc <name> -o jsonpath='{.spec.schedule}'
```

**Common Causes and Solutions:**

**1. Schedule Disabled**

```bash
# Check if enabled=false
kubectl get shc <name> -o jsonpath='{.spec.enabled}'

# Solution: Enable the schedule
kubectl patch shc <name> --type='merge' -p '{"spec":{"enabled":true}}'
```

**2. Invalid Cron Expression**

```bash
# Check conditions for validation errors
kubectl get shc <name> -o jsonpath='{.status.conditions}'

# Solution: Fix cron expression
# Test at https://crontab.guru
kubectl patch shc <name> --type='merge' -p '{"spec":{"schedule":"0 * * * *"}}'
```

**3. Operator Restart After Schedule Creation**

```bash
# Check if operator recently restarted
kubectl get pods -l app.kubernetes.io/name=holmes-operator

# Check operator startup time vs schedule creation time
kubectl get shc <name> -o jsonpath='{.metadata.creationTimestamp}'

# Solution: Wait for next scheduled time or restart operator
kubectl rollout restart deployment/holmes-operator
```

**4. Timezone Confusion**

The operator uses UTC for cron schedules.

```bash
# Verify current UTC time
date -u

# Check when schedule should next execute
# Use https://crontab.guru with UTC time

# Solution: Adjust schedule for UTC timezone
# Example: 9 AM EST = 2 PM UTC
schedule: "0 14 * * *"  # 2 PM UTC = 9 AM EST
```

### High AI Usage Costs

**Symptoms:**

- Unexpected high bills from AI provider
- More API calls than expected

**Diagnosis:**

```bash
# Count total scheduled checks
kubectl get shc --all-namespaces --no-headers | wc -l

# List all schedules with frequency
kubectl get shc --all-namespaces -o custom-columns=NAME:.metadata.name,NAMESPACE:.metadata.namespace,SCHEDULE:.spec.schedule

# Calculate approximate daily API calls per schedule
# Example: "*/5 * * * *" = 288 calls/day
# "0 * * * *" = 24 calls/day

# Check execution history
kubectl get shc <name> -o jsonpath='{.status.history}' | jq length
```

**Solutions:**

**1. Reduce Schedule Frequency**

```bash
# Change from every 5 minutes to every hour
kubectl patch shc <name> --type='merge' -p '{"spec":{"schedule":"0 * * * *"}}'

# Or every 6 hours
kubectl patch shc <name> --type='merge' -p '{"spec":{"schedule":"0 */6 * * *"}}'
```

**2. Disable Non-Critical Schedules**

```bash
# Temporarily disable schedule
kubectl patch shc <name> --type='merge' -p '{"spec":{"enabled":false}}'

# Or delete if no longer needed
kubectl delete shc <name>
```

**3. Enable Cleanup to Reduce History Storage**

```yaml
operator:
  cleanupCompletedChecks: true
  completedCheckTTLHours: 12
  maxHistoryItems: 5
```

**4. Use Cheaper Models**

```bash
# Update check to use faster/cheaper model
kubectl patch hc <name> --type='merge' -p '{"spec":{"model":"anthropic/claude-sonnet-4-5-20250929"}}'
```

### Check Execution Fails

**Symptoms:**

- HealthCheck `result: error` or `phase: Failed`
- Error message in status

**Diagnosis:**

```bash
# View error details
kubectl get hc <name> -o jsonpath='{.status.error}'

# Check operator logs for full error
kubectl logs -l app.kubernetes.io/name=holmes-operator | grep <name>

# Verify Holmes API is healthy
kubectl exec -it deployment/holmes-api -- curl http://localhost:80/health
```

**Common Causes and Solutions:**

**1. LLM API Key Invalid or Missing**

```bash
# Check Holmes API pod logs
kubectl logs -l app.kubernetes.io/name=holmes | grep -i "api key\|auth"

# Solution: Verify AI provider credentials
# See: https://holmesgpt.dev/ai-providers/
```

**2. Timeout**

```bash
# Check if error mentions timeout
kubectl get hc <name> -o jsonpath='{.status.error}' | grep -i timeout

# Solution: Increase check timeout
kubectl patch hc <name> --type='merge' -p '{"spec":{"timeout":180}}'
```

**3. Malformed Query**

```bash
# View the query
kubectl get hc <name> -o jsonpath='{.spec.query}'

# Solution: Rephrase query to be more specific
# Example: "Are pods healthy?" -> "Are all pods in namespace 'default' in Running status?"
```

**4. Insufficient Kubernetes Permissions**

```bash
# Check Holmes API pod logs for permission errors
kubectl logs -l app.kubernetes.io/name=holmes | grep -i "forbidden\|unauthorized"

# Solution: Grant additional permissions
# See: https://holmesgpt.dev/data-sources/permissions/
```

### CRDs Not Installed

**Symptoms:**

- `error: the server doesn't have a resource type "healthcheck"`
- CRDs missing from cluster

**Diagnosis:**

```bash
# Check if CRDs exist
kubectl get crd | grep holmesgpt.dev

# Should show:
# healthchecks.holmesgpt.dev
# scheduledhealthchecks.holmesgpt.dev
```

**Solution:**

CRDs are installed automatically by Helm. If missing:

```bash
# Reinstall with Helm
helm upgrade holmesgpt robusta/holmes -f values.yaml

# Or apply CRDs manually
kubectl apply -f https://raw.githubusercontent.com/HolmesGPT/holmesgpt/master/helm/holmes/crds/healthcheck.yaml
kubectl apply -f https://raw.githubusercontent.com/HolmesGPT/holmesgpt/master/helm/holmes/crds/scheduledhealthcheck.yaml
```

### Notifications Not Being Sent

**Symptoms:**

- Check mode is `alert` but no notifications received
- Notification status shows `failed` or `skipped`

**Diagnosis:**

```bash
# Check notification status
kubectl get hc <name> -o jsonpath='{.status.notifications}'

# View operator logs for notification errors
kubectl logs -l app.kubernetes.io/name=holmes-operator | grep -i notification

# Check if destinations are configured
kubectl get hc <name> -o jsonpath='{.spec.destinations}'
```

**Common Causes and Solutions:**

**1. Monitor Mode Instead of Alert**

```bash
# Check mode
kubectl get hc <name> -o jsonpath='{.spec.mode}'

# Solution: Use alert mode
kubectl patch hc <name> --type='merge' -p '{"spec":{"mode":"alert"}}'
```

**2. Destinations Not Configured**

```bash
# Verify Holmes deployment has destination integration
kubectl get configmap holmes-config -o yaml

# Solution: Configure Slack/PagerDuty integration
# See: https://holmesgpt.dev/installation/slack-installation/
```

**3. Check Passed (No Alert Needed)**

```bash
# Check result
kubectl get hc <name> -o jsonpath='{.status.result}'

# Alerts typically only sent on "fail" or "error"
# Solution: This is expected behavior for passing checks
```

## Debugging Tips

### Enable Debug Logging

Temporarily enable debug logging for detailed troubleshooting:

```bash
# Update operator with debug logging
kubectl set env deployment/holmes-operator LOG_LEVEL=DEBUG

# View detailed logs
kubectl logs -l app.kubernetes.io/name=holmes-operator --tail=200 --follow

# Revert to INFO when done
kubectl set env deployment/holmes-operator LOG_LEVEL=INFO
```

### Test Check Manually

Test a health check query directly via Holmes API:

```bash
# Port forward to Holmes API
kubectl port-forward svc/holmes-api 8080:80

# Test check manually
curl -X POST http://localhost:8080/api/check/execute \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Are all pods healthy?",
    "timeout": 30
  }'
```

### Analyze Check Execution Time

If checks are slow or timing out:

```bash
# View check duration
kubectl get hc <name> -o jsonpath='{.status.duration}'

# View start and completion times
kubectl get hc <name> -o jsonpath='{.status.startTime}{"\n"}{.status.completionTime}'

# Check operator logs for slow operations
kubectl logs -l app.kubernetes.io/name=holmes-operator | grep -i "duration\|took\|slow"
```

### Inspect Operator State

Check internal operator state:

```bash
# List active schedules being tracked
kubectl get shc --all-namespaces -o custom-columns=NAME:.metadata.name,ACTIVE:.status.active

# View recent history
kubectl get shc <name> -o jsonpath='{.status.history}' | jq

# Check operator resource usage
kubectl top pod -l app.kubernetes.io/name=holmes-operator
```

## Getting Help

If you're still stuck:

1. **Collect Diagnostic Information**:

```bash
# Gather all relevant information
kubectl get pods -l app.kubernetes.io/name=holmes-operator -o yaml > operator-pod.yaml
kubectl logs -l app.kubernetes.io/name=holmes-operator --tail=500 > operator-logs.txt
kubectl get hc --all-namespaces -o yaml > healthchecks.yaml
kubectl get shc --all-namespaces -o yaml > scheduled-checks.yaml
kubectl describe hc <problematic-check> > check-details.txt
```

2. **Join Community Slack**:
   - [Cloud Native Slack - #holmesgpt](https://cloud-native.slack.com/archives/C0A1SPQM5PZ)

3. **Report Issue on GitHub**:
   - [HolmesGPT Issues](https://github.com/HolmesGPT/holmesgpt/issues)
   - Include operator version, Kubernetes version, and diagnostic information

4. **Check Documentation**:
   - [Configuration](configuration.md) - Advanced settings
   - [Health Checks](health-checks.md) - HealthCheck CRD reference
   - [Scheduled Checks](scheduled-health-checks.md) - ScheduledHealthCheck reference

## FAQ

**Q: Can I run multiple operator replicas for HA?**

A: Not currently. The operator uses APScheduler with memory-based storage which doesn't support multiple replicas. Running multiple instances will cause duplicate executions.

**Q: How do I change the timezone for cron schedules?**

A: Cron schedules always use UTC. Convert your local time to UTC when creating schedules.

**Q: Can I manually trigger a ScheduledHealthCheck?**

A: Yes, but indirectly. Create a one-time HealthCheck with the same query, or use the rerun annotation on an existing check.

**Q: How long are check results stored?**

A: HealthCheck resources persist indefinitely unless deleted or cleanup is enabled. ScheduledHealthCheck history is limited by `maxHistoryItems` (default 10).

**Q: Can I check resources in other namespaces?**

A: Yes, the query can reference any namespace. Example: `"Are all pods in namespace 'production' healthy?"`

**Q: Do checks run during operator restarts?**

A: Missed scheduled checks during downtime are coalesced into a single execution when the operator restarts (APScheduler behavior).

**Q: How do I estimate monthly costs?**

A: Calculate total daily executions across all schedules, multiply by 30, then multiply by your AI provider's per-token cost. Example: 100 schedules * 24 executions/day * 30 days = 72,000 API calls/month.

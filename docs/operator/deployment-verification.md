# Deployment Verification

A common pattern is deploying a HealthCheck alongside your application to verify the new version is working correctly. Since HealthChecks run immediately when created, you can include one in the same manifest (or CI/CD step) as your deployment and use the result to gate rollout progression.

## One-Time Verification with HealthCheck

Include a [HealthCheck](health-checks.md) in the same manifest as your deployment. It runs immediately after `kubectl apply` and reports whether the new version started correctly.

```yaml
# app-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: checkout-api
  namespace: production
spec:
  replicas: 3
  selector:
    matchLabels:
      app: checkout-api
  template:
    metadata:
      labels:
        app: checkout-api
    spec:
      containers:
        - name: checkout-api
          image: myregistry/checkout-api:v2.4.1
---
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: checkout-api-deploy-v2-4-1
  namespace: production
  labels:
    app: checkout-api
    deploy-version: v2.4.1
spec:
  query: "Is the checkout-api deployment in 'production' fully rolled out with all replicas ready and not crash-looping? Check that pods are running the expected image and responding without errors."
  timeout: 120
  mode: alert
  destinations:
    - type: slack
      config:
        channel: "#deploy-alerts"
```

Apply both together:

```bash
kubectl apply -f app-deployment.yaml
```

If pods crash or fail readiness, the check fails and alerts your team.

## Gating CI/CD on the Result

After applying, poll for the result to gate your pipeline:

```bash
# Wait for the check to complete, then read the result
for i in $(seq 1 30); do
  RESULT=$(kubectl get hc checkout-api-deploy-v2-4-1 -n production -o jsonpath='{.status.result}' 2>/dev/null)
  if [ "$RESULT" = "pass" ]; then
    echo "Deploy verified healthy"
    exit 0
  elif [ "$RESULT" = "fail" ] || [ "$RESULT" = "error" ]; then
    echo "Deploy check failed:"
    kubectl get hc checkout-api-deploy-v2-4-1 -n production -o jsonpath='{.status.message}'
    exit 1
  fi
  sleep 10
done
echo "Timed out waiting for health check"
exit 1
```

## Ongoing Monitoring with ScheduledHealthCheck

One-time deploy checks catch immediate failures, but some problems only appear later — memory leaks, connection pool exhaustion, gradual performance degradation. [Scheduled Health Checks](scheduled-health-checks.md) run on a cron schedule to catch these regressions automatically.

## Tips for One-Time HealthChecks

- **Version the check name** (e.g., `checkout-api-deploy-v2-4-1`) so each deploy creates a distinct resource and you keep an audit trail. This applies to one-time `HealthCheck` resources only — `ScheduledHealthCheck` resources use a fixed name and create child HealthChecks automatically.
- **Set a longer timeout** (60–120s) to give the rollout time to complete before Holmes evaluates.
- **Use labels** like `deploy-version` to query checks for a specific release: `kubectl get hc -l deploy-version=v2.4.1`.
- **Combine with ArgoCD**: If you use ArgoCD, the query can reference sync status — e.g., *"Is the ArgoCD application 'checkout-api' synced and healthy with no degraded resources?"* — since Holmes has access to the [ArgoCD toolset](../data-sources/builtin-toolsets/argocd.md).

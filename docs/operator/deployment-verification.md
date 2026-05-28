# Deployment Verification

A common pattern is deploying a HealthCheck alongside your application to verify the new version is working correctly. By adding the `holmesgpt.dev/rerun: "true"` annotation to your HealthCheck manifest, the operator will **re-run the check on every deploy** — no manual intervention needed.

## How It Works

The `holmesgpt.dev/rerun` annotation creates an automatic toggle:

1. Your manifest includes `holmesgpt.dev/rerun: "true"`
2. On `kubectl apply`, the operator runs the check and **clears the annotation**
3. On the next deploy, `kubectl apply` sees the annotation is missing (operator cleared it) but the manifest says it should be `"true"` → restores it → triggers re-run
4. Repeat forever

This means every `helm upgrade` or ArgoCD sync automatically triggers a fresh health check.

## Example: HealthCheck Alongside a Deployment

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
  name: checkout-api-deploy-check
  namespace: production
  annotations:
    holmesgpt.dev/rerun: "true"
  labels:
    app: checkout-api
spec:
  query: "We just rolled out a new version of checkout-api to production. Is the deployment healthy? Check logs, error rates, latency, and resource usage before vs after the deploy and flag any regressions."
  timeout: 120
  mode: alert
  destinations:
    - type: slack
      config:
        channel: "#deploy-alerts"
```

```bash
kubectl apply -f app-deployment.yaml
```

## Helm

Add a HealthCheck template to your application's Helm chart:

```yaml
# templates/healthcheck.yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: {{ include "mychart.fullname" . }}-deploy-check
  namespace: {{ .Release.Namespace }}
  annotations:
    holmesgpt.dev/rerun: "true"
  labels:
    {{- include "mychart.labels" . | nindent 4 }}
spec:
  query: "Is the {{ include "mychart.fullname" . }} deployment in '{{ .Release.Namespace }}' fully rolled out and healthy? Check pod status, logs, and error rates."
  timeout: 120
  mode: alert
  destinations:
    - type: slack
      config:
        channel: "#deploy-alerts"
```

Every `helm upgrade` triggers a fresh check.

## ArgoCD

Add a HealthCheck to the same repo/path as your Application source. Every ArgoCD sync triggers a re-run:

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: checkout-api-deploy-check
  namespace: production
  annotations:
    holmesgpt.dev/rerun: "true"
spec:
  query: "Is the checkout-api deployment in 'production' fully rolled out and healthy? Check pod status, logs, and error rates."
  timeout: 120
  mode: alert
  destinations:
    - type: slack
      config:
        channel: "#deploy-alerts"
```

## Gating CI/CD on the Result

After applying, poll for the result to gate your pipeline:

```bash
# Wait for the check to complete, then read the result
for i in $(seq 1 30); do
  RESULT=$(kubectl get hc checkout-api-deploy-check -n production -o jsonpath='{.status.result}' 2>/dev/null)
  if [ "$RESULT" = "pass" ]; then
    echo "Deploy verified healthy"
    exit 0
  elif [ "$RESULT" = "fail" ] || [ "$RESULT" = "error" ]; then
    echo "Deploy check failed:"
    kubectl get hc checkout-api-deploy-check -n production -o jsonpath='{.status.message}'
    exit 1
  fi
  sleep 10
done
echo "Timed out waiting for health check"
exit 1
```

## Ongoing Monitoring with ScheduledHealthCheck

One-time deploy checks catch immediate failures, but some problems only appear later — memory leaks, connection pool exhaustion, gradual performance degradation. [Scheduled Health Checks](scheduled-health-checks.md) run on a cron schedule to catch these regressions automatically.

## Tips

- **Set a longer timeout** (60–120s) to give the rollout time to complete before Holmes evaluates.
- **Use labels** to query checks for a specific app: `kubectl get hc -l app=checkout-api`.
- **Combine with ArgoCD**: The query can reference sync status — e.g., *"Is the ArgoCD application 'checkout-api' synced and healthy with no degraded resources?"* — since Holmes has access to the [ArgoCD toolset](../data-sources/builtin-toolsets/argocd.md).

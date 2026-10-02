# Deployment Verification

Verifying that a new version is healthy right after it ships is one of the most common
uses of the operator. There are two ways to do it, and they compose:

- **Automatically, on every rollout** — declare one [TriggeredHealthCheck](triggered-health-checks.md)
  and Holmes investigates *every* future rollout of the service, no matter how it was
  triggered (CI, GitOps/Argo sync, `kubectl set image`, or a rollback). This is the
  recommended default — declare once, no per-deploy wiring.
- **Inline, to gate a pipeline** — include a one-time [HealthCheck](health-checks.md) in
  the deploy manifest (or CI/CD step) and block the pipeline on its result. Use this when
  CI must wait synchronously for the verdict before proceeding.

A typical setup uses both: a `TriggeredHealthCheck` for hands-off coverage of all
rollouts, plus an inline `HealthCheck` in the specific pipeline stage where you want a
hard gate.

## Automatic verification with TriggeredHealthCheck

Apply this once. From then on, any rollout of a Deployment matching the selector
automatically spawns a check — including deploys you didn't make through CI.

```yaml
# verify-checkout-deploys.yaml — apply once, verifies every future rollout
apiVersion: holmesgpt.dev/v1alpha1
kind: TriggeredHealthCheck
metadata:
  name: verify-checkout-deploys
  namespace: production
spec:
  deploymentRollout:
    selector:
      matchLabels:
        app: checkout-api
  delaySeconds: 300     # wait 5m after the rollout, then check (default)
  query: |
    checkout-api was just rolled out to {{ .new.image }} (previously {{ .old.image }}).
    Is the new version healthy? Compare error rates, latency, restarts, and logs
    before vs after the rollout and flag any regressions.
  timeout: 120
  mode: alert
  destinations:
    - type: slack
      config:
        channel: "#deploy-alerts"
```

```bash
kubectl apply -f verify-checkout-deploys.yaml

# After a deploy, see the check it produced and the verdict
kubectl get hc -n production -l holmesgpt.dev/triggered-by=verify-checkout-deploys
kubectl describe thc verify-checkout-deploys -n production
```

The `{{ .new.image }}` / `{{ .old.image }}` tokens are substituted with the rollout's
before/after images, so the investigation knows exactly what changed. See
[Triggered Health Checks](triggered-health-checks.md) for the full field reference and
[how long the check waits](triggered-health-checks.md#how-long-to-wait) after a rollout.

!!! tip "Catch slow-burn regressions too"

    Some problems (memory leaks, connection-pool exhaustion) only appear after the new
    version has run for a while. Add a second trigger with a delay — e.g.
    `delaySeconds: 86400` — to re-investigate the same rollout a day later, or use a
    [ScheduledHealthCheck](scheduled-health-checks.md) for continuous coverage.

## Gating CI/CD with an inline HealthCheck

When a pipeline must **wait for the verdict** before promoting a release, include a
one-time `HealthCheck` in the same manifest as your deployment. It runs immediately after
`kubectl apply` and reports whether the new version started correctly.

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

Then poll for the result to gate the pipeline:

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

If pods crash or fail readiness, the check fails and the pipeline stops.

## When to use which

| | TriggeredHealthCheck | Inline HealthCheck |
|---|---|---|
| Runs on | *Every* rollout, automatically | Only when you apply it |
| Setup | Declare once per service | Added to each deploy manifest/step |
| Covers out-of-band deploys (`kubectl set image`, GitOps, rollback) | Yes | No |
| Blocks a CI/CD pipeline | No (fire-and-forget) | Yes (poll the result to gate) |

## Tips

- **Version the inline check name** (e.g., `checkout-api-deploy-v2-4-1`) so each deploy
  creates a distinct resource and you keep an audit trail. This applies to one-time
  `HealthCheck` resources only — `TriggeredHealthCheck` and `ScheduledHealthCheck` use a
  fixed name and create child HealthChecks automatically.
- **Set a longer timeout** (60–120s) to give the investigation time to gather data.
- **Use labels** like `deploy-version` to query checks for a specific release:
  `kubectl get hc -l deploy-version=v2.4.1`.
- **Combine with ArgoCD**: the query can reference sync status — e.g., *"Is the ArgoCD
  application 'checkout-api' synced and healthy with no degraded resources?"* — since
  Holmes has access to the [ArgoCD toolset](../data-sources/builtin-toolsets/argocd.md).
</content>

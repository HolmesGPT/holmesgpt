# Triggered Health Checks

A `TriggeredHealthCheck` runs an investigation **automatically when a Deployment rolls
out a new version** — no per-deploy wiring, no CI polling. Declare it once, and every
rollout of a matching Deployment (from CI, Argo, `kubectl set image`, or a rollback)
fires a check.

It is the event-driven sibling of the [ScheduledHealthCheck](scheduled-health-checks.md):
both are self-contained (they embed the check definition inline) and both spawn a
[HealthCheck](health-checks.md) per run, which becomes the execution record.

!!! info "Alpha"

    `TriggeredHealthCheck` currently supports a single trigger type — `deploymentRollout`.
    More event sources (pod crashloops, failed Jobs, alerts) are planned.

## How it works

1. The operator watches Deployments in namespaces where `TriggeredHealthCheck`
   resources exist.
2. When a matching Deployment's **pod template changes** (a rollout), the operator waits
   `delaySeconds` (default 5 minutes) and then runs the check. The wait gives the rollout
   time to finish and gives any crashes or errors time to show up.
3. It creates a `HealthCheck` (owned by the trigger) with your query, having
   substituted the rollout context into it.
4. Holmes investigates using every connected data source; in `alert` mode it notifies
   your [destinations](destinations.md) on failure.

## Example

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: TriggeredHealthCheck
metadata:
  name: verify-checkout-rollouts
  namespace: production
spec:
  deploymentRollout:
    selector:
      matchLabels:
        app: checkout-api
  delaySeconds: 300         # wait 5m after the rollout, then check (default)
  cooldownSeconds: 600      # don't re-fire for the same Deployment within 10m
  query: |
    checkout-api was rolled out to {{ .new.image }} (was {{ .old.image }}).
    Compare error rates, latency, restarts, and logs before vs after the rollout
    and flag any regressions.
  timeout: 120
  mode: alert
  destinations:
    - type: slack
      config:
        channel: "#deploy-alerts"
```

Apply it once:

```bash
kubectl apply -f triggeredhealthcheck.yaml

# List triggers (short name: thc)
kubectl get thc

# See fire history and the HealthChecks each rollout produced
kubectl describe thc verify-checkout-rollouts
kubectl get hc -l holmesgpt.dev/triggered-by=verify-checkout-rollouts
```

## Query and rollout context

The rollout facts are **always** prepended to the query the check runs, so even a terse
query like `"Is the new version healthy?"` gives the model what it needs:

```
This health check was triggered automatically by a Kubernetes Deployment rollout. Use this context when investigating:
- Deployment: checkout-api
- Namespace: production
- Previous image(s): myregistry/checkout-api:v2.4.0
- New image(s): myregistry/checkout-api:v2.4.1

<your query>
```

You can also reference the same facts inline in your `query` with these tokens:

| Token | Replaced with |
|-------|---------------|
| `{{ .deployment }}` | Name of the Deployment that rolled out |
| `{{ .namespace }}` | Its namespace |
| `{{ .old.image }}` | Container image(s) before the rollout |
| `{{ .new.image }}` | Container image(s) after the rollout |

## Spec reference

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `true` | Whether the trigger is active |
| `deploymentRollout.selector.matchLabels` | `{}` | Deployment labels that must all match. **Empty matches every Deployment in the namespace.** |
| `delaySeconds` | `300` | How long to wait after a rollout before running the check. Default 5 minutes; `0` checks immediately; `86400` checks a day later (max 7 days). See [How long to wait](#how-long-to-wait). |
| `cooldownSeconds` | `0` | Suppress re-firing for the same Deployment within this window. `0` disables. |
| `query` | — | Natural-language investigation (supports the tokens above). Required. |
| `timeout` | `120` | Check execution timeout in seconds. |
| `mode` | `monitor` | `alert` notifies destinations on failure; `monitor` only records the result. |
| `model` | — | Override the default LLM model for this check. |
| `destinations` | `[]` | Alert destinations (used in `alert` mode). See [Destinations](destinations.md). |

## How long to wait

There is one knob: **`delaySeconds`** — how long to wait after a rollout before running
the check. That's it.

The check does not run the instant a new version is deployed, because a brand-new
rollout hasn't finished and problems haven't surfaced yet. So Holmes waits `delaySeconds`
first, then looks. Pick a value that fits what you want to catch:

- **`300` (default, 5 minutes)** — enough time for the rollout to finish and for crashes
  or startup errors to appear.
- **`0`** — check right away (useful if you only care that the deploy was accepted).
- **`86400` (a day)** — catch slower problems like memory leaks or resource creep that
  only show up after the version has been running a while.

If you set the wait too short for your app's rollout, the check may run while pods are
still starting — Holmes will simply report that the rollout hasn't finished yet.

The wait is saved on the resource, so it still completes if the operator restarts — even
a day-long wait. If the same Deployment is rolled out again while a check is still
waiting, the waiting check is replaced so only the newest version is checked.

A common setup is one trigger with the default 5-minute wait, plus a second with
`delaySeconds: 86400` to re-check the same rollout a day later.

## Notes & limitations

- **Rollout = pod-template change.** Scaling and HPA changes (which only touch
  `spec.replicas`) do **not** fire the trigger; only changes to the pod template do.
- **Restart behavior.** A check that was *already scheduled* before a restart still runs
  (the pending fire is persisted in `status.pending`). *Detecting* new rollouts, however,
  relies on an in-memory baseline of each Deployment's last-seen pod template (the operator
  does not annotate your Deployments). After a restart the first observation of each
  Deployment just re-establishes that baseline, so a rollout that happens *during* the
  restart window is not detected. Use a
  [ScheduledHealthCheck](scheduled-health-checks.md) for continuous coverage.
- **Cost.** Every fire is at least one LLM call. Use `cooldownSeconds` and a specific
  `selector` to bound spend on busy namespaces.

## Next Steps

- **[Deployment Verification](deployment-verification.md)** — patterns for gating and
  verifying deploys
- **[Health Checks](health-checks.md)** — the one-time checks this spawns
- **[Alert Destinations](destinations.md)** — Slack and PagerDuty configuration
</content>

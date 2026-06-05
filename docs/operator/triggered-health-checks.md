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
2. When a matching Deployment's **pod template changes** (a rollout), the operator
   waits for the rollout to settle (up to `settleTimeout`).
3. It then creates a `HealthCheck` (owned by the trigger) with your query, having
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
  settleTimeout: 300        # wait up to 5m for the rollout to finish
  delaySeconds: 0           # extra wait before running (e.g. 86400 = a day later)
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

## Query tokens

The `query` is templated with the rollout context before the check runs:

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
| `settleTimeout` | `300` | Max seconds to wait for the rollout to *finish deploying* before running the check. `0` runs immediately. If the rollout doesn't settle in time, the check runs anyway (so a stuck rollout still gets investigated). |
| `delaySeconds` | `0` | A *deliberate* wait before running the check, measured from when the rollout is detected — for catching slow-burn problems (memory leaks, connection-pool exhaustion) that only show up after the app has run a while. e.g. `86400` runs the check a day after the rollout. Max 7 days. |
| `cooldownSeconds` | `0` | Suppress re-firing for the same Deployment within this window. `0` disables. |
| `query` | — | Natural-language investigation (supports the tokens above). Required. |
| `timeout` | `120` | Check execution timeout in seconds. |
| `mode` | `monitor` | `alert` notifies destinations on failure; `monitor` only records the result. |
| `model` | — | Override the default LLM model for this check. |
| `destinations` | `[]` | Alert destinations (used in `alert` mode). See [Destinations](destinations.md). |

## `settleTimeout` vs `delaySeconds`

These solve different problems and compose:

- **`settleTimeout`** answers *"is the new version broken on arrival?"* — it waits for the
  rollout to become available, then checks immediately. Catches crash loops, bad images,
  failed readiness.
- **`delaySeconds`** answers *"did the new version degrade after running for a while?"* —
  it waits a deliberate period (minutes to days) before checking. Catches memory leaks,
  connection-pool exhaustion, slow resource creep.

Run both by setting both: the rollout settles, then the deliberate delay elapses, then
the check runs. A common pattern is one immediate trigger plus one with
`delaySeconds: 86400` for next-day verification.

Delayed checks are persisted in `status.pending` and reconciled by the operator, so a
scheduled fire **survives an operator restart** — even a day-long delay will still run.
If a newer rollout of the same Deployment happens while a check is pending, the pending
entry is debounced (replaced) so only the latest state is checked.

## Notes & limitations

- **Rollout = pod-template change.** Scaling and HPA changes (which only touch
  `spec.replicas`) do **not** fire the trigger; only changes to the pod template do.
- **Restart behavior.** The operator tracks the last-seen template in memory (it does
  not annotate your Deployments). After an operator restart, the first observation of
  each Deployment re-establishes a baseline and is not treated as a rollout — so a
  rollout that happens during the restart window may be missed. Use a
  [ScheduledHealthCheck](scheduled-health-checks.md) for continuous coverage.
- **Cost.** Every fire is at least one LLM call. Use `cooldownSeconds` and a specific
  `selector` to bound spend on busy namespaces.

## Next Steps

- **[Deployment Verification](deployment-verification.md)** — patterns for gating and
  verifying deploys
- **[Health Checks](health-checks.md)** — the one-time checks this spawns
- **[Alert Destinations](destinations.md)** — Slack and PagerDuty configuration
</content>

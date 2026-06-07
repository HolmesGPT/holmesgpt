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
2. When a matching Deployment's **pod template changes** (a rollout), the check is
   scheduled to run after `delaySeconds` (a fixed soak, default 5 minutes). When that
   time arrives, the operator confirms the rollout has actually finished — waiting up to
   `settleTimeout` for the new pods to become Available — and then runs the check. See
   [When the check runs](#when-the-check-runs) for the exact timeline.
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
  delaySeconds: 300         # soak: wait 5m after the rollout before checking (default)
  settleTimeout: 300        # then wait up to 5m more for the rollout to be fully Available
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
| `delaySeconds` | `300` | **Fixed soak** before the check runs, measured from when the rollout is detected. Always waits the full duration. This is the main "how long after the deploy should I look?" knob — default 5 minutes; `0` checks immediately; `86400` checks a day later (max 7 days). Persisted, so it survives an operator restart. See [When the check runs](#when-the-check-runs). |
| `settleTimeout` | `300` | **Maximum** time to wait, just before running, for the rollout to become fully Available. Unlike `delaySeconds` this ends *as soon as* the rollout is ready — it is only a cap. If the rollout never settles, the operator stops waiting at the cap and runs the check anyway (so a stuck rollout is still investigated). `0` skips this wait. |
| `cooldownSeconds` | `0` | Suppress re-firing for the same Deployment within this window. `0` disables. |
| `query` | — | Natural-language investigation (supports the tokens above). Required. |
| `timeout` | `120` | Check execution timeout in seconds. |
| `mode` | `monitor` | `alert` notifies destinations on failure; `monitor` only records the result. |
| `model` | — | Override the default LLM model for this check. |
| `destinations` | `[]` | Alert destinations (used in `alert` mode). See [Destinations](destinations.md). |

## When the check runs

A rollout does **not** run the check immediately. Two waits happen, in order, then the
check runs:

```
rollout detected ──► wait delaySeconds ──► wait for rollout to be Available ──► check runs
                     (fixed soak)          (up to settleTimeout)
```

1. **`delaySeconds` — the soak.** A fixed wait from the moment the rollout is detected;
   it always waits the full time. This is the knob you reach for most. It is persisted to
   `status.pending`, so the scheduled run survives an operator restart — even a multi-day
   soak still fires.
2. **`settleTimeout` — the settle guard.** Just before running, the operator waits for the
   new pods to be fully rolled out and Available. This wait **ends the moment the rollout
   is ready** — `settleTimeout` is only the ceiling. If the rollout is stuck, the operator
   stops at the ceiling and runs the check anyway.

`delaySeconds` and `settleTimeout` are easy to confuse because both are "waits in seconds",
but they behave differently:

| | `delaySeconds` (soak) | `settleTimeout` (settle guard) |
|---|---|---|
| Kind of wait | Fixed timer | Ceiling on waiting for a condition |
| Ends early? | No — always waits the full time | Yes — ends as soon as the rollout is Available |
| What `0` means | No soak; go straight to the guard | Skip the guard; run the check now |
| Tends to catch | Slow-burn issues (leaks, pool exhaustion, creep) | Broken-on-arrival issues (crash loops, bad image) |

Because the default soak is 5 minutes, the rollout has almost always finished by the time
the settle guard runs, so the guard usually passes instantly. It does real work mainly when
you shorten the soak (e.g. `delaySeconds: 0` to check the instant the rollout is ready).

**Example timings** (rollout detected at time `T`):

- **Defaults** (`delaySeconds: 300`, `settleTimeout: 300`) — check runs at about `T + 5m`.
- **Immediate** (`delaySeconds: 0`, `settleTimeout: 300`) — check runs as soon as the
  rollout is Available, or at `T + 5m` if it never settles.
- **Next-day** (`delaySeconds: 86400`) — check runs about a day after the rollout.

A common setup is one trigger with the default soak plus a second with `delaySeconds: 86400`
for next-day verification.

If a newer rollout of the same Deployment arrives while a check is still pending, the
pending entry is replaced (debounced), so only the latest version is checked.

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

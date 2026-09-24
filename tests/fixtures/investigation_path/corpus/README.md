# Investigation path corpus

A small set of **resolved** incidents used to measure path-completeness retrieval
offline. See `docs/design/2026-08-24_investigation-path-completeness.md` for what
the fields mean and how the metrics are computed.

This corpus is deliberately tiny. It exists to make the metrics runnable and the
schema concrete, not to prove that retrieval works. Numbers produced from twelve
incidents are a smoke test, not evidence.

## Rules for adding an incident

1. **A human must have resolved it and reviewed the record.** Set `validated_by`
   to that person and `validated_at` to the review date. Never generate entries
   from production traffic automatically — an unreviewed path teaches the
   validator whatever mistake the original investigation made.
2. **`root_cause.label` comes from the controlled vocabulary** (see below). Add a
   new label only when no existing one fits, and say so in the pull request.
3. **`reference_path` is what *should* have been checked**, decided after the
   cause was known — not a transcript of what was actually run.
4. **Weights are honest.** `1.0` means skipping the check would have hidden the
   cause. `0.3` means it was mildly useful. Inflated weights make recall look
   good and mean nothing.
5. **The rationale must be true of *this* cause.** The corpus is the ground
   truth, so a plausible-sounding wrong reason is not cosmetic — it teaches
   retrieval to recommend a check that cannot show what the record claims.
   INC-002 got this wrong: it is a NetworkPolicy incident, which the CNI
   enforces while the Service and its endpoints stay perfectly healthy, yet it
   claimed empty endpoints as the identifying evidence. Reading the check
   against the cause, and asking whether it could actually produce that
   evidence, is worth doing before adding a record.
6. **Refer to the affected workload as `<subject>`**, never by its real name.
   A path that names `payment-service` can only ever match `payment-service`.
   Shared infrastructure (`redis`, a node, a metric) keeps its real name.
7. **Nothing sensitive.** No customer names, no hostnames outside the cluster, no
   URLs, no identifiers that could re-identify a real tenant. Records here are
   redacted by construction: the schema has nowhere to put tool output.

## Splits

- `split: corpus` — the retrieval pool. Also the only data the confidence
  calibration is fitted on, via leave-one-out.
- `split: holdout` — evaluation cases. These also carry an `observed_path`;
  `reference_path` minus `observed_path` is the ground-truth missing set.

A holdout incident must never appear in the pool, and the two must not describe
the same underlying event.

A root cause needs **at least three pool incidents** to be usable. Leave-one-out
calibration removes one and needs two left for retrieval to answer at all, so a
cause with fewer contributes nothing and can never be answered on.

Today only `dependency_unreachable` (3), `oom_kill` (3) and `config_regression`
(3) clear that bar. `connection_pool_exhausted`, `image_pull_failure` and
`node_disk_pressure` have one each and are dead weight — the most useful thing
anyone can add to this corpus is two more incidents for each of those.

The three `dependency_unreachable` incidents are three *different* mechanisms —
a selector edit, a NetworkPolicy and a wrong port name — which share a label but
not a reference path. What they do share is that traffic genuinely could not
reach the dependency. That is the whole content of the label, and it is the test
for whether a new record belongs under it: INC-003 was filed here while its own
summary said the database stayed reachable, which is a different failure wearing
the same symptoms.

The held-out set must include cases the method gets **wrong**. `HOLD-005` is
there for that reason: it matches the cache incidents on both symptoms and root
cause, but its dependency is a message broker. A held-out set made only of cases
the method handles cannot measure precision or calibration at all.

## Root cause vocabulary

| label | meaning |
|---|---|
| `dependency_unreachable` | The workload could not reach a service it depends on |
| `connection_pool_exhausted` | The dependency was reachable, but the client had no free connection |
| `oom_kill` | A container exceeded its memory limit and was killed |
| `image_pull_failure` | The container image could not be pulled |
| `node_disk_pressure` | A node ran out of disk and evicted workloads |
| `config_regression` | A configuration or deployment change broke the workload |
| `certificate_expired` | A TLS certificate expired |

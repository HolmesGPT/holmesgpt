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
5. **Refer to the affected workload as `<subject>`**, never by its real name.
   A path that names `payment-service` can only ever match `payment-service`.
   Shared infrastructure (`redis`, a node, a metric) keeps its real name.
6. **Nothing sensitive.** No customer names, no hostnames outside the cluster, no
   URLs, no identifiers that could re-identify a real tenant. Records here are
   redacted by construction: the schema has nowhere to put tool output.

## Splits

- `split: corpus` — the retrieval pool.
- `split: holdout` — evaluation cases. These also carry an `observed_path`;
  `reference_path` minus `observed_path` is the ground-truth missing set.

A holdout incident must never appear in the pool, and the two must not describe
the same underlying event.

## Root cause vocabulary

| label | meaning |
|---|---|
| `dependency_unreachable` | The workload could not reach a service it depends on |
| `oom_kill` | A container exceeded its memory limit and was killed |
| `image_pull_failure` | The container image could not be pulled |
| `node_disk_pressure` | A node ran out of disk and evicted workloads |
| `config_regression` | A configuration or deployment change broke the workload |
| `certificate_expired` | A TLS certificate expired |

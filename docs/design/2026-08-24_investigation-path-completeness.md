# Investigation path completeness — schema, corpus and metrics

Status: **offline only.** Nothing here is wired into an investigation. This is
the benchmark groundwork for
[issue #2046](https://github.com/HolmesGPT/holmesgpt/issues/2046), so that a
retrieval policy can be measured before it is allowed to talk to a user.

The idea in the issue is: after an investigation, compare what it checked
against what similar resolved incidents checked, and point out anything it
skipped. The risk is that a wrong suggestion during an incident costs a
responder attention at the worst possible moment, and that borrowing one
historical path teaches every future investigation to repeat it. So the first
question is not "does it work" but "how would we know".

Three artifacts answer that: a **path schema**, a **corpus**, and a **metrics
definition** with a runnable offline eval.

## 1. Canonical path event

An investigation becomes an ordered list of `PathEvent` (see
`holmes/core/investigation_path/schema.py`):

| field | purpose |
|---|---|
| `ordinal` | position in the investigation, so order is preserved |
| `tool` | tool as executed — provenance only, never matched on |
| `intent` | why the check ran: `describe`, `logs`, `metrics`, `topology`, … |
| `entity` | normalized kind / name / namespace of what was checked |
| `time_window` | bucketed lookback, or a point-in-time marker |
| `outcome` | `success` / `no_data` / `error`, plus a normalized `error_class` |
| `evidence_ref` | opaque pointer to the output, never a copy of it |

Two decisions carry most of the weight.

**Matching is on intent, not tool.** `kubectl logs`, `fetch_pod_logs` and a Loki
query are all `LOGS`. Matching on tool name would tell an investigation it
skipped a check it actually ran through a different toolset.

**The affected workload is stored as `<subject>`.** A reference path that names
`payment-service` can only ever match `payment-service`. The token is
substituted in when a live path is compared, and rendered back to the real name
when a suggestion is shown.

Signatures come at two granularities. `COARSE` is `intent:kind` and asks "did
anyone look at the service topology at all". `FINE` adds the entity name and
asks "did anyone look at `service/redis`". Both are scored, because they trade
recall against precision differently.

### What is deliberately not stored

- **Tool output.** `StructuredToolResult.data` is never read.
- **Raw error text.** It is read once to pick an `ErrorClass`, then dropped.
- **Anything not on the parameter allow-list.** A new toolset cannot leak a
  parameter into a stored path just by naming it something new.
- **Credentials.** Parameter keys containing `token`, `secret`, `password`,
  `auth` and similar are skipped even if allow-listed, and high-entropy words
  inside shell commands are replaced with `<redacted>`.
- **Unstable values.** Pod hashes are stripped, lookbacks are bucketed to
  5m/15m/1h/6h/24h/7d, and absolute timestamps are reduced to a span.

`tests/core/investigation_path/test_normalize.py` asserts each of these
directly, including that a raw error string never survives into the record.

### Suggestions that do not transfer

Two incidents can share symptoms *and* a root cause while depending on entirely
different services. The held-out `HOLD-005` is exactly that: a crash loop on
refused connections, root cause `dependency_unreachable`, but the dependency is
a message broker rather than Redis. Every matching incident in the pool names
Redis, so the first version of the validator produced four confident
suggestions telling the responder to go and check a service that does not exist
in that cluster, and found none of the three checks actually skipped.

The validator therefore drops any suggestion naming an object the current
investigation has never seen. Two carve-outs:

- Checks against the subject (`<subject>`) always transfer.
- Metric and trace names normally transfer, since
  `container_memory_working_set_bytes` means the same thing everywhere — unless
  the name is built out of a foreign object's name. `redis_connected_clients` is
  nominally a metric but only exists where Redis does, so it is dropped too.
  Matching is on whole tokens, so a pod named `mem` cannot veto
  `node_memory_MemAvailable_bytes`.

This is a mitigation, not a solution. It stops the wrong advice; it does not
produce the right advice. `HOLD-005` now yields nothing at all.

## 2. Corpus

`tests/fixtures/investigation_path/corpus/` holds seventeen resolved incidents,
one YAML file each — twelve in the retrieval pool, five held out.

Every record carries a `root_cause` label from a controlled vocabulary, a
`reference_path` (what *should* have been checked, decided once the cause was
known) with per-step weights and a written rationale, and `validated_by` /
`validated_at`. Held-out records additionally carry an `observed_path`;
`reference_path` minus `observed_path` is the ground-truth missing set.

Records are human-reviewed by construction. Harvesting paths from production
automatically would teach the validator whatever mistake each original
investigation made, which is the exact failure this design is trying to avoid.

**Seventeen incidents is not evidence.** The corpus exists to make the schema
concrete and the metrics runnable. It is far too small to justify shipping
anything.

A root cause needs at least three pool members to be usable at all: leave-one-out
removes one incident and retrieval needs two remaining to answer. Three of the
five causes currently clear that bar:

| root cause | pool incidents | usable |
|---|---|---|
| `dependency_unreachable` | 4 | yes |
| `oom_kill` | 3 | yes |
| `config_regression` | 3 | yes |
| `image_pull_failure` | 1 | **no** |
| `node_disk_pressure` | 1 | **no** |

The last two are dead weight today. They can never be answered on, and they
contribute nothing to the calibration fit. They are kept because the schema and
the vocabulary should cover more than the three causes that happen to be
populated — but no result in this document is evidence about them.

### Similar symptoms vs. same root cause

These are kept apart on purpose. Retrieval ranks by symptom overlap, because
symptoms are all that is known while an incident is open. But the checks worth
suggesting come from the *cause*. So candidates are collapsed to the single root
cause most of them share, and everything that disagrees is dropped. If they do
not agree, there is no defensible cause to borrow from.

### Abstention

Abstention is an outcome, not a failure. The policy declines when any of these
hold, and records which one:

| reason | condition |
|---|---|
| `no_candidates` | nothing in the corpus overlaps at all |
| `low_similarity` | the closest incidents fall below the similarity floor |
| `insufficient_support` | fewer than `min_matches` incidents share the cause |
| `root_cause_disagreement` | candidates point at different causes |

Surviving retrieval is not enough for a check to be suggested. It must also be
run by at least `min_support_ratio` (default 0.6) of the matched incidents. A
check that only one incident out of three ran is that incident's particular
circumstance, not a property of the root cause — this filter was the single
largest source of false positives on the first benchmark run.

## 3. Confidence calibration

The raw evidence score is a product of four terms below 1 (symptom similarity,
root-cause agreement, match support, per-check support). It orders suggestions
sensibly but its magnitude is meaningless: on the first run it read about 0.32
for suggestions that turned out correct every single time, an expected
calibration error of 0.50. A number like that is worse than no number, because a
responder reads "32%" and discounts a check that is almost certainly worth
running.

`calibration.py` fits a **Platt scaling** map — a two-parameter logistic — from
raw score to probability. Two parameters rather than something more flexible
because the corpus is tiny and anything with more freedom would memorise it.

Training data comes from **leave-one-out over the retrieval pool only**. Each
pool incident is held out in turn, one reference check is removed to simulate an
investigation that skipped it, and the validator runs against the remaining
pool. Whether each suggestion was the removed check gives the label. The
held-out split is never touched during fitting, so the calibration error
measured on it is out-of-sample. On the current corpus this yields 80 training
samples, 44 of them positive.

Three details that were not optional:

- **Platt's target smoothing** (fit towards `(N+1)/(N+2)` and `1/(N+2)` rather
  than hard 1 and 0), or a separable sample drives the slope towards infinity
  and the model claims 0.999 confidence from a few dozen examples.
- **Standardizing the input.** Raw scores occupy a narrow band, roughly 0.2 to
  0.5. An L2 penalty applied at that scale swamps the data term: the first
  attempt converged to a slope of 2.74 and predicted 0.65 for a bucket whose
  observed hit rate was 1.00. Standardizing makes the penalty mean the same
  thing regardless of how the raw score happens to be scaled, so changing the
  score formula later cannot quietly under- or over-regularize the fit.
- **Cross-validating the penalty strength** rather than hardcoding it. A
  constant tuned until the numbers looked good would be fitting the benchmark.

The fit refuses to run — and falls back to passing the raw score through — when
there are fewer than two samples, only one class present, or no variation in the
score. Each of those would restate the training prior while looking like a
measurement.

## 4. Metrics

Defined in `holmes/core/investigation_path/metrics.py`. For a held-out incident
with ground-truth missing set `M` (weighted) and suggestion set `S`:

- **Weighted path recall** — share of `M`'s weight found in `S`, over *all*
  cases. Abstentions contribute zero, so this is the coverage number.
- **Weighted path recall when answering** — the same over answered cases only.
  Reported alongside recall because the two move in opposite directions as the
  abstention threshold changes, and neither means anything alone.
- **Suggestion precision** — share of suggestions that were genuinely missing.
  The weighted form credits a true positive by its weight and charges a false
  positive a flat 1.0, so surfacing a trivial check cannot pay for a wrong one.
- **False-positive burden** — mean wrong suggestions per answered incident.
  Precision hides this: 80% over 25 suggestions is far worse than 80% over 4.
- **Abstention rate** — with a breakdown by reason.
- **Expected calibration error / Brier score** — whether the confidence attached
  to a suggestion means anything. This is the check a raw top-k similarity score
  cannot pass on its own.
- **Latency** — p50/p95 per validation.
- **Storage cost** — mean serialized bytes per stored record.
- **LLM calls** — expected to be 0, tracked so a future model call shows up as a
  cost regression rather than silently.

### Running it

```bash
poetry run python -m holmes.core.investigation_path.offline_eval
```

## Current baseline

```
cases                       5
answered                    4
abstention rate             0.20
weighted path recall        0.69
  ... when answering        0.75
suggestion precision        1.00
  ... weighted              1.00
false positives per answer  0.00
expected calibration error  0.050
brier score                 0.000
latency p50 (ms)            0.05
latency p95 (ms)            0.12
bytes per incident          1451
llm calls                   0

calibration: platt(slope=2.20, intercept=0.35, l2=0.01)
             fitted on 80 leave-one-out samples, 44 positive
  calibration error before: 0.350   after: 0.050
  brier before:             0.136   after: 0.000
```

Read honestly, on five held-out cases:

- **Nothing it says is wrong.** Precision 1.00, zero false positives per answer.
  Getting there took two fixes, both of which came out of the benchmark rather
  than out of review: the support-ratio filter, and refusing to name objects the
  investigation has never seen.
- **Confidence now means something.** Calibration error fell from 0.350 to 0.050
  out-of-sample.
- **It is free.** Tens of microseconds, no tokens, ~1.4 KB per stored incident.
- **It misses a third of what was skipped.** Coverage is 0.69 against 1.00
  accuracy-when-it-speaks, and that gap is the whole story: it stays silent
  rather than guessing. `HOLD-004` abstains because nothing resembles it, and
  `HOLD-005` produces nothing because every candidate check named a service that
  is not in that cluster.
- **`HOLD-005` is an unsolved case, not a solved one.** The validator avoids
  saying something wrong, which is the right call, but a responder gets no help
  at all on an incident where three checks really were skipped.

Precision of 1.00 and a Brier score of 0.000 on nine suggestions across five
cases should be read as "no detectable problem at this sample size", not as
"solved". With this little data those numbers have very wide error bars.

## What this does not answer

- Whether any of it holds beyond seventeen hand-written incidents. Precision
  should be expected to fall once near-miss root causes enter the corpus.
- Whether symptom keyword overlap is the right retrieval signal at all. It was
  chosen because it is transparent and free, not because it beat an alternative.
- How to transfer a dependency-specific check to an incident with a *different*
  dependency — the `HOLD-005` gap. A `<dependency>` role token alongside
  `<subject>` is the obvious idea, but it needs a way to work out what the
  current incident's dependency is, which nothing here does.
- How incidents would be collected and reviewed at any real scale.
- Whether a responder mid-incident finds the suggestions useful or noisy. No
  metric here measures that, and no offline metric can.

## Suggested next steps

1. **Grow the corpus** across more causes and more clusters, and re-run. This is
   the highest-value next step by a wide margin; every number above is limited
   by sample size before it is limited by method.
2. **Close the `HOLD-005` gap** with a dependency role token, so a check learned
   against Redis can be suggested against a broker.
3. **Sweep the policy knobs** (`min_symptom_similarity`, `min_matches`,
   `min_root_cause_agreement`, `min_support_ratio`) and publish the
   risk-coverage curve rather than a single operating point.
4. **Only then** consider a runtime surface, off by default, showing provenance
   and rationale, and worded as advice rather than a checklist.

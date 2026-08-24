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

## 2. Corpus

`tests/fixtures/investigation_path/corpus/` holds twelve resolved incidents, one
YAML file each — eight in the retrieval pool, four held out.

Every record carries a `root_cause` label from a controlled vocabulary, a
`reference_path` (what *should* have been checked, decided once the cause was
known) with per-step weights and a written rationale, and `validated_by` /
`validated_at`. Held-out records additionally carry an `observed_path`;
`reference_path` minus `observed_path` is the ground-truth missing set.

Records are human-reviewed by construction. Harvesting paths from production
automatically would teach the validator whatever mistake each original
investigation made, which is the exact failure this design is trying to avoid.

**Twelve incidents is not evidence.** The corpus exists to make the schema
concrete and the metrics runnable. It is far too small to justify shipping
anything.

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

## 3. Metrics

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
cases                       4
answered                    2
abstention rate             0.50
weighted path recall        0.67
  ... when answering        1.00
suggestion precision        0.75
  ... weighted              0.73
false positives per answer  1.00
expected calibration error  0.500
brier score                 0.303
latency p50 (ms)            0.03
latency p95 (ms)            0.08
bytes per incident          1450
llm calls                   0
```

Read honestly, on four held-out cases:

- When it answers, it finds everything that was skipped, and it is free —
  microseconds, no tokens.
- It is wrong once per answer. Both false positives are real: a check that
  genuinely helped in one past incident but does not belong in the reference
  path here. Support and confidence filtering did not remove them.
- It stays quiet half the time, once because only one past incident shared the
  root cause and once because nothing resembled the incident at all. Both are
  the desired behaviour, and both cost recall — which is why coverage sits at
  0.67 while accuracy-when-answering sits at 1.00.
- **Confidence is badly calibrated.** ECE of 0.50 means the score is close to
  meaningless: it multiplies three sub-1 terms, so it reads around 0.3 for
  suggestions that turn out correct every time. It must not be shown to a user
  as a percentage in this state.

## What this does not answer

- Whether any of it holds beyond twelve hand-written incidents.
- Whether symptom keyword overlap is the right retrieval signal at all. It was
  chosen because it is transparent and free, not because it was compared
  against alternatives.
- How incidents would be collected and reviewed at any real scale.
- Whether a responder mid-incident finds the suggestions useful or noisy. No
  metric here measures that.

## Suggested next steps

1. **Calibrate confidence**, then re-measure ECE. Until then no confidence
   number should reach a user.
2. **Grow the corpus** across more causes and more clusters, and re-run. Expect
   precision to drop once near-miss causes appear.
3. **Sweep the policy knobs** (`min_symptom_similarity`, `min_matches`,
   `min_root_cause_agreement`, `min_support`) and publish the risk-coverage
   curve rather than a single operating point.
4. **Only then** consider a runtime surface, off by default, showing provenance
   and rationale, and worded as advice rather than a checklist.

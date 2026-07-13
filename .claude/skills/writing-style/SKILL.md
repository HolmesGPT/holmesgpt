---
name: writing-style
description: How to write blog posts, LinkedIn posts, experiment writeups, and any public-facing prose for Natan / robusta.dev, and how to design and report the experiments behind them. Use when asked to write or edit a blog post, announcement, or benchmark writeup, or when measuring LLM/agent behavior for publication.
---

# Writing and experimenting, the way Natan wants it

General rules first; concrete examples are marked as examples. Most examples
come from one real editing session (the pxpipe text-to-images benchmark post),
but the rules apply to any writeup or experiment.

## Voice

- First person, plain, direct. Write like explaining to a smart friend over
  lunch, not like writing a paper.
- Lead with the verdict. The title can be the conclusion, and the subtitle
  states the honest bottom line in one sentence. (Example: "No, You Can't
  Convert Text to Images and Save 70%".)
- Structure experiment posts as a log of sequential tests, each heading a
  question plus its answer. We tried X, found Y, moved on. (Example heading:
  "Test 1: does the arithmetic work? On a single request, yes.")
- End with a single summary section collecting every number, one bullet each,
  no new claims.
- Sign off with the author's name and role.

## Hard rules

- **No em-dashes.** Use commas, colons, or split the sentence.
- **Report what the reader actually pays or experiences, not internal
  units.** For LLM work that means billed dollars with caching active, not
  token counts. In general: pick the unit the reader budgets in.
- **No methodology trivia inline.** Run counts, iteration flags, and harness
  details stay out of the prose; say "we ran these scenarios and found:".
  Exception: a count that IS the finding (example: "26/26 passes fell to
  21/26").
- **No self-aware cutesiness.** Never "Not a typo." If a number is
  surprising, the surrounding sentence should carry that.
- **Explain by example, not by label.** If a concept needs a parenthesis to
  explain it, it deserves a full sentence instead. Parentheses are for
  asides, not for load-bearing explanations.
- **Don't narrate wrong intermediate versions** unless the mistake itself is
  the lesson, and then it gets one sentence, not a subplot. Only describe
  the final, correct method.
- **The clean writeup never mentions internal bugs found and fixed along the
  way.** Fix them on separate mergeable branches, rerun everything clean,
  write as if the broken runs never happened.
- **While drafting**, mark anything still being verified with an italic
  process note ("*rerunning this now*"). Remove every italic before final.

## Say it plainly

The test for a word or phrase: would a smart engineer outside the team say it
at lunch? Insider jargon and experimental-design vocabulary (arm, control,
delta, modality, payload) fail the test; widely known idioms pass ("needle in
a haystack" is fine). When a jargon word slips in, don't swap it for a
synonym; rewrite the sentence around what concretely happened.

Example edits from one editing session:

| Don't write | Write |
|---|---|
| every needle exact | it got every value exactly right |
| the handful of failures in any arm | the handful of failures in either mode |
| a back-to-back control killed that story | running both versions side by side on the same cluster killed that story |
| one behavioral cost survived every control | one problem kept showing up no matter how we tested |
| treat the +28% as a direction rather than a constant | take the +28% to mean "imaging made these scenarios more expensive," not as an exact number |
| the filter treats modalities differently | the filter treats text and images differently |
| two variants of the payload | two versions of the content |
| no profitability check | no check that the images would actually be cheaper |
| we ran the maximal version | we ran the extreme version |
| an injected first user message | a first user message that we insert |
| trajectory length, which the model controls | how many steps the model decides to take |
| an adversarial probe of 64 deliberately confusable IDs | a test with 64 IDs designed to be misread |

## Claims discipline

- Only claim what reproduced. "Every single time" means every single time; a
  pattern seen in a handful of trials gets "may be random variation."
- Separate the solid finding from the fine print explicitly, in the text, so
  the reader knows which part to trust.
- Never average away an asymmetry. If a result held on one model, one
  configuration, or one direction only, that IS the finding.
- When challenged ("are you sure?"), the answer is mechanism plus counts,
  never adjectives. (Example: "the API returned an empty response flagged
  content_filter, in 13+ trials across two content variants.")
- If two numbers in the same document look inconsistent, either explain the
  discrepancy in place or fix it. Never leave it for the reader to trip
  over.
- Check every "more X gives more Y" sentence against your own data before
  publishing; monotonic-sounding claims are where writeups most often
  contradict their own numbers. (Example: "converting more saves more"
  turned out backwards: converting everything saved 18%, converting only the
  system prompt saved 24%.)

## Designing experiments on nondeterministic systems

These lessons come from benchmarking LLM agents but apply to any experiment
where the system under test is expensive, noisy, or decides its own workload.

- **Compute the ceiling from existing data before running anything.** Work
  out the maximum possible effect from data you already have; if the ceiling
  is small, stop. (Example: tool results were 10-15% of prompt tokens, so
  halving them could never save more than ~7%.)
- **Prefer replay to live runs for anything replay can answer.** Re-scoring
  recorded runs under the new condition gives exact deltas with zero
  variance and zero infrastructure. Save live runs for the one thing replay
  can't show: the system behaving differently.
- **Check compatibility gates before the experiment, not during.**
  Constraints that invalidate a whole approach (an API field that only
  accepts one format, a cache that might not apply, a filter that might
  block the content) cost a few dollars to test up front and whole rerun
  batteries when discovered mid-experiment.
- **One variable per arm.** Bundled changes produce results nobody can
  attribute. Include the free control that isolates mechanism. (Example:
  moving a prompt as plain text isolates the restructuring from the pixels.)
- **Pairs beat blocks; ratios beat absolutes.** Run compared conditions back
  to back on identical infrastructure and trust the paired deltas. (Example:
  absolute baselines drifted 4-8% across days while paired deltas reproduced
  within 1-2%.)
- **Run same-vs-same first.** The spread between two identical runs defines
  the noise floor; declare it before interpreting any difference, so "that's
  noise" is a measurement rather than a judgment call.
- **Measure behavior, not just the score.** When the system under test
  chooses its own actions, record what it did (steps taken, choices made,
  rules kept) as first-class outcomes. Workload changes usually dwarf the
  effect being measured. (Example: an agent's step count swung 4-5x under
  identical conditions, swamping a tens-of-percent per-step saving.)
- **Suspect the environment before the treatment.** When behavior changes,
  rerun both conditions on identical infrastructure before believing the
  treatment caused it. The cheapest control usually settles it. (Example:
  two dramatic "findings" died under an 8-minute same-conditions rerun; both
  were infrastructure artifacts.)
- **Distrust fine-grained patterns from small samples.** Nondeterministic
  systems generate convincing-looking structure by chance. Either run enough
  trials that the pattern reproduces every time, or report it as
  unconfirmed.
- **Read individual traces before designing any fix.** Aggregates hide
  deterministic per-run differences; one before trace and one after trace
  for the same case usually explain a regression faster than any source
  diff.

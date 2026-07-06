---
name: writing-style
description: How to write blog posts, LinkedIn posts, experiment writeups, and any public-facing prose for Natan / robusta.dev, and how to design and report the experiments behind them. Use when asked to write or edit a blog post, announcement, or benchmark writeup, or when measuring LLM/agent behavior for publication.
---

# Writing and experimenting, the way Natan wants it

This skill was distilled from a real editing session (the pxpipe text-to-images
benchmark post). Every rule below was enforced at least once in that session;
the examples are real before/after edits.

## Voice

- First person, plain, direct. Write like explaining to a smart friend over
  lunch, not like writing a paper.
- Lead with the verdict. The title can be the conclusion ("No, You Can't
  Convert Text to Images and Save 70%"). The subtitle states the honest
  bottom line in one sentence.
- Structure experiment posts as a log of sequential tests: "Test 1: does the
  arithmetic work? On a single request, yes." Each heading is a question plus
  its answer. We tried X, found Y, moved on.
- End with a "What we measured, in one place" section: every number, one
  bullet each, no new claims.
- Sign off with the author's name and role.

## Hard rules

- **No em-dashes.** Use commas, colons, or split the sentence.
- **Measure and report in billed dollars, not tokens.** Prompt caching active,
  real prices. Token counts mislead; percentages of real money don't.
- **No run-count trivia inline.** Not "we ran 26 iterations across 13
  scenarios at n=2"; say "we ran these scenarios and found:". Exception: a
  count that IS the finding (26/26 passes fell to 21/26).
- **No self-aware cutesiness.** Never "Not a typo." If a number is
  surprising, the surrounding sentence should carry that.
- **Explain by example, not by label.** If a concept needs a parenthesis to
  explain it, it deserves a full sentence instead. Parentheses are for
  asides, not for load-bearing explanations.
- **Don't narrate wrong intermediate versions** unless the mistake itself is
  the lesson, and then it gets one sentence ("we learned the hard way that
  layout matters"), not a subplot. Only describe the final, correct method.
- **The clean writeup never mentions internal bugs found and fixed along the
  way.** Fix them on separate mergeable branches, rerun everything clean,
  write as if the broken runs never happened.
- **While drafting**, mark anything still being verified with an italic
  process note ("*rerunning this at higher iteration counts now*"). Remove
  every italic before final.

## Say it plainly

The test for a word or phrase: would a smart engineer outside the team say it
at lunch? Experimental-design jargon fails; widely known idioms pass
("needle in a haystack" is fine).

Real edits from the session:

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
| same-day text configuration | (rewrite the whole sentence; explain what was actually done) |

## Claims discipline

- Only claim what reproduced. "Every single time" means every single time;
  a pattern seen in 4 trials per cell gets "may be random variation."
- Separate the solid finding from the fine print explicitly, in the text:
  "Treat the text-versus-image split as the solid finding and the fine print
  as weather."
- Never average away an asymmetry. If a result held on one model and not the
  other, or in one direction only, that IS the finding.
- When challenged ("are you sure?"), the answer is mechanism plus counts
  (empty response, finish_reason=content_filter, 13+ trials, both content
  variants), never adjectives.
- If two numbers in the same document look inconsistent (two different
  baselines for the same suite), either explain the discrepancy in place or
  fix it. Never leave it for the reader to trip over.
- "More X gives more Y" claims must be checked against your own data before
  publishing; in the session, "converting more saves more" turned out
  backwards (converting everything saved 18%, converting only the system
  prompt saved 24%).

## Designing agent experiments (learned the expensive way)

- **Get the ceiling from traces before running anything.** Measure what
  fraction of billed dollars the intervention can even touch (tool results
  were 10-15% of prompt tokens). Addressable share times compression ratio
  is the maximum possible saving; if that number is small, stop.
- **Replay before live.** Re-billing recorded trajectories under the new
  format gives exact cost deltas with zero variance and zero infrastructure.
  Live agent runs are only needed for the one thing replay can't show:
  behavior change.
- **One variable per arm.** Bundling two changes (tool imaging plus system
  prompt imaging) cost a day of untangling. Include the free control that
  isolates mechanism (moving the prompt as plain text isolates the role move
  from the pixels).
- **Pairs beat blocks.** Baselines drifted 4-8% across days while paired
  same-infrastructure deltas reproduced within 1-2%. Run A and B back to
  back on identical infrastructure; trust ratios, not absolutes.
- **Run A/A first.** The baseline-versus-baseline spread defines the noise
  floor; any A/B delta inside it is declared noise before anyone gets
  attached to it.
- **Behavior is a first-class outcome.** Record calls per run, which tools
  were called with what argument sizes, rule violations, and filter events,
  not just cost and pass rate. Agent cost is dominated by how many steps the
  model takes (4-5x swings under identical conditions), so behavior swamps
  any per-call compression.
- **Suspect the environment before the treatment.** Both false findings in
  the session (an "obedience tax," a +73% cost regression) were environment
  artifacts (cluster memory pressure, a fixture race) that died under a
  same-conditions control. The control took 8 minutes.
- **Model-side non-determinism is real.** Safety-filter behavior looked
  font-dependent over 4 trials; with so few trials that pattern may be
  noise. Before claiming any fine-grained model behavior, run enough trials
  that it reproduces every time, or report it as unconfirmed.
- **Read trace-level data before designing any fix.** Aggregates over n
  iterations hide deterministic per-run differences; one baseline trace and
  one current trace for the same scenario usually explain a regression
  faster than any source diff.

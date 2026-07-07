# Cross-app downside check — cap + no-registry prompt

Probing whether the research budget (lever #1) + dropping the MCP-registry direction
breaks config correctness for apps beyond Sentry — especially a messy, self-hosted
case. All runs: staging relay → `alon-elish-cluster`, model `Robusta/Opus 4.7`, the
leaner prompt (cap + no registry), 3 iterations each.

| app | mean wall | runs (s) | guide 3/3 | endpoint produced | correct? |
|---|---|---|---|---|---|
| Sentry | 87s | 73 / 56 / 131 | yes | `https://mcp.sentry.dev/mcp`, streamable-http, bearer `<env.>` | ✅ |
| Stripe | 82s | 85 / 98 / 63 | yes | `https://mcp.stripe.com`, streamable-http, restricted-key `<env.>` | ✅ |
| Grafana | 152s | 217 / 146 / 94 | yes | self-hosted `mcp-grafana` in-cluster URL + Viewer service-account token | ✅ |

For reference, Sentry across prompt versions: **235s** (original) → **113s** (cap only) →
**87s** (cap + no-registry).

## Downside verdict: none on correctness

- **Sentry & Stripe** (both have a clean vendor-hosted remote MCP): correct endpoint,
  transport, and auth every run, at ~82-87s — roughly a third of the original time.
- **Grafana** (the hard case: no vendor-hosted MCP, docs mostly in a GitHub README):
  the leaner prompt did **not** cause a hallucinated `mcp.grafana.com`. All 3 runs
  correctly concluded it is self-hosted and produced a proper setup — create a
  read-only Viewer service account + token, a Kubernetes secret with the Grafana URL +
  token, and point Robusta at the in-cluster `mcp-grafana` service. That is the
  correct Grafana pattern.

## What Grafana tells us about the caps

- Grafana was **slower (152s, up to 9 turns)** because it is genuinely harder — no
  hosted endpoint to copy, and its real MCP docs live in the `grafana/mcp-grafana`
  GitHub README. The model **still read that GitHub repo** despite the "do NOT open
  GitHub repositories" line. So that instruction is a *soft steer the model overrides
  when it genuinely needs the source*, not a hard block that would starve a
  poorly-documented app. That is the desired behavior: fast for well-documented apps,
  still-correct (if slower) for hard ones — it degrades gracefully rather than
  producing wrong config to stay within budget.
- Net: the budget + registry-drop are safe to ship. The remaining latency for
  self-hosted/GitHub-doc-only apps is inherent to the task, not the prompt.

## Reproduce

```bash
# Rebuild per-app fixtures (render via the frontend builder, then assemble):
#   esbuild bundle of buildCustomDataSourceSetupGuidance -> render.cjs
#   assemble.py writes fixtures/mcp_req_<app>.json
ROBUSTA_STAGING_SESSION_TOKEN=<fresh> \
poetry run python tests/llm/perf/replay_wizard_latency.py \
  --request tests/llm/perf/fixtures/mcp_req_<app>.json \
  --iterations 3 --out tests/llm/perf/apps/<app>
```

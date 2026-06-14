# HolmesGPT Not Finding Any Issues? Here's Why.

## 1. Truncation: Too Much Data

Data overflow causes important information to be truncated. See [#437](https://github.com/HolmesGPT/holmesgpt/issues/437) for summarization improvements.

**Solution:**

- Use specific namespaces and time ranges
- Target individual components instead of cluster-wide queries

## 2. Missing Data Access

HolmesGPT can't access logs, metrics, or traces from your observability stack.

**Solution:**

- Verify toolset configuration connects to Prometheus/Grafana/logs
- Test connectivity: `kubectl exec -it <holmes-pod> -- curl http://prometheus:9090/api/v1/query?query=up`

## 3. Unclear Prompts

Vague questions produce poor results.

**Bad:**

- "Why is my pod not working?"
- "Check if anything is wrong with my cluster"
- "Something is broken in production and users are complaining"
- "My deployment keeps failing but I don't know why"
- "Can you debug this issue I'm having with my application?"

**Good:**

- "Why is payment-service pod restarting in production namespace?"
- "What caused memory spike in web-frontend deployment last hour?"

## 5. Model Issues

Older LLM models lack reasoning capability for complex problems.

**Solution:**
```yaml
config:
  model: "gpt-4.1"  # or anthropic/claude-sonnet-4-20250514
  temperature: 0.1
  maxTokens: 2000
```

**Recommended Models:**

- `anthropic/claude-opus-4-1-20250805` - Most powerful for complex investigations (recommended)
- `anthropic/claude-sonnet-4-20250514` - Superior reasoning with faster performance
- `gpt-4.1` - Good balance of speed/capability

See [benchmark results](../development/evaluations/latest-results.md) for detailed model performance comparisons.

## 6. `Extra inputs are not permitted` Errors From the LLM Provider

Some providers reject messages that contain fields they don't recognize, producing errors like:

```
litellm.BadRequestError: OpenAIException - messages.1.provider_specific_fields: Extra inputs are not permitted
```

This happens when LiteLLM attaches provider-specific metadata (e.g. `provider_specific_fields`) to assistant messages and those messages are later sent back to a provider that doesn't accept the field.

**Solution:** Set `LLM_EXTRA_STRIP_MESSAGE_FIELDS` to a comma-separated list of fields to strip before sending:

```bash
export LLM_EXTRA_STRIP_MESSAGE_FIELDS="provider_specific_fields"
```

Replace the value with whichever field is named in your error message. Multiple fields can be passed, e.g. `"provider_specific_fields,reasoning_content"`.

## 7. Startup Fails with `Connection reset by peer` { #firewall-blocking-robusta-platform }

HolmesGPT crashes on startup while signing in to the Robusta platform, with a traceback ending in:

```text
httpx.ConnectError: [Errno 104] Connection reset by peer
```

This means an **outbound firewall or egress policy is blocking traffic from your cluster to the Robusta platform**. The hostname resolves and the TLS certificate is valid, so it is not a DNS or certificate problem — the connection itself is being reset or refused.

**Solution:**

Allow outbound HTTPS (port 443) from the HolmesGPT pod to the Robusta platform — i.e. allowlist the `robusta.dev` domain (`*.robusta.dev`), which covers the `api.*` and `sp.*` subdomains across all regions.

To confirm the block, run the one-off pod below. It **auto-detects the Holmes pod's namespace and image** and curls the platform from a fresh pod — the HolmesGPT pod itself crashes on this error (`CrashLoopBackOff`), so `kubectl exec` into it won't work. Reusing Holmes's own image means nothing new is pulled (the same firewall may also block image pulls) and it shares Holmes's CA and network config. Just pick your region — a firewall block shows `Connection reset by peer`, while a reachable endpoint returns JSON:

```robusta-region {lang=bash}
read -r NS IMG <<<"$(kubectl get pods -A -l app=holmes -o jsonpath='{.items[0].metadata.namespace} {.items[0].spec.containers[0].image}')"
kubectl run holmes-egress-check --rm -it --restart=Never -n "$NS" --image="$IMG" --command -- curl -vk https://sp.robusta.dev/auth/v1/health
```

If the same logs also show a LiteLLM warning about failing to fetch the model cost map from `raw.githubusercontent.com`, that is the same firewall blocking GitHub egress — point Holmes at a region-local mirror with [`LITELLM_MODEL_COST_MAP_URL`](environment-variables.md#litellm_model_cost_map_url).

---

## Still stuck?

Join our [Slack community](https://cloud-native.slack.com/archives/C0A1SPQM5PZ) or [open a GitHub issue](https://github.com/HolmesGPT/holmesgpt/issues) for help.

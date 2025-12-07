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

## 3. RBAC Permissions

Service account lacks Kubernetes API permissions.

**Error Example:**
```
pods is forbidden: User "system:serviceaccount:default:holmesgpt" cannot get resource "pods"
```

**Solution:**
```yaml
rbac:
  create: true
rbacRules:
  - apiGroups: [""]
    resources: ["pods", "services", "events", "nodes"]
    verbs: ["get", "list", "watch"]
```

## 4. Unclear Prompts

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

---

## Still stuck?

Join our [Slack community](https://bit.ly/robusta-slack) or [open a GitHub issue](https://github.com/HolmesGPT/holmesgpt/issues) for help.

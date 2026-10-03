# Quickwit

Connect HolmesGPT to [Quickwit](https://quickwit.io/) for Kubernetes pod log analysis. Implements the unified `fetch_pod_logs` API: the model supplies typed parameters (pod, namespace, filter, time range) and the toolset builds the Quickwit query deterministically — no query language is exposed to the LLM, so malformed queries, unsupported wildcards, or quoting issues cannot silently return empty results.

## When to Use This

- ✅ You ship Kubernetes pod logs into a Quickwit index (e.g. via Vector or Fluent Bit)
- ✅ You want HolmesGPT to read pod logs from Quickwit instead of `kubectl logs`
- ✅ Your log documents carry pod/namespace/container metadata fields

## Prerequisites

- A reachable Quickwit HTTP endpoint (default port: `7280`)
- An index whose documents include the log message, a unix-seconds timestamp, and Kubernetes metadata fields (defaults match Vector's `kubernetes_logs` source: `kubernetes.pod_name`, `kubernetes.pod_namespace`, `kubernetes.container_name`)

--8<-- "snippets/toolsets_that_provide_logging.md"

## Configuration

```yaml-toolset-config
toolsets:
  quickwit/logs:
    enabled: true
    config:
      api_url: http://quickwit.monitoring.svc:7280
      index: k8s-logs
```

### Field mapping

If your index uses different field names, override the defaults:

```yaml-toolset-config
toolsets:
  quickwit/logs:
    enabled: true
    config:
      api_url: http://quickwit.monitoring.svc:7280
      index: k8s-logs
      timestamp_field: timestamp
      message_field: message
      namespace_field: kubernetes.pod_namespace
      pod_field: kubernetes.pod_name
      container_field: kubernetes.container_name
```

Dotted paths are resolved against nested JSON documents.

## How it works

- The query is built from exact field terms only (`<namespace_field>:<ns> AND <pod_field>:<pod>`); identifiers are sanitized to the DNS-1123 character set, so model-supplied values can never alter the query structure.
- The time range is passed as `start_timestamp`/`end_timestamp` (unix seconds) and results are requested newest-first, so high-volume periods cannot rotate recent lines out of the window.
- `filter`/`exclude_filter` are applied as case-insensitive regexes in code (a malformed regex degrades to a literal match), and rows with an empty message field (common for noisy sidecars) are dropped before the limit is applied.
- When more lines match than the limit, the response says so explicitly (`[showing the N most recent of M matching lines]`) instead of truncating silently.

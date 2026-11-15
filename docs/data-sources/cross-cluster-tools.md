# Cross-Cluster Tools

Some of HolmesGPT's most useful tools — `kubectl`, in-cluster Prometheus, pod logs — only work **inside** the cluster they target. Normally a Holmes instance can only run them against its own cluster.

**Cross-cluster tools** lift that restriction. A Holmes instance investigating one cluster (the **caller**) can run cluster-local tools that physically execute on the Holmes instance in another cluster (the **executor**), and get the result back in the same investigation. This is what powers **multi-agent investigations** ("compare this deployment with prod-eu", "find the failing pods across the whole fleet").

The mechanism is opt-in per toolset via the `expose_remotely` flag, and gated per account by a feature toggle in the Robusta platform. It requires the Robusta platform — each executor cluster runs a Holmes instance connected to Robusta, which routes the remote tool calls.

## Two ways to use it

Once a toolset is exposed remotely, its tools can be reached in two ways. Each is controlled by its **own, independent** account setting:

| Use case | Who calls the tools | Account setting | Default | Change it in the UI |
|---|---|---|---|---|
| **Multi-agent investigations** | Another Holmes agent, during an investigation | `multi_agent_investigation_enabled` | **On** | **Settings → Multi-Agent → Enable Multi Agent Investigations** |
| **External MCP clients** | Any MCP client (Claude Code, Claude Desktop, …) via an API key | `expose_remote_tool_calls_externally` | Off | **Settings → MCP Server → Robusta MCP Server Enabled** |

The two switches are independent — turning one on does not turn on the other.

## What gets exposed

By default, only the toolsets that are **useful exclusively inside a cluster** are exposed:

| Toolset | Exposed by default | Notes |
|---|---|---|
| `kubernetes/core` | ✅ | kubectl-based inspection |
| `kubernetes/logs` | ✅ | pod / workload logs |
| `kubernetes/live-metrics` | ✅ | `kubectl top` for pods and nodes |
| `kubernetes/kube-prometheus-stack` | ✅ | in-cluster Prometheus scrape targets |
| `prometheus/metrics` | ✅ *(in-cluster only)* | exposed only when the instance points at an in-cluster URL — see [Prometheus locality](#prometheus-and-other-multi-instance-toolsets) |
| `bash` | ✅ *(pre-approved commands only)* | commands that need approval are denied remotely |
| Everything else | ❌ | opt-in via `expose_remotely: true` |

Notable opt-in toolsets — `inspektor-gadget`, `cilium`, `openshift`, `kubevela` — default to **not exposed**. Enable them explicitly if you want them callable across clusters.

Location-agnostic toolsets (a Grafana Cloud or Datadog endpoint reachable from anywhere) are **not** exposed by default: the caller can query those directly, so proxying them through another cluster only adds latency. You can still opt one in.

[MCP servers](remote-mcp-servers.md) are toolsets too: set `expose_remotely: true` on an `mcp_servers` entry to let other clusters call its tools.

Internal agent-machinery toolsets (`robusta_platform_mcp`, the `TodoWrite` / `core_investigation` toolset, and `skills`) are **never** exposed, regardless of configuration.

## Enabling or disabling a toolset

The `expose_remotely` flag lives alongside the toolset's other config. Set it to `true` to publish a toolset that isn't exposed by default, or `false` to withhold one that is.

=== "Holmes Helm Chart"

    ```yaml
    toolsets:
      # Opt a normally-local toolset IN
      cilium/core:
        enabled: true
        expose_remotely: true

      # Opt a default-exposed toolset OUT
      bash:
        enabled: true
        expose_remotely: false
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        cilium/core:
          enabled: true
          expose_remotely: true

        bash:
          enabled: true
          expose_remotely: false
    ```

=== "Holmes CLI"

    In `~/.holmes/config.yaml`:

    ```yaml
    toolsets:
      cilium/core:
        enabled: true
        expose_remotely: true

      bash:
        enabled: true
        expose_remotely: false
    ```

Tool lists are read when Holmes starts, so apply changes with a Holmes restart / Helm upgrade. Other clusters pick up the change within about 6 minutes: each Holmes refreshes its list of remote tools every 5 minutes (`TOOLSET_STATUS_REFRESH_INTERVAL_SECONDS`), and the Robusta platform caches it for 1 minute.

### Prometheus and other multi-instance toolsets

Toolsets that support [multiple instances](multi-instance-toolsets.md) decide `expose_remotely` **per instance**. For each instance, the first of these that applies wins:

1. `expose_remotely` on the instance's `instances:` entry.
2. `expose_remotely` on the toolset, next to `enabled`.
3. For `prometheus/metrics`, the instance URL:
    - **In-cluster URL** (`*.svc`, `*.cluster.local`, `localhost`, a single-label service name, or an RFC1918 IP) — **exposed**.
    - **Auto-detected URL** (no `prometheus_url` set) — **exposed** (auto-detection only finds in-cluster servers).
    - **Public / SaaS URL** (Grafana Cloud, Azure Monitor, any public DNS host) — **not exposed**.
4. The toolset's default from the table above.

Only healthy instances are exposed. `expose_remotely` under `config:` applies to every instance that doesn't set its own.

```yaml
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      instances:
        - name: in-cluster
          prometheus_url: http://prometheus.monitoring.svc:9090
          # in-cluster → exposed automatically
        - name: staging
          prometheus_url: http://prometheus.staging.svc:9090
          expose_remotely: false  # in-cluster, but withheld
        - name: grafana-cloud
          prometheus_url: https://prometheus-prod.grafana.net/api/prom
          expose_remotely: true   # SaaS endpoint, exposed on purpose
```

## Connecting an external MCP client

With **Robusta MCP Server** enabled (`expose_remote_tool_calls_externally`), any MCP client can run your fleet's cross-cluster tools. The Robusta UI generates a paste-ready config under **Settings → Robusta MCP Server → How to Connect**; the steps are:

**1. Create a Robusta API key**

Create an API key with the **`Robusta AI:Write`** permission (Settings → API Keys).

**2. Add the MCP server to your client**

The endpoint is your Robusta API host followed by `/api/platform-mcp`. The `Authorization` header is `Bearer <ACCOUNT_ID> <API_KEY>` — the UI pre-fills your account ID, so you only add the key.

=== "Claude Code (CLI)"

    ```robusta-region {lang=bash}
    claude mcp add --transport http robusta-platform \
      "https://api.robusta.dev/api/platform-mcp" \
      --header "Authorization: Bearer <YOUR_ACCOUNT_ID> <YOUR_ROBUSTA_AI_API_KEY>"
    ```

=== "Generic MCP client (Claude Desktop / mcp.json)"

    ```robusta-region {lang=json}
    {
      "mcpServers": {
        "robusta-platform": {
          "type": "http",
          "url": "https://api.robusta.dev/api/platform-mcp",
          "headers": {
            "Authorization": "Bearer <YOUR_ACCOUNT_ID> <YOUR_ROBUSTA_AI_API_KEY>"
          }
        }
      }
    }
    ```

**3. Start investigating**

Your client can now investigate any cluster in your fleet. Each remote tool takes an `agent_name` (a synonym for the cluster) that selects which cluster it runs on.

## Requirements and limits

- **Robusta platform required.** Each executor cluster runs a Holmes instance connected to the Robusta platform; the platform routes the calls. This is not available for standalone / CLI-only Holmes.
- **Matching Holmes versions.** A tool is callable only between clusters running the **same** Holmes version — identical versions guarantee identical tool schemas. A cluster on a different version simply won't appear as a callable target.
- **Permissions.** On accounts with RBAC enabled, a user must hold **`MA_HOLMES_CHAT`** on the target cluster; clusters the user can't access never appear as targets.
- **No approval prompt.** Tools listed in a toolset's `approval_required_tools` are never offered to other clusters, and `bash` runs **pre-approved** commands only; any other command is denied. Holmes doesn't otherwise check what a tool does, so only expose toolsets whose tools you're willing to let every cluster in the account run — and, with Robusta MCP Server on, any API key with `Robusta AI:Write`.
- **Result size.** Remote tool results are returned inline and capped at **1&nbsp;MB**. Oversized results fail with a "narrow the query" error — reduce the time range, tighten filters, or lower the limit.

## Troubleshooting

A cluster or tool you expect is missing from the caller's `remote_*` tools, or from `list_available_clusters`:

- **The toolset isn't healthy on the executor.** Only enabled toolsets whose prerequisites pass are exposed — for example, `kubernetes/live-metrics` needs metrics-server. Check the executor's toolset status in the Robusta UI or its Holmes logs.
- **The executor runs a different Holmes version** from the caller. Upgrade both to the same version.
- **The account setting is off** for the way you're calling (see [Two ways to use it](#two-ways-to-use-it)), or the user lacks `MA_HOLMES_CHAT` on the executor cluster.
- **The tool is in `approval_required_tools`**, or the instance isn't exposed (see [Prometheus and other multi-instance toolsets](#prometheus-and-other-multi-instance-toolsets)).
- **The change hasn't propagated yet.** Allow about 6 minutes after the executor restarts.

A call to an instance that isn't exposed fails with `instance '<name>' is not exposed on this cluster`, and lists the instances that are.

## Related

- [Multiple Instances](multi-instance-toolsets.md) — per-instance configuration for toolsets like Prometheus.
- [MCP Servers](remote-mcp-servers.md) — the reverse direction: connecting Holmes **to** external MCP servers.

# Cross-Cluster Tools

Some of HolmesGPT's most useful tools — `kubectl`, in-cluster Prometheus, pod logs — only work **inside** the cluster they target. Normally a Holmes instance can only run them against its own cluster.

**Cross-cluster tools** lift that restriction. A Holmes instance investigating one cluster (the **caller**) can run cluster-local tools that physically execute on the Holmes instance in another cluster (the **executor**), and get the result back in the same investigation. This is what powers **multi-agent investigations** ("compare this deployment with prod-eu", "find the failing pods across the whole fleet").

The mechanism is opt-in per toolset via the `expose_remotely` flag, and gated per account by a feature toggle in the Robusta platform. It requires the Robusta platform — each executor cluster runs a Holmes instance connected to Robusta, which routes the remote tool calls.

## Two ways to use it

Once a toolset is exposed remotely, its tools can be reached in two ways. Each is controlled by its **own, independent** account setting (both off by default):

| Use case | Who calls the tools | Account setting | Enable in the UI |
|---|---|---|---|
| **Multi-agent investigations** | Another Holmes agent, during an investigation | `multi_agent_investigation_enabled` | **Settings → Enable Multi Agent Investigations** |
| **External MCP clients** | Any MCP client (Claude Code, Claude Desktop, …) via an API key | `expose_remote_tool_calls_externally` | **Settings → Robusta MCP Server** |

The two switches are independent — turning one on does not turn on the other.

## What gets exposed

By default, only the toolsets that are **useful exclusively inside a cluster** are exposed:

| Toolset | Exposed by default | Notes |
|---|---|---|
| `kubernetes/core` | ✅ | kubectl-based inspection |
| `kubernetes/logs` | ✅ | pod / workload logs |
| `prometheus` | ✅ *(in-cluster only)* | exposed only when the instance points at an in-cluster URL — see [Prometheus locality](#prometheus-and-other-multi-instance-toolsets) |
| `bash` | ✅ | commands that need approval run only after a person approves them — see [Approval](#approval) |
| Everything else | ❌ | opt-in via `expose_remotely: true` |

Notable opt-in toolsets — `inspektor-gadget`, `cilium`, `openshift`, `kubevela` — default to **not exposed**. Enable them explicitly if you want them callable across clusters.

Location-agnostic toolsets (a Grafana Cloud or Datadog endpoint reachable from anywhere) are **not** exposed by default: the caller can query those directly, so proxying them through another cluster only adds latency. You can still opt one in.

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

Tool lists are read when Holmes starts, so apply changes with a Holmes restart / Helm upgrade.

### Prometheus and other multi-instance toolsets

Toolsets that support [multiple instances](multi-instance-toolsets.md) resolve `expose_remotely` **per instance**, and each `instances:` entry may set its own override. For `prometheus`, the default is derived from the instance URL:

- **In-cluster URL** (`*.svc`, `*.cluster.local`, `localhost`, a single-label service name, or an RFC1918 IP) — **exposed**.
- **Auto-detected URL** (no `prometheus_url` set) — **exposed** (auto-detection only finds in-cluster servers).
- **Public / SaaS URL** (Grafana Cloud, Azure Monitor, any public DNS host) — **not exposed**.

An explicit `expose_remotely:` on the instance entry (or on the toolset) always wins over the heuristic:

    toolsets:
      prometheus:
        enabled: true
        config:
          instances:
            - name: in-cluster
              prometheus_url: http://prometheus.monitoring.svc:9090
              # in-cluster → exposed automatically
            - name: grafana-cloud
              prometheus_url: https://prometheus-prod.grafana.net/api/prom
              expose_remotely: true   # force-expose a SaaS endpoint (not the default)

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

## Approval

Tools that need approval are published like any other tool: those listed in a toolset's `approval_required_tools` (for example `run_kubectl_command` of the [Kubernetes Remediation MCP](builtin-toolsets/kubernetes-remediation-mcp.md)), and `bash` commands outside the allow list. MCP clients see the approval-gated ones with the standard `destructiveHint: true` tool annotation.

The first call to such a tool does not run it: the target cluster answers that it needs approval, and the Robusta platform records that request. The call then runs once, with exactly the same arguments on the same cluster, after a person approves it:

- **Multi-agent investigations.** Holmes asks for approval in the chat or in Slack, like a local command. When the user approves, Holmes calls the tool again and the platform checks the decision in the conversation before running it.
- **External MCP clients.** The first call returns a link to an approval page in Robusta. Clients on MCP `2025-11-25` get it as a URL-mode elicitation (error `-32042`); older clients get it in the tool error. The owner of the API key opens the link, signed in to Robusta, reviews the command and approves or denies it. The client then calls the tool again with the same arguments. A request expires after an hour.

Approval follows the account's **Allow Enhanced Bash Mode** setting (disabled for everyone, enabled for everyone, or for admins only) and the approver's `MA_HOLMES_CHAT` permission on the target cluster. In a chat that has an owner, only the owner can approve; for admins only, the owner must be an admin, and a chat without an owner (for example a triggered workflow) cannot be approved. The same rules apply to local commands. An approval is used once; the next call asks again. Approving a `bash` command with "don't ask again" applies only to commands on that cluster in the same conversation.

To expose the Kubernetes Remediation MCP, set `mcpAddons.kubernetesRemediation.exposeRemotely: true` on the clusters that should accept remote remediation.

## Requirements and limits

- **Robusta platform required.** Each executor cluster runs a Holmes instance connected to the Robusta platform; the platform routes the calls. This is not available for standalone / CLI-only Holmes.
- **Matching Holmes versions.** A tool is callable only between clusters running the **same** Holmes version — identical versions guarantee identical tool schemas. A cluster on a different version simply won't appear as a callable target.
- **Permissions.** On accounts with RBAC enabled, a user must hold **`MA_HOLMES_CHAT`** on the target cluster; clusters the user can't access never appear as targets.
- **Approval.** A tool that needs approval runs remotely only after a person approves that exact call — see [Approval](#approval).
- **Result size.** Remote tool results are returned inline and capped at **1&nbsp;MB**. Oversized results fail with a "narrow the query" error — reduce the time range, tighten filters, or lower the limit.

## Related

- [Multiple Instances](multi-instance-toolsets.md) — per-instance configuration for toolsets like Prometheus.
- [MCP Servers](remote-mcp-servers.md) — the reverse direction: connecting Holmes **to** external MCP servers.

# Kubernetes Remediation (MCP)

The Kubernetes Remediation MCP server is what lets Holmes **act on your cluster** — restart pods, scale deployments, drain nodes, patch and edit resources, and more — plus run **deeper diagnostics** than read-only access allows: reading files and processes *inside* running containers and launching short-lived troubleshooting pods (netshoot/busybox/curl).

It runs **alongside** your existing [built-in Kubernetes toolset](kubernetes.md) (which already covers `get`/`describe`/`logs`), extending Holmes from read-only investigation to investigation **and** remediation — with every mutating action gated behind human approval.

!!! info "What this adds over the built-in Kubernetes toolset"

    | Capability | Built-in | + Remediation MCP |
    |---|---|---|
    | Read resources (`get` / `describe` / `logs`) | ✅ | — *(keep using the built-in)* |
    | Read files & processes inside containers | ❌ | ✅ auto-approved |
    | Run diagnostic pods (netshoot/busybox/curl) | ❌ | ✅ auto-approved |
    | Write actions (restart / scale / drain / patch / …) | ❌ | ✅ **human-approved** |

## Available Tools

| Tool | Mutating | Approval | What it does |
|------|----------|----------|--------------|
| `read_file_from_container` | No | Auto | Read a single file from inside a running container. Secret/token mounts are always refused. |
| `run_preapproved_kubectl_command` | No | Auto | Run a read-only diagnostic command from the allowlist (`ps`/`top`/`df`/`ls`/`netstat`/`ss` via exec). |
| `run_preapproved_diagnostic_image` | No | Auto | Launch a short-lived pod from a pre-approved troubleshooting image (netshoot/busybox/curl), capture output, auto-delete. Probe targets are restricted to in-cluster destinations; cloud metadata is always refused ([details](#diagnostic-pod-target-policy)). |
| `get_remediation_mcp_config` | No | Auto | Return the live effective policy for debugging. |
| `run_kubectl_command` | Yes | **Human approval** | Catch-all for everything not pre-approved: all mutations, arbitrary exec, non-allowlisted images. |

Each tool is *either* always auto-approved *or* always human-approved — the split is fixed, so the model never has to guess whether an action is safe to take on its own. The read-only and diagnostic tools run immediately; the mutating fallback (`run_kubectl_command`) always pauses for a human.

## Prerequisites

For CLI deployments, you'll need to create the RBAC resources manually. For Helm deployments, the chart creates them automatically (a scoped, least-privilege ClusterRole — not `cluster-admin`).

## Configuration

=== "Holmes CLI"

    **Step 1: Create RBAC Resources**

    Create a file named `k8s-remediation-rbac.yaml` with a **scoped** ClusterRole (no `cluster-admin`, no `secrets`):

    ```yaml
    apiVersion: v1
    kind: Namespace
    metadata:
      name: holmes-mcp
    ---
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: k8s-remediation-mcp-sa
      namespace: holmes-mcp
    ---
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRole
    metadata:
      name: k8s-remediation-mcp-role
    rules:
      - apiGroups: ["apps"]
        resources: ["deployments", "statefulsets", "daemonsets", "replicasets"]
        verbs: ["get", "list", "patch", "update", "delete"]
      - apiGroups: ["apps"]
        resources: ["deployments/scale", "statefulsets/scale", "replicasets/scale"]
        verbs: ["get", "update", "patch"]
      - apiGroups: [""]
        resources: ["pods"]
        verbs: ["get", "list", "create", "delete"]
      - apiGroups: [""]
        resources: ["pods/exec"]
        verbs: ["create"]
      - apiGroups: [""]
        resources: ["pods/log"]
        verbs: ["get"]
      - apiGroups: [""]
        resources: ["pods/eviction"]
        verbs: ["create"]
      - apiGroups: [""]
        resources: ["nodes"]
        verbs: ["get", "list", "patch", "update"]
      - apiGroups: ["batch"]
        resources: ["jobs", "cronjobs"]
        verbs: ["get", "list", "create", "patch", "update", "delete"]
      # Read-only context (NO secrets)
      - apiGroups: [""]
        resources: ["events", "services", "configmaps", "namespaces", "replicationcontrollers"]
        verbs: ["get", "list"]
    ---
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRoleBinding
    metadata:
      name: k8s-remediation-mcp
    roleRef:
      apiGroup: rbac.authorization.k8s.io
      kind: ClusterRole
      name: k8s-remediation-mcp-role
    subjects:
    - kind: ServiceAccount
      name: k8s-remediation-mcp-sa
      namespace: holmes-mcp
    ```

    ```bash
    kubectl apply -f k8s-remediation-rbac.yaml
    ```

    **Step 2: Deploy the MCP Server**

    Create a file named `k8s-remediation-mcp-deployment.yaml`:

    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: k8s-remediation-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: k8s-remediation-mcp-server
      template:
        metadata:
          labels:
            app: k8s-remediation-mcp-server
        spec:
          serviceAccountName: k8s-remediation-mcp-sa
          containers:
          - name: k8s-remediation-mcp
            image: us-central1-docker.pkg.dev/genuine-flight-317411/mcp/kubernetes-remediation-mcp:1.2.0
            imagePullPolicy: IfNotPresent
            ports:
            - containerPort: 8000
              name: http
            # The defaults below ship in the image — listing them is optional.
            env:
            - name: KUBECTL_ALLOWED_COMMANDS
              value: "edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate,run,exec"
            - name: KUBECTL_TIMEOUT
              value: "60"
            # Diagnostic-pod target policy (see "Diagnostic-pod target policy"
            # below). These are the defaults; both are shown because they are
            # the two you are most likely to need to change.
            - name: KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS
              value: "false"
            - name: KUBECTL_DIAGNOSTIC_INTERNAL_DNS_SUFFIXES
              value: ".svc,.svc.cluster.local,.cluster.local"
            resources:
              requests:
                memory: "64Mi"
                cpu: "50m"
              limits:
                memory: "128Mi"
            securityContext:
              readOnlyRootFilesystem: true
              runAsNonRoot: true
              runAsUser: 1000
              allowPrivilegeEscalation: false
            readinessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 5
              periodSeconds: 10
            livenessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 10
              periodSeconds: 30
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: k8s-remediation-mcp-server
      namespace: holmes-mcp
    spec:
      selector:
        app: k8s-remediation-mcp-server
      ports:
      - port: 8000
        targetPort: 8000
        protocol: TCP
        name: http
    ```

    ```bash
    kubectl apply -f k8s-remediation-mcp-deployment.yaml
    ```

    **Step 3: Configure Holmes CLI**

    Add the MCP server configuration to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      kubernetes_remediation:
        description: "Kubernetes remediation & deep diagnostics - execute kubectl and run diagnostic pods"
        config:
          url: "http://k8s-remediation-mcp-server.holmes-mcp.svc.cluster.local:8000/mcp"
          mode: streamable-http
        approval_required_tools:
          - "run_kubectl_command"
    ```

    Only the mutating fallback (`run_kubectl_command`) is listed under `approval_required_tools`, so it requires confirmation before execution. The four read-only tools run immediately.

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    The defaults work out of the box once enabled (plug-and-play). Add the following to your `values.yaml`:

    ```yaml
    mcpAddons:
      kubernetesRemediation:
        enabled: true
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

    The chart creates a scoped ClusterRole (no `cluster-admin`), an ingress-only NetworkPolicy locked to Holmes, and wires `approval_required_tools: ["run_kubectl_command"]`. Override `serviceAccount.clusterRole` to bring your own role, or `config.*` to tune the allowlists.

=== "Robusta Helm Chart"

    Add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        kubernetesRemediation:
          enabled: true
    ```

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Security Controls

All policy lives in the MCP server; Holmes only maps tool name → approval.

| Control | Description |
|---------|-------------|
| **Tool separation** | Read-only tools auto-approve; only `run_kubectl_command` (mutations) requires human approval |
| **Path policy** | `read_file_from_container` resolves symlinks in-container and re-checks them; secret/token mounts (`/var/run/secrets/`, `/run/secrets/`) and the `/proc`, `/sys`, `/dev` pseudo-filesystems are always denied |
| **Command allowlist** | `run_preapproved_kubectl_command` only runs the read-only diagnostics allowlist |
| **Image allowlist** | `run_preapproved_diagnostic_image` only launches pre-approved, pinned troubleshooting images |
| **Diagnostic target policy** | `run_preapproved_diagnostic_image` probes are restricted to in-cluster targets, and cloud-metadata/link-local addresses are always refused — see [Diagnostic-pod target policy](#diagnostic-pod-target-policy) |
| **Verb allowlist** | `run_kubectl_command` only accepts an allowlisted set of verbs |
| **Flag blocklist** | Flags like `--kubeconfig`, `--context`, `--token`, `--as` are always blocked |
| **Shell injection protection** | Shell metacharacters are rejected; `shell=False` |
| **Locked-down mode** | Set `allowArbitraryKubectlCommands: false` to disable `run_kubectl_command` entirely |
| **Scoped RBAC** | Least-privilege ClusterRole — no `cluster-admin`, no `secrets` |
| **NetworkPolicy** | Ingress-only, locked to Holmes pods |
| **Command timeout** | Commands are killed after a configurable timeout (default: 60s) |

## Diagnostic-pod target policy

!!! warning "Requires MCP server image 1.2.0 or newer"

    The `config` keys in this section are read by the MCP server, not by Holmes.
    On an older image they are passed through and ignored, and probe targets are
    unrestricted. The Helm chart pins 1.2.0 by default.

`run_preapproved_diagnostic_image` is auto-approved, and the images it launches
are network-probing tools (`curl`, `dig`, `wget`, `tcpdump`). The image allowlist
controls *what runs* but not *where the probe points* — so without a target
policy, prompt-injected content in your cluster (a pod log, an annotation, an
alert description) could steer an auto-approved probe at the cloud metadata
service and have the response handed back to Holmes, or POST cluster data to an
external collector. No approval prompt would appear, because this tool
legitimately never asks for one.

Two layers constrain the target:

**1. Target validation in the server**, before any pod is created:

- **Always refused, and not configurable:** cloud metadata and
  link-local/loopback destinations — `169.254.0.0/16` (AWS/Azure/OpenStack IMDS
  and ECS task metadata), `127.0.0.0/8`, `0.0.0.0/8`, `100.100.100.200`
  (Alibaba), `192.0.0.192` (Oracle), `::1`, `fe80::/10`, `fd00:ec2::254`, plus
  metadata hostnames like `metadata.google.internal`. Recognised in every IPv4
  spelling (decimal, hex, octal, short forms) and as IPv4-mapped IPv6.
- **Refused unless you opt in:** targets outside the cluster
  (`diagnosticAllowExternalTargets`).
- **Always refused:** redirect-following (`curl -L`), which would let the
  responding server choose the real target.

**2. An egress NetworkPolicy** on the diagnostic pod, which is the CNI-enforced
backstop for anything validation cannot see — a DNS name that only resolves to a
metadata address inside the pod, or `wget` following a redirect it was never told
to follow. The server labels every diagnostic pod `robusta.dev/diagnostic-pod: "true"`
and pins `hostNetwork: false` so the policy applies.

!!! important "The NetworkPolicy is not installed by this chart"

    Apply
    [`diagnostic-pod-networkpolicy.yaml`](https://github.com/robusta-dev/holmes-mcp-integrations/blob/master/servers/kubernetes-remediation/diagnostic-pod-networkpolicy.yaml)
    to **every namespace** Holmes may run diagnostics in — NetworkPolicy is
    namespaced, and the namespace comes from the caller. Namespaces without it
    fall back to target validation alone. It is also inert on a CNI that does not
    enforce NetworkPolicy.

### If a legitimate probe is refused

Refusals name the rule and the fix, and Holmes will usually correct itself. The
most common case is a namespace-qualified short name: `kubernetes.default` is
refused because by shape it is indistinguishable from an external domain — use
the FQDN `kubernetes.default.svc.cluster.local`.

Otherwise, in order of preference:

1. **Custom cluster domain** → set `diagnosticInternalDnsSuffixes`.
2. **Legitimate external probing** (egress checks, reaching a known external API)
   → set `diagnosticAllowExternalTargets: true`. Metadata and link-local stay refused.
3. **A one-off that genuinely needs a restricted target** → use
   `run_kubectl_command`, which asks a human first.
4. **Last resort** → `diagnosticTargetPolicyEnabled: false`.

!!! danger "`diagnosticTargetPolicyEnabled: false` disables all target checks"

    This includes the cloud-metadata ranges that are otherwise not configurable,
    and restores the pre-1.2.0 behaviour: an auto-approved probe can be pointed
    at the metadata service and its response returned to Holmes. The server logs
    a warning at startup and on every call while it is off. Apply the egress
    NetworkPolicy first if you set this, since it becomes your only remaining
    control. The image allowlist, shell-metacharacter rejection and
    flag-injection guard are unaffected.

## Configuration Reference

| Helm value (`config.*`) | Default | Purpose |
|-------------------------|---------|---------|
| `allowedCommands` | `edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate,run,exec` | Hard verb allowlist for `run_kubectl_command` |
| `dangerousFlags` | `--kubeconfig,--context,--cluster,--user,--token,--as,--as-group,--as-uid` | Blocked flags |
| `preapprovedCommands` | `exec * -- ps*,...,exec * -- ss*` | `run_preapproved_kubectl_command` allowlist |
| `diagnosticImages` | `nicolaka/netshoot:v0.13,busybox:1.37.0,curlimages/curl:8.11.1` | `run_preapproved_diagnostic_image` allowlist |
| `diagnosticTargetPolicyEnabled` | `true` | master switch for the [target policy](#diagnostic-pod-target-policy); `false` disables **all** target checks |
| `diagnosticAllowExternalTargets` | `false` | allow diagnostic probes to reach hosts outside the cluster |
| `diagnosticInternalDnsSuffixes` | `.svc,.svc.cluster.local,.cluster.local` | DNS suffixes counted as cluster-internal (set for a custom cluster domain) |
| `fileReadAllowedPaths` | `/` | `read_file_from_container` allow roots |
| `fileReadDeniedPaths` | `/var/run/secrets/,/run/secrets/,...` | secret-mount denylist |
| `allowArbitraryKubectlCommands` | `true` | enable the approval-gated fallback |
| `timeout` | `60` | per-command timeout (s) |

## Common Use Cases

```bash
holmes ask "Read /app/config.yaml from the checkout-api pod and tell me what database host it points to"
```

```bash
holmes ask "From inside the production cluster, check whether the payments service DNS resolves and the endpoint is reachable"
```

```bash
holmes ask "Restart the payment-service deployment in the production namespace"
```

```bash
holmes ask "The checkout-api pods are crashlooping - investigate and fix"
```

## Additional Resources

- [Kubernetes Remediation MCP Server setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/kubernetes-remediation)

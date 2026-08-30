# Kubernetes Remediation (MCP)

The Kubernetes Remediation MCP server extends Holmes from read-only investigation to investigation **and action**: it can restart, scale, patch and drain — with every mutating action gated behind human approval — and run deeper diagnostics than read-only access allows, like reading files inside running containers and launching short-lived troubleshooting pods.

It runs **alongside** the built-in [Kubernetes toolset](kubernetes.md), which keeps handling `get`/`describe`/`logs`.

## What Works Out of the Box

Enable it with one Helm value (see [Setup](#setup)) and Holmes immediately gets five tools:

| Tool | Approval | What it does |
|------|----------|--------------|
| `read_file_from_container` | Auto | Read a file from inside a running container (config files, on-disk logs). Secret/token mounts and `/proc`, `/sys`, `/dev` are always refused. |
| `run_preapproved_kubectl_exec_command` | Auto | Run an allowlisted read-only binary inside a container via `kubectl exec`. Default allowlist: `ps`, `top`, `df`, `ls`, `netstat`, `ss`. |
| `run_preapproved_diagnostic_image` | Auto | Launch a short-lived pod from an allowlisted troubleshooting image (`nicolaka/netshoot`, `busybox`, `curlimages/curl`), capture its output, auto-delete it. Probe targets are restricted to in-cluster destinations by default ([details](#diagnostic-pod-target-policy)). |
| `get_remediation_mcp_config` | Auto | Return the live effective policy, for debugging. |
| `run_kubectl_command` | **Human approval** | The catch-all for everything else: mutations (`scale`, `rollout`, `delete`, `patch`, `cordon`, `drain`, `taint`, `label`, ...), arbitrary `exec`, non-allowlisted images. |

Each tool is *either* always auto-approved *or* always human-approved — the split is fixed, so the model never guesses whether an action is safe to take on its own.

**What this lets Holmes do**, with no further configuration:

```bash
# Remediate (Holmes proposes the kubectl command; you approve it before it runs)
holmes ask "Restart the payment-service deployment in production"
holmes ask "The checkout-api pods are crashlooping - investigate and fix"
holmes ask "Cordon node worker-3 and drain it safely"

# Look inside containers (auto-approved)
holmes ask "Read the app config from the checkout-api pod and tell me which database host it points to"
holmes ask "Is anything filling up the disk inside the etl-worker pod?"
holmes ask "Which process inside the api pod is using the most memory, and what ports is it listening on?"

# Probe the network from inside the cluster (auto-approved)
holmes ask "From inside the cluster, check whether the payments service DNS resolves and the endpoint responds"
holmes ask "Is the orders service reachable from the staging namespace?"
```

## Setup

=== "Holmes Helm Chart"

    The defaults work out of the box once enabled. Add to your `values.yaml`:

    ```yaml
    mcpAddons:
      kubernetesRemediation:
        enabled: true
    ```

    Then deploy or upgrade:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

    The chart creates a scoped ClusterRole (no `cluster-admin`, no `secrets`), an ingress-only NetworkPolicy locked to Holmes, a random per-release bearer token authenticating Holmes to the server, and wires `approval_required_tools: ["run_kubectl_command"]`. Override `serviceAccount.clusterRole` to bring your own role, or `config.*` to tune the allowlists ([reference](#configuration-reference)).

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

=== "Holmes CLI (manual deploy)"

    For CLI deployments, create the RBAC resources and deploy the server manually.

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
      # `watch` lets kubectl run/debug follow the pods they create; without it
      # they still work but spam forbidden-watch retries into stderr
      - apiGroups: [""]
        resources: ["pods"]
        verbs: ["get", "list", "watch", "create", "delete"]
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
            # The defaults below ship in the image — listing them is optional,
            # but they are the values you are most likely to customize
            # (see "Customizing What Holmes Can Run").
            env:
            - name: KUBECTL_ALLOWED_COMMANDS
              value: "edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate,run,exec"
            - name: KUBECTL_PREAPPROVED_EXEC_BINARIES
              value: "ps,top,df,ls,netstat,ss"
            - name: KUBECTL_DIAGNOSTIC_IMAGES
              value: "nicolaka/netshoot:v0.13,busybox:1.37.0,curlimages/curl:8.11.1"
            - name: KUBECTL_TIMEOUT
              value: "60"
            # Diagnostic-pod target policy (see below).
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

## Customizing What Holmes Can Run

All policy lives in the MCP server, configured through Helm values under `mcpAddons.kubernetesRemediation.config.*` (or the matching `KUBECTL_*` env vars on a manual deployment — see the [reference table](#configuration-reference)). The sections below cover the customizations that unlock the most, with real scenarios.

### GPU Troubleshooting: More Pre-Approved Exec Binaries

`run_preapproved_kubectl_exec_command` runs a single binary inside an existing container, and only binaries on the allowlist are allowed (matched exactly on the binary name — `nvidia-smi` does not allow `nvidia-smi-tool`). The default list is generic (`ps,top,df,ls,netstat,ss`); extend it with whatever read-only diagnostics your workloads carry. GPU pods running on the NVIDIA runtime have `nvidia-smi` available inside the container, so adding it lets Holmes check GPU health with no approval round-trip:

=== "Holmes Helm Chart"

    ```yaml
    mcpAddons:
      kubernetesRemediation:
        enabled: true
        config:
          preapprovedExecBinaries: "ps,top,df,ls,netstat,ss,nvidia-smi,free,uptime,du"
    ```

=== "Manual deployment"

    ```yaml
    env:
    - name: KUBECTL_PREAPPROVED_EXEC_BINARIES
      value: "ps,top,df,ls,netstat,ss,nvidia-smi,free,uptime,du"
    ```

Now Holmes can answer these without waiting for approval:

```bash
holmes ask "The training job in ml-prod is slower than yesterday. Check GPU utilization and memory on its pods."
```

```bash
holmes ask "Are any GPUs in the ml-prod namespace showing ECC errors or thermal throttling?"
```

```bash
holmes ask "Is the inference service actually using the GPU, or is it falling back to CPU?"
```

Holmes passes arguments too — the allowlist gates only the binary, so `nvidia-smi -q -d MEMORY,UTILIZATION,ECC,TEMPERATURE` works once `nvidia-smi` is listed.

GPU checks belong on this exec allowlist rather than on the diagnostic-image list: the GPU device is bound to the workload's container, so `nvidia-smi` must run *inside that container* — a separate diagnostic pod sees no GPU.

**What to add — and what not to.** Good candidates are read-only, side-effect-free binaries: `nvidia-smi`, `free`, `uptime`, `du`, `ip`, `id`, `hostname`, or a read-only health CLI your app ships. Do **not** add shells (`sh`, `bash`), interpreters (`python`, `perl`), or package managers — they execute arbitrary code, which collapses the auto-approved/human-approved distinction. Anything that mutates state belongs in `run_kubectl_command`, where a human sees it first. Also note the binary must actually exist in the target container: allowlisting `nvidia-smi` does nothing for pods without the NVIDIA runtime. (`cat` and `env` are deliberately absent from the default list: use `read_file_from_container` for files — it enforces the secret-mount denylist — and `env` output leaks env-injected secrets.)

### Custom Diagnostic Images

`run_preapproved_diagnostic_image` launches a short-lived pod, so it works even when the workload's own containers have nothing useful installed (distroless images, scratch containers). The default allowlist covers general network debugging; add your own images for anything more specific — a database client image, an org-internal toolbox, a cloud CLI:

=== "Holmes Helm Chart"

    ```yaml
    mcpAddons:
      kubernetesRemediation:
        enabled: true
        config:
          diagnosticImages: "nicolaka/netshoot:v0.13,busybox:1.37.0,curlimages/curl:8.11.1,ghcr.io/yourorg/db-toolbox:1.4.2"
    ```

=== "Manual deployment"

    ```yaml
    env:
    - name: KUBECTL_DIAGNOSTIC_IMAGES
      value: "nicolaka/netshoot:v0.13,busybox:1.37.0,curlimages/curl:8.11.1,ghcr.io/yourorg/db-toolbox:1.4.2"
    ```

Always pin a tag — the model requests an image by repository name and the server substitutes the pinned tag from the allowlist, so the model can never pick the version. Keep the images read-only troubleshooting tools; every image on this list runs with **no approval prompt** (though with [restricted targets](#diagnostic-pod-target-policy), no service account token, and capped resources).

```bash
holmes ask "Use the db-toolbox to check whether the orders database accepts connections from the staging namespace"
```

### Node Troubleshooting with kubectl debug

Node-level problems — kubelet failures, packet drops, conntrack exhaustion, disk pressure from files outside any pod — need access to the node itself. `kubectl debug node/<name>` is the standard tool for that, but the `debug` verb is **not** in the default allowlist, so Holmes gets a refusal if it tries. Add it:

=== "Holmes Helm Chart"

    ```yaml
    mcpAddons:
      kubernetesRemediation:
        enabled: true
        config:
          allowedCommands: "edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate,run,exec,debug"
    ```

=== "Manual deployment"

    ```yaml
    env:
    - name: KUBECTL_ALLOWED_COMMANDS
      value: "edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate,run,exec,debug"
    ```

`debug` goes through `run_kubectl_command`, so **every node debug session is human-approved** — appropriate, since a node debug pod can mount the host filesystem. Holmes proposes the full `kubectl debug ... -- <command>` invocation, you approve it, and the command's output comes back in the tool result (this works non-interactively: `-it` needs no real terminal here, and the server captures stdout).

```bash
holmes ask "Pods on node worker-3 keep timing out on DNS. Debug the node's network and find out why."
```

```bash
holmes ask "Check the kubelet logs on worker-3 for eviction or PLEG errors."
```

```bash
holmes ask "Something outside of Kubernetes is filling the disk on worker-3 - find what."
```

Commands Holmes will typically propose (each one approval-gated):

```bash
# Node network diagnostics. The debug pod shares the node's network namespace
# by default, so interface stats, sockets, and routing are the node's own:
kubectl debug node/worker-3 -it --image=nicolaka/netshoot:v0.13 -- ip -s link

# Tools that need NET_ADMIN/NET_RAW (tcpdump, conntrack) additionally need
# --profile=netadmin; --profile=sysadmin runs privileged:
kubectl debug node/worker-3 -it --profile=netadmin --image=nicolaka/netshoot:v0.13 -- conntrack -S

# Node files and logs: the node filesystem is mounted at /host
kubectl debug node/worker-3 -it --image=busybox:1.37.0 -- chroot /host journalctl -u kubelet --since "1 hour ago"
```

!!! note "Cleanup"

    `kubectl debug` leaves the debug pod behind when it finishes. Holmes can delete it afterwards (`delete` is in the default verb allowlist and prompts for approval), or clean up yourself: debug pods are named `node-debugger-<node>-<suffix>`.

### Debugging Pods That Have No Shell

The same `debug` verb also enables **ephemeral debug containers** on running pods — the way into distroless or scratch-based containers where `kubectl exec` has nothing to execute:

```bash
holmes ask "The checkout-api container is distroless and I can't exec into it. Attach a debug container and check what ports it's listening on."
```

Holmes proposes something like:

```bash
kubectl debug -it pod/checkout-api-7d9f -n prod --image=nicolaka/netshoot:v0.13 --target=checkout-api -- ss -tlnp
```

`--target` puts the debug container in the app container's process namespace so `ss`/`ps` see its sockets and processes.

This needs one RBAC addition the default ClusterRole doesn't carry — attaching an ephemeral container patches the pod:

```yaml
- apiGroups: [""]
  resources: ["pods/ephemeralcontainers"]
  verbs: ["update", "patch"]
```

With the Helm chart, copy the chart's ClusterRole, add the rule, and point `serviceAccount.clusterRole` at your copy. (Node debugging needs no RBAC change — it creates a regular pod, which the default role already allows.)

### Allowing External Probe Targets

By default, auto-approved diagnostic pods may only probe in-cluster targets. If Holmes should verify egress or reach external APIs (is `api.stripe.com` reachable from inside the cluster?), opt in:

```yaml
mcpAddons:
  kubernetesRemediation:
    enabled: true
    config:
      diagnosticAllowExternalTargets: true
```

Cloud-metadata and link-local addresses stay refused regardless. See [Diagnostic-pod target policy](#diagnostic-pod-target-policy) for what this changes and for the egress NetworkPolicy you should pair with it.

### Locked-Down Mode: Diagnostics Only, No Mutations

To get the deep-diagnostics tools without giving Holmes any write path at all, disable the mutating fallback entirely:

```yaml
mcpAddons:
  kubernetesRemediation:
    enabled: true
    config:
      allowArbitraryKubectlCommands: false
```

`run_kubectl_command` then refuses everything; the four auto-approved tools keep working. You can also narrow `allowedCommands` instead — e.g. `"rollout,scale,delete"` permits restarts and scaling but refuses drains, taints, and exec.

### Longer Timeouts for Slow Operations

Every command is killed after `timeout` seconds (default 60). `kubectl drain` on a busy node routinely takes longer:

```yaml
mcpAddons:
  kubernetesRemediation:
    enabled: true
    config:
      timeout: "300"
```

## Security Model

All policy lives in the MCP server; Holmes only maps tool name → approval.

| Control | Description |
|---------|-------------|
| **Tool separation** | Read-only tools auto-approve; only `run_kubectl_command` (mutations) requires human approval |
| **Path policy** | `read_file_from_container` resolves symlinks in-container and re-checks them; secret/token mounts (`/var/run/secrets/`, `/run/secrets/`) and the `/proc`, `/sys`, `/dev` pseudo-filesystems are always denied |
| **Exec binary allowlist** | `run_preapproved_kubectl_exec_command` only runs allowlisted binaries, matched exactly on the binary name |
| **Image allowlist** | `run_preapproved_diagnostic_image` only launches pre-approved images, at the pinned tag |
| **Diagnostic target policy** | Probes are restricted to in-cluster targets; cloud-metadata/link-local addresses are refused and cannot be re-enabled by config while the policy is on — see [below](#diagnostic-pod-target-policy) |
| **Diagnostic pod hardening** | Diagnostic pods run with `automountServiceAccountToken: false`, no privilege escalation, capped memory, and `hostNetwork: false` pinned |
| **Verb allowlist** | `run_kubectl_command` only accepts an allowlisted set of verbs |
| **Flag blocklist** | Flags like `--kubeconfig`, `--context`, `--token`, `--as` are always blocked, as is `--overrides` |
| **Shell injection protection** | Shell metacharacters are rejected; `shell=False` |
| **HTTP authentication** | The Helm chart generates a bearer token requiring callers to authenticate (server ≥ 1.2.0); a NetworkPolicy additionally restricts ingress to Holmes pods |
| **Scoped RBAC** | Least-privilege ClusterRole — no `cluster-admin`, no `secrets` |
| **Command timeout** | Commands are killed after a configurable timeout (default: 60s) |

## Diagnostic-Pod Target Policy

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

    Apply it yourself to **every namespace** Holmes may run diagnostics in —
    NetworkPolicy is namespaced, and the namespace comes from the caller.
    Namespaces without it fall back to target validation alone. It is also inert
    on a CNI that does not enforce NetworkPolicy.

    Note this is a *different* policy from the ingress-only one the chart already
    renders for the MCP server itself: that one selects the server pod
    (`app: kubernetes-remediation-mcp`) and restricts inbound traffic, whereas
    this one selects the short-lived diagnostic pods and restricts their egress.

Save this as `diagnostic-pod-networkpolicy.yaml` and apply it with
`kubectl apply -f diagnostic-pod-networkpolicy.yaml -n <namespace>`. It ships
alongside the MCP server, at
[`servers/kubernetes-remediation/`](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/kubernetes-remediation)
in `holmes-mcp-integrations`, which is the canonical copy if the two ever drift.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: kubernetes-remediation-diagnostic-pod-egress
  labels:
    app: kubernetes-remediation-mcp
spec:
  # The MCP server stamps this label on every diagnostic pod it creates, and
  # pins hostNetwork:false (a host-networked pod is exempt from NetworkPolicy).
  podSelector:
    matchLabels:
      robusta.dev/diagnostic-pod: "true"
  policyTypes:
    - Egress
  egress:
    # Cluster DNS, so service names still resolve. Scoped with `to:` — a rule
    # carrying only `ports` would match ALL destinations on port 53, turning DNS
    # into an exfiltration channel out of the cluster.
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53

    # In-cluster (RFC1918) destinations only. An egress policy denies whatever it
    # does not allow, so cloud metadata (169.254.0.0/16), loopback (127.0.0.0/8),
    # 0.0.0.0/8, 100.100.100.200 (Alibaba), 192.0.0.192 (Oracle) and the public
    # internet are all denied by omission — none of them fall inside these blocks.
    # Also covers DNS sent to the kube-dns Service ClusterIP on CNIs that evaluate
    # policy before kube-proxy's DNAT.
    - to:
        - ipBlock:
            cidr: 10.0.0.0/8
        - ipBlock:
            cidr: 172.16.0.0/12
        - ipBlock:
            cidr: 192.168.0.0/16
```

Two things to check against your cluster: `kubernetes.io/metadata.name` is set on
every namespace automatically from Kubernetes 1.21, and CoreDNS carries
`k8s-app: kube-dns` on both kubeadm and k3s — adjust the selector if your DNS
provider differs. And if your **Service CIDR is outside RFC1918**, add it as an
`ipBlock` or in-cluster resolution will break.

If you set `diagnosticAllowExternalTargets: true`, widen the `ipBlock` to
`0.0.0.0/0` but add the denied ranges back as `except` entries — this policy
should never be looser than the server-side checks it backs up.

Verify your CNI actually enforces it before relying on it:

```bash
kubectl run np-test --rm -i --restart=Never -n <namespace> \
  --image=curlimages/curl:8.11.1 \
  --overrides='{"metadata":{"labels":{"robusta.dev/diagnostic-pod":"true"}}}' \
  -- curl -s -m 5 http://169.254.169.254/    # must time out, not answer
```

### If a Legitimate Probe Is Refused

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

| Helm value (`config.*`) | Env var (manual deploy) | Default | Purpose |
|-------------------------|-------------------------|---------|---------|
| `allowedCommands` | `KUBECTL_ALLOWED_COMMANDS` | `edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate,run,exec` | Hard verb allowlist for `run_kubectl_command` |
| `dangerousFlags` | `KUBECTL_DANGEROUS_FLAGS` | `--kubeconfig,--context,--cluster,--user,--token,--as,--as-group,--as-uid` | Blocked flags |
| `preapprovedExecBinaries` | `KUBECTL_PREAPPROVED_EXEC_BINARIES` | `ps,top,df,ls,netstat,ss` | Binaries `run_preapproved_kubectl_exec_command` may run in-container, matched exactly on `command[0]` |
| `diagnosticImages` | `KUBECTL_DIAGNOSTIC_IMAGES` | `nicolaka/netshoot:v0.13,busybox:1.37.0,curlimages/curl:8.11.1` | `run_preapproved_diagnostic_image` allowlist (pinned tags) |
| `diagnosticTargetPolicyEnabled` | `KUBECTL_DIAGNOSTIC_TARGET_POLICY_ENABLED` | `true` | Master switch for the [target policy](#diagnostic-pod-target-policy); `false` disables **all** target checks |
| `diagnosticAllowExternalTargets` | `KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS` | `false` | Allow diagnostic probes to reach hosts outside the cluster |
| `diagnosticInternalDnsSuffixes` | `KUBECTL_DIAGNOSTIC_INTERNAL_DNS_SUFFIXES` | `.svc,.svc.cluster.local,.cluster.local` | DNS suffixes counted as cluster-internal (set for a custom cluster domain) |
| `fileReadAllowedPaths` | `KUBECTL_FILE_READ_ALLOWED_PATHS` | `/` | `read_file_from_container` allow roots |
| `fileReadDeniedPaths` | `KUBECTL_FILE_READ_DENIED_PATHS` | `/var/run/secrets/,/run/secrets/,...` | Secret-mount denylist |
| `allowArbitraryKubectlCommands` | `KUBECTL_ALLOW_ARBITRARY_COMMANDS` | `true` | Enable the approval-gated fallback |
| `timeout` | `KUBECTL_TIMEOUT` | `60` | Per-command timeout (s) |

To see the policy a running server actually has (after Helm overrides, env typos, image-version mismatches), ask Holmes — `get_remediation_mcp_config` returns the live effective configuration:

```bash
holmes ask "Show me the current kubernetes remediation policy - which commands and images are pre-approved?"
```

## Additional Resources

- [Kubernetes Remediation MCP Server setup guide](https://github.com/robusta-dev/holmes-mcp-integrations/tree/master/servers/kubernetes-remediation)

# Kubernetes Remediation MCP — Design & Review Spec

This document describes the Kubernetes Remediation MCP addon as implemented across
two repositories, so a reviewer can understand *what* it does, *why* it's split the
way it is, and *where the security boundaries actually are* (including the residual
risks). It is the companion to PRs:

- **`robusta-dev/holmes-mcp-integrations` #24** — the MCP server (Python + manifests).
- **`HolmesGPT/holmesgpt` #2148** — agent-core change, Helm chart, docs, tests.

Both PRs are on branch `claude/k8s-remediation-mcp-BTz5W`.

---

## 1. Goal

Let HolmesGPT **diagnose and act** on a cluster beyond what the Holmes pod's own
RBAC allows — read files inside other containers, run throwaway diagnostic pods,
and remediate (mutate) the cluster — while keeping a clear, legible boundary
around which actions require a human.

## 2. Core design principle: approval legibility through tool separation

Every tool is **either always auto-approved or always approval-gated**. The split
is encoded in the *tool set*, not in per-command heuristics the model has to reason
about. This is the central design bet: a reviewer (and the model) can tell at a
glance whether a tool needs a human, because it's a property of the tool's identity,
not of its arguments.

| Tool | Mutating | Approval | Enforced where |
|---|---|---|---|
| `read_file_from_container` | No | **Auto** | server path policy |
| `run_preapproved_kubectl_command` | No | **Auto** | server command allowlist |
| `run_preapproved_diagnostic_image` | No (data-gathering pod) | **Auto** | server image allowlist |
| `run_gpu_node_diagnostics` | No (data-gathering pod, no host access) | **Auto** | server check catalog (server-owned commands) |
| `get_remediation_mcp_config` | No | **Auto** | — |
| `run_kubectl_command` | Yes | **Human approval** | HolmesGPT `approval_required_tools` + server guards |
| `run_gpu_node_host_diagnostics` | No, but node-root (privileged + hostPID + host FS read-only) | **Human approval** | HolmesGPT `approval_required_tools` + server check catalog + `GPU_DIAG_ALLOW_HOST_ACCESS` |

### Division of responsibility

- **All *policy* lives in the MCP server**: command/image/path allowlists, the
  hard verb allowlist, the dangerous-flag blocklist, the arbitrary-command toggle.
- **HolmesGPT only maps tool name → approval** via `approval_required_tools`, and
  carries the LLM instructions. The agent core has no command-parsing logic.

This keeps the agent generic and puts the security-sensitive logic in one place
(the server), versionable with the server image.

---

## 3. The MCP server (`servers/kubernetes-remediation/`)

`kubernetes_remediation.py` is a FastMCP server. Every kubectl invocation uses
`subprocess.run([...], shell=False)` — no shell is ever involved.

### 3.1 Auto-approved tools

**`read_file_from_container(namespace, pod, path, container=None)`**
- Runs `kubectl exec <pod> [-c <c>] -n <ns> -- cat <path>`.
- `path` is validated **before** execution against the allow/deny policy, and
  **symlinks are resolved inside the container** (`readlink -f`) and the canonical
  target is re-checked. This closes a symlink-under-an-allowed-root → secret-mount
  bypass.
- `namespace`/`pod`/`container` are validated as identifiers — shell metacharacters
  rejected **and** a leading `-` rejected (flag-injection guard, see §6.1).

**`run_preapproved_kubectl_command(args)`**
- The joined command is glob-matched against `KUBECTL_PREAPPROVED_COMMANDS`
  (default: `exec * -- ps*`, `top*`, `df*`, `ls*`, `netstat*`, `ss*`). Deliberately
  excludes `cat` (use `read_file_from_container`) and `env` (leaks secrets).
- Still applies the dangerous-flag blocklist and shell-char rejection (defense in depth).

**`run_preapproved_diagnostic_image(image, namespace, command=None, name=None)`**
- `image` is matched on **repository** against `KUBECTL_DIAGNOSTIC_IMAGES`
  (default pinned: `nicolaka/netshoot:v0.13`, `busybox:1.37.0`,
  `curlimages/curl:8.11.1`); the server runs the **pinned tag** so the model can
  just name the repo.
- The pod is launched **hardened** (see §6.3): no SA token, no privilege
  escalation, memory-capped — but capabilities and root are left intact so
  `tcpdump`/`ping`/`iperf` still work. Output is captured; the pod is auto-deleted
  (`--rm` plus a best-effort `finally` delete).

**`run_gpu_node_diagnostics(node, check, dcgm_diag_level=1)`** *(server >= 1.3.0)*
- Runs one **named** GPU check on a node via a short-lived pod pinned there with
  `spec.nodeName` (bypasses the scheduler, so cordoned nodes stay diagnosable;
  a blanket toleration survives GPU-node taints). The NVIDIA container runtime
  injects the host driver's `nvidia-smi` (`NVIDIA_VISIBLE_DEVICES=all`,
  `NVIDIA_DRIVER_CAPABILITIES=utility`, `runtimeClassName` from
  `GPU_DIAG_RUNTIME_CLASS`) **without allocating `nvidia.com/gpu`**, so it
  schedules on fully-utilized nodes. Checks: `overview`, `details`,
  `throttling`, `utilization_samples`, `ecc`, `page_retirement`,
  `row_remapper`, `compute_processes`, and (when `GPU_DIAG_DCGM_ENABLED`)
  `dcgm_discovery`/`dcgm_health`/`dcgm_diag` in the DCGM image with an embedded
  host engine. Every command string is a server-owned constant; the caller
  supplies only the node name (identifier-validated), a check name from the
  catalog, and a `dcgm_diag_level` bounds-checked against
  `GPU_DIAG_DCGM_MAX_DIAG_LEVEL` (default 1 — level 3 stress-tests the GPU).
  The pod carries the same hardening as `run_preapproved_diagnostic_image`
  (no SA token, no privilege escalation, memory-capped, host namespaces off,
  the `robusta.dev/diagnostic-pod` label) and is auto-deleted.

**`get_remediation_mcp_config()`** — returns the live effective policy for debugging.

### 3.2 Approval-gated fallback

**`run_kubectl_command(args)`** — the catch-all for everything not pre-approved:
all mutations, arbitrary exec, non-allowlisted images. Server guards (independent
of HolmesGPT approval):
- **Hard verb allowlist** `KUBECTL_ALLOWED_COMMANDS`
  (default: `edit,patch,delete,scale,rollout,cordon,uncordon,drain,taint,label,annotate,run,exec`).
- **Flag blocklist** `KUBECTL_DANGEROUS_FLAGS`
  (`--kubeconfig,--context,--cluster,--user,--token,--as,--as-group,--as-uid`) + `--overrides`.
- **Shell-metacharacter rejection**; `shell=False`.
- **Timeout** `KUBECTL_TIMEOUT` (60s).
- **`KUBECTL_ALLOW_ARBITRARY_COMMANDS`** (default `true`): when `false`, this tool is
  disabled — a fully locked-down mode where only the auto-approved tools work.

**`run_gpu_node_host_diagnostics(node, check, pid=None, pci_bus_id=None)`** *(server >= 1.3.0)*
— the second approval-gated tool. Runs one **named** kernel/driver/PCIe GPU
check via a pod that is what `kubectl debug node/` would build: privileged,
`hostPID: true`, the host root hostPath-mounted **read-only** at `/host`
(host binaries run through `chroot /host`; host processes and the shared
kernel are visible through the pod's own `/proc`). Checks: `kernel_gpu_errors`
(dmesg XID/NVRM), `kernel_log_journal`, `driver_info` (loaded vs on-disk vs
running driver version), `pci`, `pci_link`, `fabric_manager`,
`gpu_device_holders` (`/dev/nvidia*` holders), `process_info`. Commands are
server-owned; the only caller values that reach a command are `pid`
(numeric-validated) and `pci_bus_id` (`[0-9a-fA-F:.]{1,16}`). It mutates
nothing, but privileged+hostPID+host-filesystem is node-root, so it sits on
the gated side of the boundary — `approval_required_tools` lists it alongside
`run_kubectl_command` — and `GPU_DIAG_ALLOW_HOST_ACCESS=false` (chart:
`gpuDiagnostics.allowHostAccess`) removes it entirely, mirroring the
locked-down toggle.

GPU diagnostics config: `GPU_DIAG_ENABLED` (master switch), `GPU_DIAG_IMAGE` /
`GPU_DIAG_DCGM_IMAGE` / `GPU_DIAG_HOST_IMAGE` (pinned defaults), 
`GPU_DIAG_DCGM_ENABLED`, `GPU_DIAG_DCGM_MAX_DIAG_LEVEL`,
`GPU_DIAG_RUNTIME_CLASS`, `GPU_DIAG_ALLOW_HOST_ACCESS`, `GPU_DIAG_NAMESPACE`
(the chart sets the release namespace, where the diagnostic-pod egress
NetworkPolicy is applied), `GPU_DIAG_TIMEOUT` (default 300s — image pull +
`dcgmi diag` runtime). The chart also grants `pods/attach` in the scoped
ClusterRole: `kubectl run --rm -i` (used by all pod-launching tools) attaches
to stream output.

### 3.3 Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `KUBECTL_ALLOWED_COMMANDS` | see above | Hard verb allowlist for the fallback |
| `KUBECTL_DANGEROUS_FLAGS` | see above | Blocked flags |
| `KUBECTL_PREAPPROVED_COMMANDS` | 6 read-only exec patterns | Auto-approved command allowlist |
| `KUBECTL_DIAGNOSTIC_IMAGES` | 3 pinned images | Auto-approved image allowlist |
| `KUBECTL_DIAGNOSTIC_TARGET_POLICY_ENABLED` | `true` | Master switch for the diagnostic target policy (§6.4) |
| `KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS` | `false` | Permit probes to non-cluster targets |
| `KUBECTL_DIAGNOSTIC_INTERNAL_DNS_SUFFIXES` | `.svc,.svc.cluster.local,.cluster.local` | Suffixes counted as cluster-internal |
| `KUBECTL_FILE_READ_ALLOWED_PATHS` | `/` | Read allow roots |
| `KUBECTL_FILE_READ_DENIED_PATHS` | secret/token mounts | Configurable read denylist |
| `KUBECTL_ALLOW_ARBITRARY_COMMANDS` | `true` | Enable the gated fallback |
| `KUBECTL_TIMEOUT` | `60` | Per-command timeout (s) |

In addition, `/proc`, `/sys`, `/dev` are **hard-denied in code** (not operator-removable) —
see §6.2. The cloud-metadata/link-local ranges are hard-denied for diagnostic-pod
targets on the same basis, overridable only by turning the whole target policy off —
see §6.4.

### 3.4 RBAC & NetworkPolicy

- **Scoped ClusterRole**, not `cluster-admin`. Workloads (scale/rollout/edit/patch/
  delete), pods (`get/list/create/delete`, `pods/exec` create, `pods/log` get,
  `pods/eviction` create), nodes (cordon/drain/taint/label), batch jobs, and
  read-only context. **`secrets` is intentionally absent** (defense in depth on top
  of the file-read denylist).
- **Ingress-only NetworkPolicy** locked to Holmes pods (`app: holmes`) in the
  release namespace. Restricts only ingress, so it can't break MCP→apiserver.
  Inert where the CNI doesn't enforce.

---

## 4. HolmesGPT agent-core change

The legacy **`restricted_tools` / skill-gating mechanism was removed entirely**;
approval is now purely tool-name based.

- `holmes/core/tools.py`: deleted `Tool.restricted`, `Tool._is_restricted()`, and
  the `restricted_tools` fields on `Toolset` / `ToolsetYamlFromConfig`. Kept the
  `approval_required_tools` → `_check_approval_config` → `requires_approval` path
  (this is what gates `run_kubectl_command`). Added a `model_validator` that
  **ignores + warns** on a deprecated `restricted_tools` key so old configs don't
  hard-fail.
- `holmes/core/tool_calling_llm.py`: removed `_skill_in_use`,
  `_should_include_restricted_tools()`, and the "skills unlock restricted tools"
  coupling. `reset_interaction_state()` is now a no-op. Skill *fetching* is untouched.
- `holmes/core/tools_utils/{tool_executor,frontend_tools}.py`: removed the
  `include_restricted` parameter/filter and the `_is_restricted` override.

Net core change is a deletion plus config plumbing.

## 5. Helm chart (`helm/holmes/.../kubernetes-remediation/`)

`mcpAddons.kubernetesRemediation`: opt-in (`enabled: false`) but plug-and-play once
enabled. Image `1.3.0`. Chart renders the scoped ClusterRole when
`serviceAccount.clusterRole` is empty (bring-your-own otherwise), the ingress-only
NetworkPolicy (on by default), the ConfigMap/env wiring for all the config keys
(including the `config.gpuDiagnostics` block → `GPU_DIAG_*` env vars, with
`GPU_DIAG_NAMESPACE` pinned to the release namespace), and
`approval_required_tools: ["run_kubectl_command", "run_gpu_node_host_diagnostics"]`
in `toolset-config.yaml`. The LLM instructions in `_helpers.tpl` describe the
auto-vs-gated split, including both GPU tools.

---

## 6. Security model — what's actually enforced (read this part)

The auto-approved tier is the security-sensitive surface, because those tools run
with **no human in the loop**. Be precise about what that tier can and cannot do.

### 6.1 Flag injection (fixed)

The dedicated tools build their own kubectl arg lists, so the
`DANGEROUS_FLAGS` blocklist (which only runs in the gated `validate_kubectl_args`
path) did **not** protect them. A value like `pod="--kubeconfig=/tmp/evil"` would be
parsed by kubectl as a flag, not a pod name — flag injection in an auto-approved
tool. **Fix:** `_validate_identifier` rejects any `namespace`/`pod`/`container`/pod-
`name` that begins with `-` (these are never legitimately flag-like), applied across
`read_file_from_container` and `run_preapproved_diagnostic_image`.

### 6.2 The path denylist is a string filter — `/proc` and symlinks routed around it (fixed)

The configurable `FILE_READ_DENIED_PATHS` is a **string-prefix** check on the
*requested* path, but `cat` runs in the container and follows symlinks and `/proc`.
Two concrete bypasses existed:
- `/proc/<pid>/environ` → env-injected secrets (note: the design excludes the `env`
  *command* "because it leaks secrets" — the file read was the same leak).
- `/proc/<pid>/root/var/run/secrets/.../token` → the SA token via a path not
  prefixed by any deny entry.
- a symlink under an allowed root pointing into a secret mount.

**Fixes:**
- `/proc`, `/sys`, `/dev` are **hard-denied in code** (`HARD_DENIED_PATHS`),
  unconditional and not operator-removable. (The default allow root stays `/` — we
  can't assume where app source lives.)
- `read_file_from_container` resolves the path with `readlink -f` *inside the
  container* and re-checks the canonical target against the full policy. Best-effort:
  if the container has no `readlink`, it falls back to the literal-path checks plus
  the `/proc,/sys,/dev` denial.

### 6.3 Diagnostic pods are launched hardened (fixed)

`run_preapproved_diagnostic_image` now injects a server-controlled `--overrides` (strategic
merge) that sets `automountServiceAccountToken: false` (the pod never needs API
access; removes an escalation vector), `allowPrivilegeEscalation: false`, and a
memory limit + requests. It deliberately does **not** drop capabilities, force
non-root, or set a CPU limit — so `tcpdump`/`ping` keep their caps and `iperf` isn't
throttled.

The overrides also pin `hostNetwork`/`hostPID`/`hostIPC` to `false` and stamp the pod
with `robusta.dev/diagnostic-pod: "true"`. Both exist for §6.4: a host-networked pod is
exempt from NetworkPolicy, and the label is what the egress policy selects on.

### 6.4 Diagnostic-pod targets were unrestricted (fixed — ROB-910)

The image allowlist constrains *what runs*; it said nothing about *where the probe
points*. Since the allowlisted images are network-probing tools (`curl`/`dig`/`wget`)
and the tool is **auto-approved**, the probe target was the real security boundary and
it was wide open. Shell-char rejection does not help: `:` `/` `.` `?` `=` are all legal,
so a URL passes untouched.

Prompt-injected content in the cluster (a pod log, an annotation, an alert body) could
therefore steer an auto-approved probe at `169.254.169.254` and have the instance
credentials returned to the agent, or POST cluster data to an external collector — with
no approval prompt, because this tool legitimately never asks for one.
`automountServiceAccountToken: false` (§6.3) does not cover it: IMDSv1, GCP and Azure
metadata need no pod credential.

Two layers now constrain the target.

**Layer 1 — `validate_diagnostic_command`, before any pod is created:**

- **Hard-denied, not operator-tunable** (same basis as `/proc` in §6.2):
  `169.254.0.0/16` (IMDS + ECS task metadata), `127.0.0.0/8`, `0.0.0.0/8`,
  `100.100.100.200` (Alibaba), `192.0.0.192` (Oracle), `::1`, `fe80::/10`,
  `fd00:ec2::254`, and the metadata hostnames. Matched in **every IPv4 spelling
  `inet_aton` accepts** (decimal `2852039166`, hex `0xA9FEA9FE`, octal, 2-/3-part) and
  as IPv4-mapped IPv6, with URL userinfo stripped first so
  `http://harmless.example.com@169.254.169.254/` is judged on the real host.
- **Denied unless `KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS=true`:** non-cluster
  targets. This is the half that closes exfiltration. *Every* token is inspected, not
  just the URL, so `-x http://collector` / `--proxy=socks5://…` and `dig @server` are
  covered — those change where the connection actually goes.
- **Always denied:** explicit redirect-following (`curl -L`, bundled `-fsSL`), which
  hands target selection to the responding server.

**Layer 2 — an egress NetworkPolicy** on the diagnostic pod:
`diagnostic-pod-networkpolicy.yaml`, added by holmes-mcp-integrations#39 next to the
server manifests, selecting the `robusta.dev/diagnostic-pod` label from §6.3 and
allowing cluster DNS + RFC1918 only.

This is **not** the same object as the pre-existing `networkpolicy.yaml` in that
directory (also rendered by the chart as
`templates/mcp-servers/kubernetes-remediation/networkpolicy.yaml`). That one is
`policyTypes: [Ingress]` and selects the *server* pod
(`app: kubernetes-remediation-mcp`) to restrict who may call the MCP server; it says
nothing about diagnostic-pod egress and cannot substitute for this layer. Two policies,
two different pod selectors, opposite directions.

Neither is installed by the Helm chart for diagnostic pods — NetworkPolicy is
namespaced and `run_preapproved_diagnostic_image` takes the namespace from the caller,
so the operator applies the egress policy per namespace. The docs page carries the
manifest inline for that reason.

**Neither layer is sufficient alone.** Layer 1 cannot see a DNS name that only resolves
to a metadata address *inside* the pod (wildcard DNS such as `169.254.169.254.nip.io`),
nor `wget`'s follow-redirects-by-default, which has no flag to key on — layer 2 contains
those. Layer 2 is namespaced (so it must be applied to every namespace diagnostics run
in), inert on a non-enforcing CNI, and cannot give the model a reason it can act on —
layer 1 does that, and Holmes self-corrects from the refusal text.

Cluster-internal means a bare service name, a configured suffix
(`KUBECTL_DIAGNOSTIC_INTERNAL_DNS_SUFFIXES`), or an RFC1918 address.
Namespace-qualified shortcuts (`kubernetes.default`) are deliberately *not* internal —
by shape they are indistinguishable from `evil.com` — and the refusal tells the caller
to use the FQDN.

`KUBECTL_DIAGNOSTIC_TARGET_POLICY_ENABLED=false` disables layer 1 entirely, including
the hard denials, for environments the policy misjudges. It restores the pre-fix
exposure and is logged loudly at startup and per call; the image allowlist, shell-char
rejection and flag-injection guard are unaffected.

### 6.4b GPU node diagnostics — where the boundary sits

The GPU tools deliberately expose **no command surface**: the caller picks a
check name; the server owns every command string. That keeps the auto-approved
tier honest — prompt-injected content can choose *which* fixed read-only
diagnostic runs on *which* node, but cannot compose a command, pick an image,
mount anything, or point a probe anywhere (the GPU checks take no network
targets at all, so §6.4's target policy is not in play).

Three caller-supplied scalars do reach a shell string inside the throwaway
pod, each strictly validated first: `pid` (numeric, bounded), `pci_bus_id`
(`^[0-9a-fA-F:.]{1,16}$` — no shell metacharacter passes), and
`dcgm_diag_level` (int, capped by `GPU_DIAG_DCGM_MAX_DIAG_LEVEL`, default 1 so
the GPU-stressing level 3 needs an operator opt-in). Node names go through the
same `_validate_identifier` flag-injection guard as pods/namespaces (§6.1).

The split between the two tools is the host boundary, not mutation:
`run_gpu_node_diagnostics` runs an unprivileged pod (no host namespaces, no
mounts, SA token off, driver tooling via runtime injection) and is
auto-approved; `run_gpu_node_host_diagnostics` is privileged + `hostPID` with
the host root mounted read-only — it mutates nothing, but that combination is
node-root (any host file readable, all host processes visible), so it is
approval-gated exactly like `run_kubectl_command` and removable outright via
`GPU_DIAG_ALLOW_HOST_ACCESS=false`. Read-only mounting is a real constraint on
file tampering but not on secrets exposure — a host mount reaches kubelet
credentials and every pod's writable layer — which is why "read-only" did not
argue it onto the auto side.

Both pods bypass the scheduler via `spec.nodeName` (intentional: a cordoned or
NoSchedule-tainted GPU node must stay diagnosable) and carry a blanket
`operator: Exists` toleration so NoExecute taints don't evict a check mid-run.
The resource-exhaustion posture is the same as §6.5's diagnostic-pod point:
per-pod memory caps and `GPU_DIAG_TIMEOUT`, no global concurrency cap.

### 6.5 Residual risks (accepted, documented)

- **`pods/exec` is granted and reachable from an auto-approved path.** `pods/exec`
  cluster-wide is a known privilege-escalation verb; `read_file_from_container` and
  `run_preapproved_kubectl_command` are constrained exec, but exec-into-container is
  inherently powerful. `ps` output (auto-approved) can include secrets passed as
  process CLI args. This is an accepted product trade-off: deep diagnostics without
  a human prompt. The denylist/symlink/`/proc` hardening reduces, but does not
  eliminate, what an auto-approved read can reach.
- **The approval boundary depends on cross-system config staying in sync.** "Gated"
  is enforced only by HolmesGPT's `approval_required_tools`; the server has no notion
  of approval and will run `run_kubectl_command` if called directly. The chart pins
  the image so the tool-name↔approval-list mapping can't silently drift; overriding
  the image tag re-opens that. The server-side backstop is the all-or-nothing
  `allowArbitraryKubectlCommands` toggle. Two operational caveats follow from this:
  (1) the NetworkPolicy (§3.4) is what stops a non-HolmesGPT client from calling the
  server directly — but it is **inert where the CNI doesn't enforce NetworkPolicy**,
  in which case the approval boundary can be bypassed by anything that can reach the
  pod; enforce a NetworkPolicy-capable CNI (or add host/firewall-level restrictions)
  where this matters; (2) operators should monitor for direct calls to
  `run_kubectl_command` that don't originate from HolmesGPT (see §6.7) as a
  detection for both misconfiguration and bypass.
    - *Which CNIs enforce NetworkPolicy:* Calico, Cilium, Antrea, and Weave Net
      enforce `NetworkPolicy` out of the box; plain Flannel does **not** without an
      add-on (e.g. Calico-for-policy). Managed clusters vary — EKS needs the VPC CNI
      network-policy controller (or Calico/Cilium) enabled, GKE needs "Network Policy"
      (Calico) or Dataplane V2 (Cilium), AKS needs a network-policy engine selected at
      cluster creation. To check quickly, look for a known policy-controller workload
      (`kubectl get ds -A | grep -E 'calico-node|cilium|antrea|weave'`), or apply a
      deny-all `NetworkPolicy` in a scratch namespace and confirm a previously-reachable
      pod becomes unreachable.
- **Resource exhaustion from diagnostic pods.** The per-pod memory cap and 60s
  timeout (§6.3, §3.2) bound a *single* `run_preapproved_diagnostic_image` invocation, but the
  server imposes no global concurrency or rate limit — an LLM (or a prompt-injected
  one) could launch many diagnostic pods in parallel and pressure node resources.
  Mitigations not yet implemented in the server: a server-side concurrency/rate cap
  (or queue) on `run_preapproved_diagnostic_image`, and a `ResourceQuota`/`LimitRange` on the
  MCP server's namespace. Recommended for multi-tenant or resource-constrained
  clusters. *Status: tracked separately — not blocking this PR. The
  `ResourceQuota`/`LimitRange` is an operator-side deployment control available
  today; the server-side concurrency cap is a follow-up enhancement.*

### 6.6 Things that are correct by construction

- Path traversal: `posixpath.normpath` + residue check rejects `..` escapes.
- Prefix matching: `_path_is_under` uses `root.rstrip("/") + "/"`, so
  `/var/run/secrets-public` doesn't match `/var/run/secrets`.
- Deny-wins-ties: hard-denied → configured-deny → allow, in that order.

### 6.7 Audit logging — current state and recommendations

For a tool that can mutate the cluster, an audit trail matters (incident
investigation, compliance, anomaly detection). What exists today, and where the
gaps are:

- **MCP server logs (today):** the server logs every executed kubectl invocation
  (`Executing kubectl with args: [...]`) and every refusal (denied path, blocked
  flag, non-pre-approved command, non-allowlisted image) at `INFO`/`WARNING` via
  `LOG_LEVEL`. So command invocation, the matched tool, target pod/namespace/path,
  and policy/deny hits are captured **at the server**. Execution *outcome* is in the
  tool result returned to HolmesGPT, not separately logged.
- **HolmesGPT side (today):** approval decisions (who approved `run_kubectl_command`
  and when) live in the HolmesGPT/Robusta interaction history, not in the MCP server.
- **Kubernetes audit (today):** because every action goes through the
  ServiceAccount, the cluster's own API audit log captures the authoritative record
  of what hit the apiserver (verb, resource, user=the SA, timestamp), if cluster
  audit logging is enabled.
- **Gaps / recommendations:** the server does not forward its logs anywhere by
  default and does not emit a single structured "actor + tool + target + approval +
  outcome" audit event. For production: ship the MCP server's stdout to centralized
  logging, enable Kubernetes API audit logging for the SA, retain per your
  compliance window, and alert on anomalous patterns (bursts of
  `run_preapproved_diagnostic_image`, repeated policy-deny `WARNING`s, or any direct
  `run_kubectl_command` not correlated with a HolmesGPT approval — see §6.5).

---

## 7. Test coverage

- **Server unit tests** (`servers/kubernetes-remediation/test_kubernetes_remediation.py`,
  57 tests, no cluster needed): path policy (allow/deny, traversal, deny-wins,
  `/proc,/sys,/dev` hard-deny), symlink re-validation (allowed and denied canonical
  targets), flag-injection rejection, command-allowlist glob matching, image repo→pin
  resolution, verb allowlist, locked-down mode, and the diagnostic-pod hardening
  override.
- **HolmesGPT unit tests**:
  - `tests/test_approval_required_tools.py` — `run_kubectl_command` returns
    `APPROVAL_REQUIRED` without approval; the four read-only tools return `SUCCESS`;
    `user_approved=True` suppresses the re-prompt; deprecated `restricted_tools`
    ignored-with-warning.
  - `tests/test_kubernetes_remediation_helm.py` — values/templates drop
    `restricted_tools`, map approval to `["run_kubectl_command"]`, scoped RBAC (no
    `cluster-admin`), ingress-only NetworkPolicy scoped to the release namespace, new
    env vars wired, old `allowedImages` gone, LLM-instruction text mentions the split.

## 8. Eval coverage — current state and the gap

There are **no LLM evals specific to this toolset yet**. The regression eval suite
doesn't exercise it (it's an opt-in addon, off by default, and not in the eval
toolsets). The existing tests in §7 validate **authorization** (that approvals are
required, that policy refuses the right things) — they do **not** validate **LLM
behavior** (does the model pick the right tool, construct a safe command, reach for
the no-approval tools before the gated one).

**Plan / acceptance checklist** (tracked separately — not blocking this PR):

1. **Auto-approved tools first** (no harness change needed; they don't prompt):
   - [ ] `read_file_from_container`: model reads a named file and reports a unique
     injected value (hallucination-proof), and is refused on a secret/`/proc` path.
   - [ ] `run_preapproved_kubectl_command`: model runs `ps`/`df`-style diagnostics
     and reports a discoverable fact; non-allowlisted command is refused.
   - [ ] `run_preapproved_diagnostic_image`: model launches `nicolaka/netshoot` to probe
     DNS/HTTP and reports the result; non-allowlisted image is refused.
   - [ ] Tool-selection: model uses the **built-in** k8s tools for `get`/`describe`/
     `logs`, not this server.
2. **Gated `run_kubectl_command`** (needs a harness enhancement to auto-approve a
   named tool non-interactively so the mutation path can run in CI):
   - [ ] Model reaches for a pre-approved tool first and only falls back to
     `run_kubectl_command` when necessary.
   - [ ] With auto-approval enabled in the harness, a `rollout restart` / `scale`
     actually mutates and the model verifies the result.
3. **Harness work:** add an eval-only "auto-approve these tool names" hook (or a
   non-LLM integration test that drives the MCP server directly) so #2 is runnable.

## 9. Backwards compatibility

- Old configs with `restrictedTools` / `restricted_tools`: ignored with a deprecation
  warning, not a hard failure.
- Old tool names (`kubectl`, `run_image`, `get_config`) disappear when the 1.1.0
  image is deployed; the chart pins the image so names and the approval list stay
  consistent. Pinning the old `1.0.0` image keeps old behavior until upgrade.

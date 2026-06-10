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
| `run_diagnostic_image` | No (data-gathering pod) | **Auto** | server image allowlist |
| `get_remediation_mcp_config` | No | **Auto** | — |
| `run_kubectl_command` | Yes | **Human approval** | HolmesGPT `approval_required_tools` + server guards |

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

**`run_diagnostic_image(image, namespace, command=None, name=None)`**
- `image` is matched on **repository** against `KUBECTL_DIAGNOSTIC_IMAGES`
  (default pinned: `nicolaka/netshoot:v0.13`, `busybox:1.37.0`,
  `curlimages/curl:8.11.1`); the server runs the **pinned tag** so the model can
  just name the repo.
- The pod is launched **hardened** (see §6.3): no SA token, no privilege
  escalation, memory-capped — but capabilities and root are left intact so
  `tcpdump`/`ping`/`iperf` still work. Output is captured; the pod is auto-deleted
  (`--rm` plus a best-effort `finally` delete).

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

### 3.3 Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `KUBECTL_ALLOWED_COMMANDS` | see above | Hard verb allowlist for the fallback |
| `KUBECTL_DANGEROUS_FLAGS` | see above | Blocked flags |
| `KUBECTL_PREAPPROVED_COMMANDS` | 6 read-only exec patterns | Auto-approved command allowlist |
| `KUBECTL_DIAGNOSTIC_IMAGES` | 3 pinned images | Auto-approved image allowlist |
| `KUBECTL_FILE_READ_ALLOWED_PATHS` | `/` | Read allow roots |
| `KUBECTL_FILE_READ_DENIED_PATHS` | secret/token mounts | Configurable read denylist |
| `KUBECTL_ALLOW_ARBITRARY_COMMANDS` | `true` | Enable the gated fallback |
| `KUBECTL_TIMEOUT` | `60` | Per-command timeout (s) |

In addition, `/proc`, `/sys`, `/dev` are **hard-denied in code** (not operator-removable) —
see §6.2.

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
enabled. Image `1.1.0`. Chart renders the scoped ClusterRole when
`serviceAccount.clusterRole` is empty (bring-your-own otherwise), the ingress-only
NetworkPolicy (on by default), the ConfigMap/env wiring for all the new config keys,
and `approval_required_tools: ["run_kubectl_command"]` in `toolset-config.yaml`. The
LLM instructions in `_helpers.tpl` describe the auto-vs-gated split.

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
`read_file_from_container` and `run_diagnostic_image`.

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

`run_diagnostic_image` now injects a server-controlled `--overrides` (strategic
merge) that sets `automountServiceAccountToken: false` (the pod never needs API
access; removes an escalation vector), `allowPrivilegeEscalation: false`, and a
memory limit + requests. It deliberately does **not** drop capabilities, force
non-root, or set a CPU limit — so `tcpdump`/`ping` keep their caps and `iperf` isn't
throttled.

### 6.4 Residual risks (accepted, documented)

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
  `allowArbitraryKubectlCommands` toggle.

### 6.5 Things that are correct by construction

- Path traversal: `posixpath.normpath` + residue check rejects `..` escapes.
- Prefix matching: `_path_is_under` uses `root.rstrip("/") + "/"`, so
  `/var/run/secrets-public` doesn't match `/var/run/secrets`.
- Deny-wins-ties: hard-denied → configured-deny → allow, in that order.

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
toolsets). See §9 of the review notes / the PR discussion for the plan: the three
auto-approved tools are naturally eval-able (they don't prompt), while the gated
`run_kubectl_command` needs either an auto-approve harness hook or a non-LLM test,
because evals run non-interactively and a human-approval gate would block the
mutation path.

## 9. Backwards compatibility

- Old configs with `restrictedTools` / `restricted_tools`: ignored with a deprecation
  warning, not a hard failure.
- Old tool names (`kubectl`, `run_image`, `get_config`) disappear when the 1.1.0
  image is deployed; the chart pins the image so names and the approval list stay
  consistent. Pinning the old `1.0.0` image keeps old behavior until upgrade.

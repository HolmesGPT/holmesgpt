# Bash Tool Sandboxing

## Intent

HolmesGPT lets the LLM run shell commands through the `bash` toolset. Today the
only thing standing between the model and the host is **string-level
validation**: `holmes/plugins/toolsets/bash/validation.py` parses each command
with `bashlex`, splits it into segments, and checks every segment against a
prefix allow/deny list (`common/default_lists.py`), with `sudo`/`su`
hard-blocked. The approved string is then executed by
`common/bash.py:execute_bash_command()` via
`subprocess.Popen(cmd, shell=True, executable="/bin/bash")`.

This is a guardrail, not a security boundary:

- The command runs **in the main Holmes process context** — same UID, same
  filesystem, same network, and crucially the **same environment variables**.
  `execute_bash_command` passes no `env=` argument, so every child inherits the
  parent's kubeconfig, cloud credentials, and LLM API keys.
- The allowlist is bypassable in principle (parser ambiguities, allowed binaries
  with shell escapes such as `find -exec` or `kubectl exec`, env-var abuse).
- In the HTTP server (`server.py`) there is a single long-lived process with a
  shared, cached `ToolExecutor`. Only the tool-result temp directory is
  per-request. There is **no OS-level isolation between concurrent sessions** —
  an "allowed" command in one session can read another session's secrets off
  disk or out of the environment.

The goal of this work is to move from "is this command string allowed?" to
"what can this process actually touch?" by executing each bash command inside an
OS-level jail.

## Approach: per-command OS jail via bubblewrap

We evaluated four tiers of isolation: (0) in-process hardening, (1) OS-primitive
jail via namespaces + seccomp + cgroups (bubblewrap / nsjail), (2) a container
per session (Docker/Podman, optionally gVisor), (3) a microVM per session
(Firecracker/Kata), and (4) a remote sandbox-as-a-service.

**We chose Approach 1 with [bubblewrap](https://github.com/containers/bubblewrap)
(`bwrap`) as the backend.** Rationale:

- It is unprivileged (uses user namespaces), so it runs without root and inside
  a non-privileged Kubernetes pod.
- It is the one approach that runs in **all three** environments we care about:
  the developer/agent sandbox, the GitHub Actions eval runners, and a production
  Holmes pod.
- It is a single, ubiquitously-packaged static binary (`apt install
  bubblewrap`), battle-tested as the engine behind Flatpak.
- Per-command jail setup is cheap (single-digit milliseconds), which matters
  because the agentic loop fans out up to 16 tool calls in parallel.

The jail wraps the **bash toolset only** for now (the single
`execute_bash_command` call site), not every toolset that shells out. Widening
it to a shared subprocess primitive is a possible follow-up.

### What the jail does

Each command runs as `bwrap [isolation args] /bin/bash -c "<ulimit prefix><cmd>"`
with:

- **Fresh namespaces**: user, pid, ipc, uts, cgroup. The process sees itself as
  the only thing running, and its capabilities map to nothing on the host.
- **A scrubbed environment**: `--clearenv` followed by an explicit allowlist of
  variables re-injected with `--setenv`. This is the single most important
  control — namespaces do **not** scrub the environment, and credential bleed
  through env is the current worst case.
- **A read-only root**: curated `--ro-bind` of system directories (`/usr`,
  `/bin`, `/sbin`, `/lib`, `/lib64`) and a curated set of `/etc` files needed for
  DNS/TLS (`resolv.conf`, `ssl`, `ca-certificates`, `hosts`, `nsswitch.conf`,
  `passwd`, `group`). The host filesystem — `/home`, the Holmes config dir,
  `~/.kube`, `~/.aws`, `/var/run/secrets/...` — is **not** mounted unless
  explicitly bound.
- **Ephemeral writable space**: a private `tmpfs` at `/tmp` and a `tmpfs` working
  directory (`/work`, the cwd). Nothing the command writes touches the host.
- **`--die-with-parent --new-session`** so a jailed process cannot outlive Holmes
  or escape its controlling terminal.

`no_new_privs` and capability dropping come for free — bwrap sets them by
default — so they are not separate code.

### Interaction with tool-result spilling

HolmesGPT spills oversized tool results to disk
(`core/tools_utils/filesystem_result_storage.py`) and hands the LLM an absolute
path with the instruction to read it back via bash:

> `Saved to: /tmp/.holmes/<chat-uuid>/tool_results/<tool>_<id>.txt`
> `Use \`cat <path>\` to read it ... \`cat <path> | jq ...\`, \`cat <path> | grep ...\``

A naive jail with an ephemeral `tmpfs /tmp` **breaks this workflow** — the
spilled file lives on the host, not in the jail, so `cat <path>` fails with "No
such file or directory". This is a real regression the sandbox must avoid.

The fix is to bind-mount the **per-session** spill directory into the jail at the
**same absolute path**. The per-session `tool_results_dir` (already created once
per chat/request) is threaded from the tool-call layer down through
`ToolInvokeContext` → the bash tool → `execute_bash_command` →
`build_sandbox_argv`, which adds a `--bind <dir> <dir>` *after* the `tmpfs /tmp`
so it layers on top. Crucially we bind only that session's directory, never the
shared `HOLMES_TOOL_RESULT_STORAGE_PATH` base — so one session's jail cannot see
another session's spilled files, which keeps the isolation property intact.

Images are unaffected: they are read back by the `read_image_file` Python tool,
which reads the host filesystem directly rather than shelling out into the jail.

Note this is per-command isolation, so scratch files written by one bash command
to the ephemeral `/work` or `/tmp` are **not** visible to a later command in the
same session. Persisting a per-session work dir is part of the per-session
sandbox follow-up; the spill directory above is the one cross-command path that
must survive, and it does.

### Decisions

These are the design forks and how we resolved them:

| Decision | Choice | Notes |
|----------|--------|-------|
| **Backend** | bubblewrap | Portability and simplicity over nsjail's bundled resource controls. |
| **Granularity** | Per-command (first increment) | Easiest to bolt onto the single `execute_bash_command` call site and gives the largest blast-radius reduction. A persistent per-session sandbox (keyed by `conversation_id`) is the natural next step for the HTTP-server isolation goal. |
| **Network policy** | **None — share the host network** | Holmes's entire job is querying remote backends (K8s API, Prometheus, Grafana). A deny-all net namespace would break the product. We explicitly `--share-net` and rely on credential scoping (not network isolation) as the boundary. Per-session egress control is explicitly out of scope. |
| **Resource limits** | **Keep today's `ulimit` / OOM handling** | We do **not** introduce cgroup v2 resource caps in this iteration. The existing `ulimit -v` prefix from `memory_limit.py` and the OOM-hint UX are preserved and continue to run inside the jail. cgroups remain a separate, later, feature-detected layer. |
| **Seccomp** | Not in the POC | bwrap's default namespace isolation is the POC boundary. A curated seccomp allowlist (passed as a compiled BPF fd) is a follow-up hardening layer, gated on testing against the allowed binaries. |
| **Landlock** | Not used | Optional future hardening; must be runtime-feature-detected because it is absent on some kernels (e.g. the current agent sandbox) even though present on CI/modern kernels. Never a hard dependency. |
| **Credential injection** | Env allowlist only (POC) | The env passthrough allowlist is configurable. Binding a per-session, scoped kubeconfig / service-account token into the jail is the follow-up that makes the bash toolset fully functional under sandboxing. |
| **Rollout** | Opt-in, default off | New `HOLMES_BASH_SANDBOX_ENABLED` flag. With graceful degradation: if the flag is on but the jail is unavailable (no `bwrap`, or unprivileged user namespaces are disabled), behaviour falls back to today's direct execution and logs a warning. |
| **Scope** | Bash toolset only | Not yet a shared primitive for all subprocess-spawning toolsets. |

### What remains from in-process hardening (Approach 0)

With the jail in place, the Approach-0 checklist collapses to:

- **Subsumed by bwrap** (now just jail config, not separate code): `no_new_privs`,
  capability dropping.
- **Still required, and more important** because the jail will not do it for you:
  **environment scrubbing/allowlisting** (the jail's `--clearenv` + `--setenv`),
  and **per-session credential partitioning** (the jail provides the mechanism;
  the credential model is a follow-up).
- **Dropped**: restricted shell (`rbash`) — a weak, bypassable control that the
  jail makes redundant.
- **Demoted, not removed**: the existing bashlex allowlist stays as
  defense-in-depth and to drive the approval UX, but it is no longer the boundary
  that keeps sessions apart.

## Feature detection and graceful degradation

Unprivileged user namespaces are the hard dependency and are **not** universally
available — hardened clusters set `kernel.unprivileged_userns_clone=0` or
seccomp-block `unshare`, and a restrictive container runtime (gVisor, a tight
seccomp profile) hosting Holmes itself can block nested namespace creation.

Detection is therefore a runtime probe, not a static assumption: we check that
`bwrap` is on `PATH` and that a trivial jailed `true` actually runs, and we cache
the result. If the probe fails while sandboxing is enabled, Holmes logs a clear
warning and falls back to direct execution rather than refusing to run bash.

Verified behaviour across the three target environments:

| Capability | Agent sandbox | GitHub Actions (`ubuntu-latest`) | Production pod |
|------------|:-:|:-:|:-:|
| Unprivileged user namespaces | ✅ verified | ✅ | depends on runtime — probe at startup |
| `bwrap` installable / present | ✅ (apt) | ✅ (apt) | bake into image |
| Network share (`--share-net`) | ✅ | ✅ | ✅ |
| cgroup v2 resource caps | ⚠️ weak (v1, no `CAP_SYS_RESOURCE`) | ✅ | depends | (not used in POC) |
| Landlock | ❌ `ENOSYS` | ✅ | depends | (not used) |

## Structure

The POC adds one module and threads it into the single execution call site:

- **`holmes/plugins/toolsets/bash/common/sandbox.py`** — all sandboxing logic.
  - `is_sandbox_available()` — cached runtime probe (bwrap present + a jailed
    `true` succeeds).
  - `build_sandbox_argv()` — assembles the `bwrap` argument vector (namespaces,
    curated read-only binds, tmpfs, env allowlist) in front of the bash invocation.
  - `sandbox_enabled()` — reads the opt-in flag.
- **`holmes/plugins/toolsets/bash/common/bash.py`** — `execute_bash_command`
  builds the same `ulimit`-prefixed command as before, then either wraps it in
  the jail (argv form, `shell=False`) or runs it directly (today's
  `shell=True`), depending on availability.
- **`holmes/common/env_vars.py`** — `HOLMES_BASH_SANDBOX_ENABLED` (default off)
  and `HOLMES_BASH_SANDBOX_ENV_PASSTHROUGH` (comma-separated extra env vars to
  carry into the jail beyond the safe defaults).

## Out of scope (follow-ups)

- Persistent per-session sandbox keyed by `conversation_id` (the real fix for
  HTTP-server session isolation).
- Scoped per-session credential injection (bind a session kubeconfig / token).
- cgroup v2 resource limits replacing `ulimit`.
- A seccomp-bpf profile and optional Landlock filesystem layer.
- Extending the jail to all subprocess-spawning toolsets, not just bash.

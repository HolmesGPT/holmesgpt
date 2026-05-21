# Tool Execution Safety

HolmesGPT runs many of its tools as shell subprocesses — bash commands, `kubectl`, `grep`, `jq`, and similar. To prevent a single runaway command from exhausting all available memory and crashing Holmes itself (or the pod it runs in), every tool subprocess is wrapped with a `ulimit -v` prefix that caps the virtual memory the command is allowed to allocate.

This page explains how that mechanism works, how to tune it via the `TOOL_MEMORY_LIMIT_MB` environment variable, and the operational implications you need to know before changing the default.

## Default Limit

The default value of `TOOL_MEMORY_LIMIT_MB` depends on CPU architecture:

- **x86_64:** `800` MB
- **ARM / aarch64:** `1500` MB

The ARM default is higher because ARM toolchains — especially Go binaries like `kubectl` — reserve more virtual address space at startup.

## What Happens When a Command Exceeds the Limit

When a subprocess tries to allocate beyond the cap, the kernel sends `SIGKILL` (exit code `137`). Holmes detects this by checking:

- Exit code `137` or `-9`, OR
- Strings like `Killed`, `MemoryError`, `Cannot allocate memory`, `bad_alloc`, or `out of memory` on a non-zero exit.

When detected, Holmes prefixes the tool output with an `[OOM]` hint. The hint tells the LLM:

1. This is a designed safety feature, not an error or bug.
2. Retry the query with filters that narrow the result set (namespace, label selector, resource name, shorter time range).
3. Only suggest raising `TOOL_MEMORY_LIMIT_MB` if narrowing the query fails to recover useful results.

Large goroutine stack dumps (common from Go programs like `kubectl`) are truncated to keep the response token-cheap.

## Caveat: Client-Side Filtering Can Hide the Real Cost

Some CLIs fetch the full result set from the server and then filter or format it **locally**, in the same process. The visible output is small but the memory footprint is not. The most common offenders:

- **`kubectl`** with output projections like `-o jsonpath=...`, `-o custom-columns=...`, or `-o name` over wide selectors. The server returns full objects; the formatter discards fields locally.
- **`argocd app list`** (and similar Argo CD list subcommands). The CLI pulls every Application manifest into memory and then projects to the requested output format. `argocd app list -o name` prints just names but loads full manifests to do so.
- **`kubectl get ... | grep ...`**, `kubectl get ... -o json | jq ...` — the pipe filters *after* the full object has been fetched and serialized.

!!! warning "Why this confuses users"
    A user who sees `[OOM]` on `argocd app list -o name` and then runs the **exact same command** manually in a terminal will see it succeed. Their terminal has no `ulimit -v` cap, so the command works — even though it allocated far more memory than the output suggests. This looks like a Holmes bug, but it isn't: the command was always memory-heavy, the cap just exposed it.

## Implications

These are the things to keep in mind when deciding whether to change the default.

**Per-subprocess, not aggregate.**

Each tool call gets its own ceiling. Two concurrent tool calls can each allocate up to the limit, so the total memory in use can be a multiple of `TOOL_MEMORY_LIMIT_MB`.

**Coordinate with your container memory limit.**

On Kubernetes the tool limit operates *inside* the pod's cgroup memory limit. Keep `TOOL_MEMORY_LIMIT_MB` comfortably below your pod's `resources.limits.memory`:

- The Helm chart defaults to `resources.limits.memory: 1024Mi`.
- The default `TOOL_MEMORY_LIMIT_MB` of 800 leaves headroom for Holmes itself, the Python runtime, and LLM client buffers.

If you raise one, raise the other in lockstep.

**macOS silently ignores the cap.**

The BSD kernel does not enforce `ulimit -v`, and the `|| true` fallback hides the failure. Local tests on macOS will not reproduce the OOM behavior you'd see in a Linux container or pod. See [Platform Notes](#platform-notes).

**`[OOM]` is the safety net working, not a bug.**

When you see `[OOM]` in tool output, the limit did its job. First narrow the query; raise the limit only if a legitimately small result still trips it.

## When to Raise the Limit

- Large-cluster `kubectl get ... -o json` output that cannot be filtered further.
- Big `jq` transformations over multi-megabyte inputs.
- Log dumps from chatty services.

When you raise `TOOL_MEMORY_LIMIT_MB`, raise the pod's `resources.limits.memory` in lockstep — see [Helm Configuration](../reference/helm-configuration.md#resource-configuration).

## Configuration

=== "Holmes CLI"

    ```bash
    export TOOL_MEMORY_LIMIT_MB=2000
    ```

=== "Holmes Helm Chart"

    Add to your Helm `values.yaml`:

    ```yaml
    additionalEnvVars:
      - name: TOOL_MEMORY_LIMIT_MB
        value: "2000"
    ```

    Apply with:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml -n <namespace>
    ```

=== "Robusta Helm Chart"

    When using the Robusta Helm Chart (which includes HolmesGPT as a sub-chart), env vars for Holmes are nested under the `holmes:` key. Add to your `generated_values.yaml`:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: TOOL_MEMORY_LIMIT_MB
          value: "2000"
    ```

    Apply with:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml -n <namespace>
    ```

    The value flows through to the Holmes pod automatically — no other changes required.

!!! note "Keep the pod memory limit in sync"
    Whenever you raise `TOOL_MEMORY_LIMIT_MB`, also raise `resources.limits.memory` on the Holmes pod so the cap actually has room to operate. For the Robusta chart, pod resources live under `holmes.resources` in `generated_values.yaml`. See [Helm Resource Configuration](../reference/helm-configuration.md#resource-configuration).

## Platform Notes

- **Linux (containers, pods, most servers):** `ulimit -v` is enforced. The cap behaves as documented.
- **macOS:** `bash` and `zsh` accept `ulimit -v` but the BSD kernel does not enforce it. The `|| true` fallback swallows the failure, so commands run uncapped. Production behavior must be validated on Linux.
- **Other shells / minimal containers:** If `ulimit -v` is unavailable, the `|| true` fallback also applies and the cap is effectively disabled. In that case rely on the container's cgroup memory limit instead.

## See Also

- [`TOOL_MEMORY_LIMIT_MB`](../reference/environment-variables.md#tool_memory_limit_mb) — environment variable reference entry.
- [Helm Resource Configuration](../reference/helm-configuration.md#resource-configuration) — pod-level memory limits.

# Stress Testing Holmes with Intentional OOM Kills

> ⚠️ **Never enable this toolset in production.** It allocates ~30 GB of RAM on the Holmes host and is intended only for controlled, non-production stress tests.

This guide explains how to intentionally trigger OOM kills using the built-in OOM toolsets, how to enable them safely, and how to confirm they are available.

## Available Toolsets

Holmes ships with two disabled-by-default toolsets for inducing an OOM kill:

- **`oom_kill` (Python)** – `trigger_oom_kill` allocates ~30 GB and sleeps for a configurable duration.
- **`oom_kill_bash` (YAML/bash)** – `trigger_oom_kill_bash` does the same via a bash-executed Python snippet and applies `ulimit -v 2097152` (2 GiB virtual memory cap) before allocation to reduce blast radius.

Both toolsets require the environment variable `ALLOW_HOLMES_OOMKILL_TOOLSET` to pass prerequisites and must be explicitly enabled in configuration. They are **disabled by default** and will not be loaded unless you opt in.

## Enabling via Helm/ArgoCD (cluster install)

Use the correct values path for your deployment method.

=== "Robusta Helm Chart (Holmes as subchart)"

1. **Set the env guard** (required):
   ```bash
   argocd app set <APP_NAME> \
     --helm-set-string holmes.additionalEnvVars[0].name=ALLOW_HOLMES_OOMKILL_TOOLSET \
     --helm-set-string holmes.additionalEnvVars[0].value=true
   ```

2. **Enable the toolsets**:
   ```bash
   argocd app set <APP_NAME> \
     --helm-set-string holmes.toolsets.oom_kill.enabled=true \
     --helm-set-string holmes.toolsets.oom_kill_bash.enabled=true
   ```

3. **Sync to apply**:
   ```bash
   argocd app sync <APP_NAME>
   ```

4. **Verify** (optional):
   ```bash
   kubectl -n <holmes-namespace> exec -it <holmes-pod> -- \
     cat /app/custom_toolset.yaml
   # Expect oom_kill and oom_kill_bash present and enabled
   ```

=== "Holmes Helm Chart (direct)"

1. **Set the env guard** (required):
   ```bash
   argocd app set <APP_NAME> \
     --helm-set-string additionalEnvVars[0].name=ALLOW_HOLMES_OOMKILL_TOOLSET \
     --helm-set-string additionalEnvVars[0].value=true
   ```

2. **Enable the toolsets**:
   ```bash
   argocd app set <APP_NAME> \
     --helm-set-string toolsets.oom_kill.enabled=true \
     --helm-set-string toolsets.oom_kill_bash.enabled=true
   ```

3. **Sync to apply**:
   ```bash
   argocd app sync <APP_NAME>
   ```

4. **Verify** (optional):
   ```bash
   kubectl -n <holmes-namespace> exec -it <holmes-pod> -- \
     cat /app/custom_toolset.yaml
   # Expect oom_kill and oom_kill_bash present and enabled
   ```

## Enabling in Local CLI Mode

Add to your local config (e.g., `config.yaml`) and set the env guard before running the CLI:

```yaml
toolsets:
  oom_kill:
    enabled: true
  oom_kill_bash:
    enabled: true
```

Then run:
```bash
export ALLOW_HOLMES_OOMKILL_TOOLSET=true
holmes --config ./config.yaml ...
```

Because both toolsets are disabled by default and gated by `ALLOW_HOLMES_OOMKILL_TOOLSET`, they will **not** be auto-enabled in local mode unless you explicitly enable them and set the env variable.

## Using the Tools

- **Python toolset**: `trigger_oom_kill` (param: `hold_seconds`, default 300).
- **Bash toolset**: `trigger_oom_kill_bash` (param: `hold_seconds`, default 300).

Example invocation (conceptual):
```
trigger_oom_kill: allocate ~30GB and sleep for 120s
```

## Safety Considerations

- Keep this toolset out of production environments.
- Ensure hosts have proper isolation; the process is expected to be OOM-killed.
- Consider running in a dedicated test cluster or namespace.

# Bash Toolset

!!! info "Enabled by Default"
    This toolset is enabled by default and should typically remain enabled.

The bash toolset allows Holmes to execute shell commands for troubleshooting and system analysis. Commands are validated against configurable allow/deny lists before execution.

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      bash:
        enabled: true
        config:
          builtin_allowlist: "core"  # "none", "core", or "extended"
          # allow:
          #   - "helm list"
          #   - "kubectl rollout history"
          #   - "curl https://prometheus.monitoring.svc:9090/api/v1"
          deny:
            - "kubectl get secret"
            - "kubectl describe secret"
    ```

    Approved commands are saved to `~/.holmes/bash_approved_prefixes.yaml` and persist across sessions.

    **CLI Flags:**

    | Flag | Description |
    |------|-------------|
    | `--bash-always-deny` | Automatically deny commands not in the allow list |
    | `--bash-always-allow` | Automatically approve all commands (use with caution) |

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        bash:
          enabled: true
          config:
            builtin_allowlist: "extended"
            # allow:
            #   - "helm list"
            #   - "kubectl rollout history"
            #   - "curl https://prometheus.monitoring.svc:9090/api/v1"
            deny:
              - "kubectl get secret"
    ```

    `extended` is recommended for Helm deployments where Holmes runs in a container with a minimal filesystem.

## Builtin Allowlist Levels

The `builtin_allowlist` field controls which commands are pre-approved:

**`core`** (CLI default) - safe on local machines and containers:

| Category | Commands |
|----------|----------|
| Kubernetes | `kubectl get`, `kubectl describe`, `kubectl logs`, `kubectl top`, `kubectl explain`, `kubectl api-resources`, `kubectl config view`, `kubectl config current-context`, `kubectl cluster-info`, `kubectl version`, `kubectl auth can-i`, `kubectl diff`, `kubectl events` |
| JSON | `jq` |
| Text processing | `grep`, `head`, `tail`, `sort`, `uniq`, `wc`, `cut`, `tr`, `echo` |
| System info | `id`, `whoami`, `hostname`, `uname`, `date`, `which`, `type` |

**`extended`** (Helm default) - adds these on top of `core`:

| Category | Commands | Why container-only |
|----------|----------|--------------------|
| File reading | `cat`, `base64` | Can read sensitive files (~/.ssh, ~/.aws) on local machines |
| Filesystem | `ls`, `find`, `stat`, `du`, `df` | Exposes local filesystem structure |
| Archives | `tar -tf`, `tar -tvf`, `gzip -l`, `zcat`, `zgrep` | Can inspect local archives |

**`none`** - empty builtin list. Only commands in your `allow` list and previously approved commands are allowed.

User-provided `allow` and `deny` entries are always merged with the selected builtin level.

## Command Approval

When Holmes tries to run a command not in your allow list, you'll see a prompt:

```text
Bash command

  kubectl scale deployment nginx --replicas=3
  Scale nginx deployment to 3 replicas

Do you want to proceed?
  1. Yes
  2. Yes, and don't ask again for `kubectl scale deployment nginx` commands
  3. Type here to tell Holmes what to do differently
```

- **Option 1**: Run this command once
- **Option 2**: Run and add the prefix to your allow list (saved to `~/.holmes/bash_approved_prefixes.yaml`)
- **Option 3**: Reject and provide feedback to Holmes

## Prefix Matching

Commands are matched by prefix. For example, if `kubectl get` is in your allow list:

| Command | Allowed? |
|---------|----------|
| `kubectl get pods` | Yes |
| `kubectl get pods -n production` | Yes |
| `kubectl get deployments --all-namespaces` | Yes |
| `kubectl delete pod my-pod` | No (different subcommand) |

For piped commands, each segment is checked:

```bash
kubectl get pods | grep error | head -10
```

This requires `kubectl get`, `grep`, and `head` to all be allowed.

A prefix can be as narrow as you like — it is matched against the start of the
command and must end on a whitespace or `/` boundary, so it can pin a subcommand
or the leading part of a URL. It constrains the start of the command and nothing
else; read the warning under the table before relying on a URL-scoped entry.

| Allow entry | Allows | Still needs approval |
|-------------|--------|----------------------|
| `helm list` | `helm list -A`, `helm list -n prod -o json` | `helm upgrade my-release ./chart` |
| `kubectl rollout history` | `kubectl rollout history deployment/nginx` | `kubectl rollout restart deployment/nginx` |
| `curl https://prometheus.monitoring.svc:9090/api/v1` | `curl https://prometheus.monitoring.svc:9090/api/v1/targets` | `curl https://example.com` |

!!! warning "A prefix does not restrict where a command goes"
    It constrains the start of the command and nothing else. With the `curl`
    entry above, `curl https://prometheus.monitoring.svc:9090/api/v1/targets
    https://example.com` also matches, and so does the same command with
    `--next` or `-o`, because each still *starts* with the allowed prefix.
    `deny` entries are matched the same way, so they don't catch it either.
    A URL-scoped prefix cuts approval prompts for the endpoint you use most;
    it is not an egress control. If Holmes must not reach other destinations,
    leave `curl` out of the allow list and approve each command as it comes up.

Because matching starts at the beginning of the command, put the part you are
scoping on first and flags last — `curl https://host/api/v1/targets -s` matches
the prefix above, `curl -s https://host/api/v1/targets` does not.

## Large Tool Result Storage

When a tool response exceeds the LLM context window limit, Holmes saves the result to disk and gives the LLM a file path. The bash toolset automatically allows read-only commands (`cat`, `head`, `tail`, `wc`, `jq`) on the storage directory so the LLM can access saved results without approval prompts.

| Variable | Default | Description |
|----------|---------|-------------|
| `HOLMES_TOOL_RESULT_STORAGE_ENABLED` | `true` | Enable/disable saving large results to disk. When disabled, oversized results are dropped and the LLM is asked to retry with a narrower query. |
| `HOLMES_TOOL_RESULT_STORAGE_PATH` | `/tmp/.holmes` | Directory for saved results. Auto-cleaned per session. |

```bash
# Disable filesystem storage entirely
export HOLMES_TOOL_RESULT_STORAGE_ENABLED=false
```

See [Environment Variables](../../reference/environment-variables.md#tool-result-size-limits) for the full list of size-limit settings.

## Blocked Commands

The following are always blocked and cannot be overridden:

- `sudo` and `su`
- Subshells: `$(...)`, backticks, `<(...)`, `>(...)`

## Tools

### bash

Executes a shell command.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| command | string | Yes | The command to execute |
| suggested_prefixes | array | Yes | Prefixes for validation (one per command segment) |
| timeout | integer | No | Timeout in seconds (default: 30) |

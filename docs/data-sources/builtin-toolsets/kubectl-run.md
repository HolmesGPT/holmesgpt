# Kubectl Run Toolset

!!! warning "Disabled by Default"
    This toolset is disabled by default and must be explicitly enabled.

The kubectl-run toolset allows Holmes to run commands in temporary Kubernetes pods. This is useful for network debugging, DNS checks, and running diagnostic tools not available on the cluster.

## Configuration

With the chart, `kubectl run` needs permissions the chart's default RBAC does not grant: it creates the pod, attaches to it and deletes it when the command ends. The Helm values below add those rules. Unless `namespaceScopedRBAC` is `true`, the chart puts them in a ClusterRole, so Holmes can create pods in every namespace, and a pod it creates can run as any service account in its namespace; with `namespaceScopedRBAC: true` they are limited to the release namespace, and `kubectl run` works only there.

The `curl .*` pattern below lets the model run `curl` with any arguments, so the temporary pod can request any URL it can reach, internal services and cloud metadata endpoints included. List only the URLs Holmes needs (for example `curl -s https://status\.example\.com/[^ ]*`, which also keeps the model from adding arguments) where that is too broad.

```yaml-toolset-config
customClusterRoleRules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["create", "delete"]
  - apiGroups: [""]
    resources: ["pods/attach"]
    verbs: ["create", "get"]

toolsets:
  kubectl-run:
    enabled: true
    config:
      allowed_images:
        - image: "busybox:1.36"
          allowed_commands:
            - "nslookup .*"
            - "ping -c 3 .*"
        - image: "curlimages/curl:8.8.0"
          allowed_commands:
            - "curl .*"
---
cli: |
  Add the following to **~/.holmes/config.yaml**:

  ```yaml
  toolsets:
    kubectl-run:
      enabled: true
      config:
        allowed_images:
          - image: "busybox:1.36"
            allowed_commands:
              - "nslookup .*"
              - "ping -c 3 .*"
              - "wget -qO- .*"
          - image: "curlimages/curl:8.8.0"
            allowed_commands:
              - "curl .*"
  ```
```

## Security

For security, you must explicitly whitelist:

1. **Images**: Only specified container images can be used
2. **Commands**: Only commands matching the regex patterns are allowed

If no images are configured, all kubectl run commands are blocked.

Additional safeguards:

- **Patterns are fully anchored.** A command must match a pattern in its entirety (`re.fullmatch`), so a matching prefix cannot be followed by extra content. Write patterns to cover the whole command — e.g. `nslookup .*`, not just `nslookup`.
- **No shell.** Commands run directly as an argument vector (`shell=False`), never through a host shell, so a loosely written pattern (e.g. one ending in `.*`) cannot be abused to run commands on the Holmes host. As a defense-in-depth check, command-combination and substitution tokens (`;`, `|`, `` ` ``, `<`, `>`, newlines, `$(`, `${`, `&&`) are also rejected outright. Ordinary argument characters — including a lone `&` in a URL query string such as `?a=1&b=2` — are unaffected.

## Tools

### kubectl_run_image

Runs a command in a temporary Kubernetes pod.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| image | string | Yes | Container image to use (must be in allowed_images) |
| command | string | Yes | Command to run (must match allowed_commands pattern) |
| namespace | string | No | Namespace for the pod (default: default) |
| timeout | integer | No | Timeout in seconds (default: 60) |

The temporary pod is automatically deleted after the command completes (`--rm` flag).

## Example Use Cases

- **DNS debugging**: Run `nslookup` to check service discovery
- **Network connectivity**: Use `curl` or `wget` to test endpoints
- **Database connectivity**: Test connections from within the cluster

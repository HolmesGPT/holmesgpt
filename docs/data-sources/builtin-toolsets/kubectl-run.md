# Kubectl Run Toolset

!!! warning "Disabled by Default"
    This toolset is disabled by default and must be explicitly enabled.

The kubectl-run toolset allows Holmes to run commands in temporary Kubernetes pods. This is useful for network debugging, DNS checks, and running diagnostic tools not available on the cluster.

## Configuration

=== "Holmes CLI"

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

=== "Robusta Helm Chart"

    ```yaml
    holmes:
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
    ```

## Security

For security, you must explicitly whitelist:

1. **Images**: Only specified container images can be used
2. **Commands**: Only commands matching the regex patterns are allowed

If no images are configured, all kubectl run commands are blocked.

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

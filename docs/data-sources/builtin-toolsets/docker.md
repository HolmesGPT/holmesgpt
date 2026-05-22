# Docker

!!! warning "Not Recommended for Kubernetes"
    This integration is not recommended for monitoring a Kubernetes cluster because it is neither necessary nor useful. It is documented here for users of HolmesGPT CLI.

Read access to Docker resources.

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
        docker/core:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| docker_images | List all Docker images |
| docker_ps | List all running Docker containers |
| docker_ps_all | List all Docker containers, including stopped ones |
| docker_inspect | Inspect detailed information about a Docker container or image |
| docker_logs | Fetch the logs of a Docker container |
| docker_top | Display the running processes of a container |
| docker_events | Get real-time events from the Docker server |
| docker_history | Show the history of an image |
| docker_diff | Inspect changes to files or directories on a container's filesystem |

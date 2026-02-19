# Custom Toolsets

If the built-in toolsets don't meet your needs, you can extend HolmesGPT's investigation capabilities by creating custom toolsets. This is especially useful for unique use cases, proprietary tools, or specialized infrastructure setups. Examples include advanced log analysis tools, external monitoring integrations, or custom diagnostic scripts.

By creating custom toolsets, you can ensure HolmesGPT has access to all the data sources and tools necessary for thorough investigations in your specific environment.

## Examples

Below are three examples of how to create custom toolsets for different scenarios.

### Example 1: Grafana Toolset

This example creates a toolset that helps HolmesGPT view and suggest relevant Grafana dashboards.

=== "Holmes CLI"

    **Configuration File (`toolsets.yaml`):**

    ```yaml
    toolsets:
      grafana:
        description: "View and suggest Grafana dashboards"
        prerequisites: "Grafana instance accessible from HolmesGPT"
        tags: [monitoring, observability]
        installation: |
          1. Ensure Grafana is accessible from HolmesGPT
          2. Configure Grafana API credentials if authentication is required
        tools:
          - name: view_dashboard
            description: "View a specific Grafana dashboard by ID or name"
            command: |
              curl -s "${GRAFANA_URL}/api/dashboards/uid/{{ dashboard_uid }}" \
                -H "Authorization: Bearer ${GRAFANA_TOKEN}"

          - name: search_dashboards
            description: "Search for dashboards related to specific keywords"
            command: |
              curl -s "${GRAFANA_URL}/api/search?query={{ search_query }}" \
                -H "Authorization: Bearer ${GRAFANA_TOKEN}"
    ```

    **Environment Variables:**

    ```bash
    export GRAFANA_URL="http://grafana.monitoring.svc.cluster.local:3000"
    export GRAFANA_TOKEN="your-grafana-api-token"
    ```

    **Run HolmesGPT:**

    ```bash
    holmes ask "show me dashboards related to CPU usage" --custom-toolsets=toolsets.yaml
    ```

    After making changes to your toolsets file, run:
    ```bash
    holmes toolset refresh
    ```

=== "Robusta Helm Chart"

    **Helm Values:**

    ```yaml
    holmes:
      customToolsets:
        grafana:
          description: "View and suggest Grafana dashboards"
          prerequisites: "Grafana instance accessible from HolmesGPT"
          tags: [monitoring, observability]
          installation: |
            1. Ensure Grafana is accessible from HolmesGPT
            2. Configure Grafana API credentials if authentication is required
          tools:
            - name: view_dashboard
              description: "View a specific Grafana dashboard by ID or name"
              command: |
                curl -s "{{ grafana_url }}/api/dashboards/uid/{{ dashboard_uid }}" \
                  -H "Authorization: Bearer {{ grafana_token }}"

            - name: search_dashboards
              description: "Search for dashboards related to specific keywords"
              command: |
                curl -s "{{ grafana_url }}/api/search?query={{ search_query }}" \
                  -H "Authorization: Bearer {{ grafana_token }}"
    ```

    **Environment Variables:**

    ```bash
    export GRAFANA_URL="http://grafana.monitoring.svc.cluster.local:3000"
    export GRAFANA_TOKEN="your-grafana-api-token"
    ```

    **Helm Upgrade:**

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

### Example 2: Kubernetes Diagnostics Toolset

This example creates a toolset with advanced diagnostic tools for Kubernetes clusters.

=== "Holmes CLI"

    **Configuration File (`toolsets.yaml`):**

    ```yaml
    toolsets:
      k8s-diagnostics:
        description: "Advanced Kubernetes diagnostic tools"
        prerequisites: "kubectl access to the cluster"
        tags: [kubernetes, diagnostics]
        installation: |
          1. Ensure kubectl is configured with cluster access
          2. Verify necessary RBAC permissions are in place
        tools:
          - name: check_node_pressure
            description: "Check for node pressure conditions and resource usage"
            command: |
              kubectl get nodes -o json | jq -r '
                .items[] |
                select(.status.conditions[]? | select(.type == "MemoryPressure" or .type == "DiskPressure" or .type == "PIDPressure") | .status == "True") |
                .metadata.name + ": " + (.status.conditions[] | select(.type == "MemoryPressure" or .type == "DiskPressure" or .type == "PIDPressure") | .type + " = " + .status)
              '

          - name: analyze_pod_distribution
            description: "Analyze pod distribution across nodes in a namespace"
            command: |
              kubectl get pods -n {{ namespace }} -o wide --no-headers |
              awk '{print $7}' | sort | uniq -c | sort -nr

          - name: check_resource_quotas
            description: "Check resource quota usage in a namespace"
            command: |
              kubectl describe resourcequota -n {{ namespace }}
    ```

    **Run HolmesGPT:**

    ```bash
    holmes ask "check for any resource pressure in the cluster" --custom-toolsets=toolsets.yaml
    ```

    After making changes to your toolsets file, run:
    ```bash
    holmes toolset refresh
    ```

=== "Robusta Helm Chart"

    **Helm Values:**

    ```yaml
    holmes:
      customToolsets:
        k8s-diagnostics:
          description: "Advanced Kubernetes diagnostic tools"
          prerequisites: "kubectl access to the cluster"
          tags: [kubernetes, diagnostics]
          installation: |
            1. Ensure kubectl is configured with cluster access
            2. Verify necessary RBAC permissions are in place
          tools:
            - name: check_node_pressure
              description: "Check for node pressure conditions and resource usage"
              command: |
                kubectl get nodes -o json | jq -r '
                  .items[] |
                  select(.status.conditions[]? | select(.type == "MemoryPressure" or .type == "DiskPressure" or .type == "PIDPressure") | .status == "True") |
                  .metadata.name + ": " + (.status.conditions[] | select(.type == "MemoryPressure" or .type == "DiskPressure" or .type == "PIDPressure") | .type + " = " + .status)
                '

            - name: analyze_pod_distribution
              description: "Analyze pod distribution across nodes in a namespace"
              command: |
                kubectl get pods -n {{ namespace }} -o wide --no-headers |
                awk '{print $7}' | sort | uniq -c | sort -nr

            - name: check_resource_quotas
              description: "Check resource quota usage in a namespace"
              command: |
                kubectl describe resourcequota -n {{ namespace }}
    ```

    **Helm Upgrade:**

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

### Example 3: GitHub Toolset

This example shows how to create a toolset for fetching information from GitHub repositories.

=== "Holmes CLI"

    **Configuration File (`toolsets.yaml`):**

    ```yaml
    toolsets:
      github:
        description: "Fetch information from GitHub repositories"
        prerequisites: "GitHub API token with repository access"
        tags: [source-control, github]
        installation: |
          1. Create a GitHub personal access token
          2. Set the token as an environment variable
          3. Ensure network access to GitHub API
        tools:
          - name: get_repository_info
            description: "Get information about a GitHub repository"
            command: |
              curl -s -H "Authorization: token ${GITHUB_TOKEN}" \
                "https://api.github.com/repos/{{ owner }}/{{ repo }}"

          - name: get_recent_commits
            description: "Get recent commits from a repository"
            command: |
              curl -s -H "Authorization: token ${GITHUB_TOKEN}" \
                "https://api.github.com/repos/{{ owner }}/{{ repo }}/commits?per_page={{ limit | default(10) }}"

          - name: search_issues
            description: "Search for issues in a repository"
            command: |
              curl -s -H "Authorization: token ${GITHUB_TOKEN}" \
                "https://api.github.com/search/issues?q=repo:{{ owner }}/{{ repo }}+{{ search_query }}"
    ```

    **Environment Variables:**

    ```bash
    export GITHUB_TOKEN="your-github-personal-access-token"
    ```

    **Run HolmesGPT:**

    ```bash
    holmes ask "check recent commits in robusta-dev/robusta repository" --custom-toolsets=toolsets.yaml
    ```

    After making changes to your toolsets file, run:
    ```bash
    holmes toolset refresh
    ```

=== "Robusta Helm Chart"

    **Helm Values:**

    ```yaml
    holmes:
      customToolsets:
        github:
          description: "Fetch information from GitHub repositories"
          prerequisites: "GitHub API token with repository access"
          tags: [source-control, github]
          installation: |
            1. Create a GitHub personal access token
            2. Set the token as an environment variable
            3. Ensure network access to GitHub API
          tools:
            - name: get_repository_info
              description: "Get information about a GitHub repository"
              command: |
                curl -s -H "Authorization: token {{ github_token }}" \
                  "https://api.github.com/repos/{{ owner }}/{{ repo }}"

            - name: get_recent_commits
              description: "Get recent commits from a repository"
              command: |
                curl -s -H "Authorization: token {{ github_token }}" \
                  "https://api.github.com/repos/{{ owner }}/{{ repo }}/commits?per_page={{ limit | default(10) }}"

            - name: search_issues
              description: "Search for issues in a repository"
              command: |
                curl -s -H "Authorization: token {{ github_token }}" \
                  "https://api.github.com/search/issues?q=repo:{{ owner }}/{{ repo }}+{{ search_query }}"
    ```

    **Environment Variables:**

    ```bash
    export GITHUB_TOKEN="your-github-personal-access-token"
    ```

    **Helm Upgrade:**

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Reference

### Toolset Configuration

A custom toolset consists of the following components:

```yaml
toolsets:
  <toolset-name>:
    description: "Human-readable description"
    prerequisites: "What needs to be installed/configured"
    tags: [tag1, tag2]  # Optional: for categorization
    installation: |
      Multi-line installation instructions
    tools:
      - name: tool_name
        description: "What this tool does"
        command: |
          Command or script to execute
        parameters:  # Optional: can be inferred by LLM
          - name: param_name
            description: "Parameter description"
```

### Tool Configuration

Each tool within a toolset can be configured with:

- **name**: Unique identifier for the tool
- **description**: What the tool does (visible to the AI)
- **command**: Shell command or script to execute
- **parameters**: Optional parameter definitions (usually inferred)

### Variable Syntax

HolmesGPT supports two types of variables in commands:

- **`{{ variable }}`**: Dynamic variables inferred by the LLM based on context
- **`${VARIABLE}`**: Environment variables (not visible to the LLM)

### Tags

Optional tags help categorize toolsets:

- **core**: Essential system tools
- **cluster**: Cluster-specific tools
- **monitoring**: Observability tools
- **networking**: Network-related tools
- **storage**: Storage-related tools

## Advanced: Adding Custom Binaries

If your custom toolset requires additional binaries not available in the base HolmesGPT image, you can extend the Docker image:

### Create a Custom Dockerfile

```dockerfile
FROM us-central1-docker.pkg.dev/genuine-flight-317411/devel/holmes:latest

# Install additional tools
RUN apt-get update && apt-get install -y \
    your-custom-tool \
    another-binary \
    && rm -rf /var/lib/apt/lists/*

# Copy custom scripts
COPY scripts/ /usr/local/bin/

# Make scripts executable
RUN chmod +x /usr/local/bin/*.sh
```

### Build and Push Your Image

```bash
docker build -t your-registry/holmes-custom:latest .
docker push your-registry/holmes-custom:latest
```

### Use Custom Image in Helm Values

```yaml
holmes:
  image:
    repository: your-registry/holmes-custom
    tag: latest
  customToolsets:
    # Your custom toolset configuration
```

This approach allows you to include any additional tools or dependencies your custom toolsets might need.

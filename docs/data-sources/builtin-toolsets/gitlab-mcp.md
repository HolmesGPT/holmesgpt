# GitLab (MCP)

The GitLab MCP server provides access to GitLab projects, merge requests, issues, pipelines, and code. It enables Holmes to investigate CI/CD failures, search code, review changes, and create merge requests.

Both **GitLab Cloud** (gitlab.com) and **self-hosted GitLab** instances are supported. The addon wraps the [`@zereight/mcp-gitlab`](https://github.com/zereight/gitlab-mcp) server via [supergateway](https://github.com/supercorp-ai/supergateway), exposing it over SSE for Holmes to consume.

## Prerequisites

You need a GitLab Personal Access Token (PAT).

1. Go to **User Settings → Access Tokens** (e.g., [gitlab.com/-/user_settings/personal_access_tokens](https://gitlab.com/-/user_settings/personal_access_tokens))
2. Click **Add new token**
3. Give it a descriptive name (e.g., "Holmes MCP Server") and an expiration
4. Select scopes:
    - **`api`** — full read/write API access
    - **`read_repository`** — read access to code (for code search and file reads)
    - **`write_repository`** — only if you want Holmes to create branches and MRs
5. Click **Create personal access token**
6. **Copy the token immediately** — it won't be shown again

!!! note "Write permissions are optional"
    `api` plus `write_repository` are only required if you want HolmesGPT to be able to open MRs, create issues, or commit changes. For read-only investigations, `read_api` and `read_repository` are sufficient.

## Configuration

=== "Holmes CLI"

    For CLI usage, deploy the GitLab MCP server first, then configure Holmes to connect to it.

    **Step 1: Create the GitLab PAT Secret**

    ```bash
    kubectl create namespace holmes-mcp

    kubectl create secret generic gitlab-mcp-token \
      --from-literal=token=<YOUR_GITLAB_PAT> \
      -n holmes-mcp
    ```

    **Step 2: Deploy the GitLab MCP Server**

    Create a file named `gitlab-mcp-deployment.yaml`:

    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: gitlab-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: gitlab-mcp-server
      template:
        metadata:
          labels:
            app: gitlab-mcp-server
        spec:
          containers:
          - name: gitlab-mcp
            image: supercorp/supergateway:latest
            imagePullPolicy: IfNotPresent
            stdin: true
            tty: true
            ports:
            - containerPort: 8000
              name: http
            args:
              - "--stdio"
              - "npx -y @zereight/mcp-gitlab"
              - "--port"
              - "8000"
            env:
            - name: GITLAB_PERSONAL_ACCESS_TOKEN
              valueFrom:
                secretKeyRef:
                  name: gitlab-mcp-token
                  key: token
            - name: GITLAB_API_URL
              value: "https://gitlab.com/api/v4"
            - name: GITLAB_READ_ONLY_MODE
              value: "false"
            - name: USE_PIPELINE
              value: "true"
            # For self-hosted GitLab, change GITLAB_API_URL above and see
            # "Self-Hosted GitLab" below for SSL options.
            resources:
              requests:
                memory: "256Mi"
                cpu: "100m"
              limits:
                memory: "512Mi"
            readinessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 5
              periodSeconds: 10
            livenessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 10
              periodSeconds: 30
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: gitlab-mcp-server
      namespace: holmes-mcp
    spec:
      selector:
        app: gitlab-mcp-server
      ports:
      - port: 8000
        targetPort: 8000
        protocol: TCP
        name: http
    ```

    Apply it:

    ```bash
    kubectl apply -f gitlab-mcp-deployment.yaml
    ```

    **Step 3: Configure Holmes CLI**

    Add the MCP server to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      gitlab:
        description: "GitLab MCP Server - access projects, merge requests, issues, pipelines, and code"
        config:
          url: "http://gitlab-mcp-server.holmes-mcp.svc.cluster.local:8000/sse"
          mode: "sse"
    ```

    **Step 4: Port Forwarding (Optional for Local Testing)**

    ```bash
    kubectl port-forward -n holmes-mcp svc/gitlab-mcp-server 8000:8000
    ```

    Then update the URL in `config.yaml` to `http://localhost:8000/sse`.

=== "Holmes Helm Chart"

    **Basic Configuration**

    Create a Kubernetes secret with your GitLab PAT:

    ```bash
    kubectl create secret generic gitlab-mcp-token \
      --from-literal=token=<YOUR_GITLAB_PAT> \
      -n <NAMESPACE>
    ```

    Then add the following to your `values.yaml`:

    ```yaml
    mcpAddons:
      gitlabMcp:
        enabled: true
        auth:
          secretName: "gitlab-mcp-token"
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    **Basic Configuration**

    ```bash
    kubectl create secret generic gitlab-mcp-token \
      --from-literal=token=<YOUR_GITLAB_PAT> \
      -n <NAMESPACE>
    ```

    Add to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        gitlabMcp:
          enabled: true
          auth:
            secretName: "gitlab-mcp-token"
    ```

    Then deploy or upgrade:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Self-Hosted GitLab

For self-hosted GitLab instances, set `config.apiUrl` to your instance's API endpoint:

```yaml
mcpAddons:
  gitlabMcp:
    enabled: true
    auth:
      secretName: "gitlab-mcp-token"
    config:
      apiUrl: "https://gitlab.mycompany.com/api/v4"
```

### SSL/TLS for Self-Signed Certificates

If your self-hosted GitLab uses a self-signed certificate or an internal CA, you have two options:

**Option 1 (Preferred): Trust a custom CA bundle**

Create a secret with your CA certificate, then point the addon at it:

```bash
kubectl create secret generic gitlab-ca-cert \
  --from-file=ca.crt=/path/to/your/ca-certificate.crt \
  -n <NAMESPACE>
```

```yaml
mcpAddons:
  gitlabMcp:
    enabled: true
    auth:
      secretName: "gitlab-mcp-token"
    config:
      apiUrl: "https://gitlab.mycompany.com/api/v4"
      caCert:
        secretName: "gitlab-ca-cert"
        secretKey: "ca.crt"
```

The addon will mount the secret and set `GITLAB_CA_CERT_PATH` so the MCP server trusts your CA.

**Option 2 (Insecure, last resort): Disable TLS verification**

```yaml
mcpAddons:
  gitlabMcp:
    enabled: true
    auth:
      secretName: "gitlab-mcp-token"
    config:
      apiUrl: "https://gitlab.internal/api/v4"
      verifySsl: false
```

This sets `NODE_TLS_REJECT_UNAUTHORIZED=0` in the MCP container, disabling all TLS verification. **Only use this in trusted networks** — it makes the MCP server vulnerable to man-in-the-middle attacks.

## Configuration Reference

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable the GitLab MCP addon |
| `auth.secretName` | — (required) | K8s secret holding the GitLab PAT |
| `auth.secretKey` | `token` | Key inside the secret |
| `config.apiUrl` | `https://gitlab.com/api/v4` | GitLab API endpoint (change for self-hosted) |
| `config.readOnly` | `false` | When `true`, blocks all write operations |
| `config.useWiki` | `false` | Enable wiki-related tools (`USE_GITLAB_WIKI`) |
| `config.useMilestone` | `false` | Enable milestone-related tools (`USE_MILESTONE`) |
| `config.usePipeline` | `true` | Enable pipeline/CI tools (`USE_PIPELINE`) |
| `config.projectId` | `""` | Default project ID (`GITLAB_PROJECT_ID`) |
| `config.allowedProjectIds` | `""` | Comma-separated project allowlist (`GITLAB_ALLOWED_PROJECT_IDS`) |
| `config.toolsets` | `""` | Comma-separated toolset IDs to expose (`GITLAB_TOOLSETS`); empty = all |
| `config.tools` | `""` | Comma-separated individual tools (hard allowlist; takes precedence over `toolsets`) |
| `config.verifySsl` | `true` | When `false`, disables ALL TLS verification (insecure) |
| `config.caCert.secretName` | `""` | K8s secret with a CA bundle for self-signed certs |
| `config.caCert.secretKey` | `ca.crt` | Key inside the CA secret |
| `networkPolicy.enabled` | `true` | Restrict ingress to Holmes pods |
| `llmInstructions` | `""` | Override the default LLM instructions |

## Customizing Tool Exposure

The addon exposes two knobs that control which tools the MCP server makes available:

- **`config.toolsets`** — comma-separated list of toolset groups. Every tool in each selected group becomes available.
- **`config.tools`** — comma-separated list of individual tool names. When set, this is a **hard allowlist** and takes precedence over `toolsets`.

```yaml
mcpAddons:
  gitlabMcp:
    enabled: true
    auth:
      secretName: "gitlab-mcp-token"
    config:
      # Hard allowlist — only these tools are exposed
      tools: "get_file_contents,list_commits,get_pipeline,get_pipeline_jobs,get_job_logs"
```

See the [`@zereight/mcp-gitlab` documentation](https://github.com/zereight/gitlab-mcp) for the full list of available toolsets and tools.

## Testing the Connection

```bash
# Check pod status
kubectl get pods -n YOUR_NAMESPACE -l app.kubernetes.io/name=gitlab-mcp-server

# Check logs
kubectl logs -n YOUR_NAMESPACE -l app.kubernetes.io/name=gitlab-mcp-server

# Ask Holmes a GitLab question
holmes ask "List the last 5 commits in mygroup/myproject"
```

## Common Use Cases

- "Pipeline #4523 in mygroup/myproject failed — what went wrong?"
- "What changed in the auth module of mygroup/backend in the past week?"
- "Find all usages of the deprecated `/v1/users` API endpoint in mygroup/myproject"
- "Open an MR in mygroup/myproject to add retry logic to the payment client"

## Troubleshooting

### Authentication Errors

Verify the secret exists and the PAT is valid:

```bash
# Check secret exists
kubectl get secret gitlab-mcp-token -n YOUR_NAMESPACE

# Test the token directly
curl -H "PRIVATE-TOKEN: <YOUR_GITLAB_PAT>" https://gitlab.com/api/v4/user
```

### SSL Certificate Verification Errors

See the [Self-Hosted GitLab](#sslsignedtls-for-self-signed-certificates) section above. Prefer mounting a custom CA over disabling verification.

### Tool Not Found

Check `config.toolsets` and `config.tools`. The defaults expose every tool from the upstream server. If you've restricted them, broaden the allowlist or unset `config.tools`.

### Slow Pod Startup

The default image runs `npx -y @zereight/mcp-gitlab` on every startup, which fetches the package from npm. Pod startup may take 30-60 seconds. If startup speed matters, build a custom image that bakes the npm package into the image at build time.

## Additional Resources

- [@zereight/mcp-gitlab (upstream MCP server)](https://github.com/zereight/gitlab-mcp)
- [supergateway (stdio → SSE bridge)](https://github.com/supercorp-ai/supergateway)
- [GitLab Personal Access Tokens](https://docs.gitlab.com/ee/user/profile/personal_access_tokens.html)

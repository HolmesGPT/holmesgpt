# GitHub (MCP)

The GitHub MCP server provides access to GitHub repositories, pull requests, issues, and GitHub Actions. It enables Holmes to investigate CI/CD failures, search code, review changes, and delegate tasks to GitHub Copilot.

## Overview

The GitHub MCP server is deployed as a separate pod in your cluster when using the Holmes or Robusta Helm charts. For CLI users, you'll need to deploy the MCP server manually and configure Holmes to connect to it.

The server supports both GitHub.com and GitHub Enterprise Server, making it suitable for both cloud and on-premises deployments.

## Prerequisites

Before deploying the GitHub MCP server, you need a GitHub Personal Access Token (PAT). GitHub offers two types of PATs:

| Type | Best For | Expiration |
|------|----------|------------|
| **Classic** | Simple setup, broad access | Up to no expiration |
| **Fine-grained** | Production, least-privilege | Max 1 year |

!!! note "Write permissions are optional"
    Write permissions (for Contents, Issues, Pull requests, Actions) are only required if you want HolmesGPT to be able to open PRs, create issues, or trigger workflows. For read-only investigations, read permissions are sufficient.

=== "Classic PAT"

    1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
    2. Click **Generate new token** → **Generate new token (classic)**
    3. Set a descriptive name (e.g., "Holmes MCP Server")
    4. Set expiration (90 days recommended)
    5. Select the following scopes:
       - ✅ **repo** - Full control of private repositories
       - ✅ **workflow** - Update GitHub Action workflows
       - ✅ **read:org** - Read organization membership (optional)
    6. Click **Generate token**
    7. **Copy the token immediately** - it won't be shown again

=== "Fine-grained PAT"

    1. Go to [github.com/settings/tokens?type=beta](https://github.com/settings/tokens?type=beta)
    2. Click **Generate new token**
    3. Set a descriptive name and expiration
    4. Under **Resource owner**, select your organization or personal account
    5. Under **Repository access**, choose:
       - **All repositories**, or
       - **Only select repositories** (for restricted access)
    6. Under **Permissions** → **Repository permissions**, set:
       - **Actions**: Read and write (to view and trigger workflows)
       - **Contents**: Read and write (to push code changes)
       - **Commit statuses**: Read-only
       - **Issues**: Read and write (to create issues and delegate to Copilot)
       - **Pull requests**: Read and write (to create PRs and request reviews)
       - **Metadata**: Read-only (automatically selected)
    7. Click **Generate token**
    8. **Copy the token immediately** - it won't be shown again

## Configuration

=== "Holmes CLI"

    For CLI usage, you need to deploy the GitHub MCP server first, then configure Holmes to connect to it.

    **Step 1: Create the GitHub PAT Secret**

    First, create a namespace and secret for the GitHub MCP server:

    ```bash
    kubectl create namespace holmes-mcp

    kubectl create secret generic github-mcp-token \
      --from-literal=token=<YOUR_GITHUB_PAT> \
      -n holmes-mcp
    ```

    **Step 2: Deploy the GitHub MCP Server**

    Create a file named `github-mcp-deployment.yaml`:

    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: github-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: github-mcp-server
      template:
        metadata:
          labels:
            app: github-mcp-server
        spec:
          containers:
          - name: github-mcp
            image: me-west1-docker.pkg.dev/robusta-development/development/github-mcp:1.0.0
            imagePullPolicy: IfNotPresent
            ports:
            - containerPort: 8000
              name: http
            env:
            - name: GITHUB_PERSONAL_ACCESS_TOKEN
              valueFrom:
                secretKeyRef:
                  name: github-mcp-token
                  key: token
            # Uncomment for GitHub Enterprise:
            # - name: GITHUB_HOST
            #   value: "https://github.mycompany.com"
            # For self-signed certs, see "SSL Certificate Verification Errors" in Troubleshooting.
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
      name: github-mcp-server
      namespace: holmes-mcp
    spec:
      selector:
        app: github-mcp-server
      ports:
      - port: 8000
        targetPort: 8000
        protocol: TCP
        name: http
    ```

    Deploy it to your cluster:

    ```bash
    kubectl apply -f github-mcp-deployment.yaml
    ```

    **Step 3: Configure Holmes CLI**

    Add the MCP server configuration to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      github:
        description: "GitHub MCP Server - access repositories, pull requests, issues, and GitHub Actions"
        config:
          url: "http://github-mcp-server.holmes-mcp.svc.cluster.local:8000/sse"
          mode: "sse"
    ```

    **Step 4: Port Forwarding (Optional for Local Testing)**

    If running Holmes CLI locally and need to access the MCP server:

    ```bash
    kubectl port-forward -n holmes-mcp svc/github-mcp-server 8000:8000
    ```

    Then update the URL in config.yaml to:
    ```yaml
    url: "http://localhost:8000/sse"
    ```

=== "Holmes Helm Chart"

    **Basic Configuration**

    First, create a Kubernetes secret with your GitHub PAT:

    ```bash
    kubectl create secret generic github-mcp-token \
      --from-literal=token=<YOUR_GITHUB_PAT> \
      -n <NAMESPACE>
    ```

    Then add the following to your `values.yaml`:

    ```yaml
    mcpAddons:
      github:
        enabled: true
        auth:
          secretName: "github-mcp-token"
    ```

    **GitHub Enterprise Configuration**

    For GitHub Enterprise Server, add the `host` configuration:

    ```yaml
    mcpAddons:
      github:
        enabled: true
        auth:
          secretName: "github-mcp-token"
        config:
          host: "https://github.mycompany.com"
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    **Basic Configuration**

    First, create a Kubernetes secret with your GitHub PAT:

    ```bash
    kubectl create secret generic github-mcp-token \
      --from-literal=token=<YOUR_GITHUB_PAT> \
      -n <NAMESPACE>
    ```

    Then add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        github:
          enabled: true
          auth:
            secretName: "github-mcp-token"
    ```

    **GitHub Enterprise Configuration**

    ```yaml
    holmes:
      mcpAddons:
        github:
          enabled: true
          auth:
            secretName: "github-mcp-token"
          config:
            host: "https://github.mycompany.com"
    ```

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Available Tools

By default, the GitHub MCP server enables 4 toolsets that provide comprehensive access to GitHub functionality:

| Toolset | Tools | Description |
|---------|-------|-------------|
| `repos` | ~24 | Repository operations, file access, commits, branches, code search |
| `issues` | ~11 | Issue management, labels, comments, Copilot delegation |
| `pull_requests` | ~10 | PR operations, reviews, comments, merging |
| `actions` | ~14 | Workflow runs, job logs, artifacts, CI/CD management |

### Key Tools by Category

**Repository & Code:**

- `get_file_contents` - Get contents of a file in a repository
- `get_repository_tree` - Get the file/directory structure
- `list_commits` / `get_commit` - View commit history and details
- `search_code` / `search_repositories` - Search across GitHub

**Pull Requests:**

- `list_pull_requests` / `pull_request_read` - View PR details, diffs, reviews
- `create_pull_request` - Create new pull requests
- `create_branch` / `push_files` - Create branches and push changes
- `request_copilot_review` - Request Copilot to review a PR

**GitHub Actions:**

- `list_workflows` - List workflow definitions
- `list_workflow_runs` / `get_workflow_run` - View workflow run status
- `get_workflow_run_logs` / `get_job_logs` - Get CI/CD logs for debugging

**Issues & Copilot:**

- `list_issues` / `search_issues` - Find and view issues
- `issue_write` / `add_issue_comment` - Create and update issues
- `assign_copilot_to_issue` - Delegate tasks to GitHub Copilot

### Customizing Toolsets

To use a different set of toolsets, override `config.toolsets`:

```yaml
mcpAddons:
  github:
    enabled: true
    auth:
      secretName: "github-mcp-token"
    config:
      # Only enable specific toolsets
      toolsets: "pull_requests,actions"
```

For fine-grained control, you can also specify individual tools with `config.tools`:

```yaml
mcpAddons:
  github:
    enabled: true
    auth:
      secretName: "github-mcp-token"
    config:
      toolsets: ""  # Disable toolsets
      # Only enable specific tools
      tools: "get_file_contents,list_commits,list_workflow_runs,get_job_logs"
```

For the full list of available tools and toolsets, see the [GitHub MCP Server documentation](https://github.com/github/github-mcp-server).

## Testing the Connection

After deploying the GitHub MCP server, verify it's working:

### Test 1: Check Pod Status

```bash
# For Helm deployments
kubectl get pods -n YOUR_NAMESPACE -l app.kubernetes.io/name=github-mcp-server

# For manual CLI deployments
kubectl get pods -n holmes-mcp -l app=github-mcp-server
```

### Test 2: Check Logs

```bash
# For Helm deployments
kubectl logs -n YOUR_NAMESPACE -l app.kubernetes.io/name=github-mcp-server

# For manual CLI deployments
kubectl logs -n holmes-mcp -l app=github-mcp-server
```

### Test 3: Ask Holmes

```bash
holmes ask "List the recent commits in the owner/repo repository"
```

## Common Use Cases

- "The CI build failed on PR #123 in myorg/myrepo. What went wrong?"
- "What changes were made to the authentication module in the last week?"
- "Find all usages of the deprecated API endpoint /v1/users in our codebase"
- "Create an issue to add retry logic to the payment service and assign it to Copilot"

## Troubleshooting

### Authentication Issues

**Problem:** Pod logs show authentication errors

**Solution:** Verify the secret exists and contains a valid PAT

```bash
# Check secret exists
kubectl get secret github-mcp-token -n YOUR_NAMESPACE

# Verify PAT has correct permissions (test locally with your token)
curl -H "Authorization: token <YOUR_GITHUB_PAT>" https://api.github.com/user
```

### Rate Limiting

**Problem:** Getting 403 rate limit errors

**Solution:** GitHub has API rate limits (5000 requests/hour for authenticated requests). If you're hitting limits:

1. Reduce the frequency of investigations
2. Use a GitHub App instead of PAT for higher limits
3. Consider using multiple PATs for different repositories

### GitHub Enterprise Connection Issues

**Problem:** Can't connect to GitHub Enterprise Server

**Solutions:**

1. Verify the hostname is correct and accessible from the cluster
2. Check if SSL certificates are valid
3. Ensure network policies allow egress to your GitHub Enterprise Server

```bash
# Test connectivity from the pod
kubectl exec -n YOUR_NAMESPACE deployment/github-mcp-server -- \
  curl -I https://github.mycompany.com/api/v3
```

### SSL Certificate Verification Errors

**Problem:** Getting SSL certificate verification errors when connecting to GitHub Enterprise with self-signed or internal CA certificates

**Solution:** Provide your organization's CA certificate to properly validate the connection:

**Step 1:** Create a Kubernetes secret with your CA certificate:

```bash
kubectl create secret generic github-ca-cert \
  --from-file=ca.crt=/path/to/your/ca-certificate.crt \
  -n <NAMESPACE>
```

**Step 2:** Configure the GitHub MCP addon to use the CA certificate:

=== "Holmes Helm Chart"

    ```yaml
    mcpAddons:
      github:
        enabled: true
        auth:
          secretName: "github-mcp-token"
        config:
          host: "https://github.mycompany.com"
          customCACert:
            enabled: true
            # secretName: "github-ca-cert"  # default
            # secretKey: "ca.crt"           # default
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      mcpAddons:
        github:
          enabled: true
          auth:
            secretName: "github-mcp-token"
          config:
            host: "https://github.mycompany.com"
            customCACert:
              enabled: true
    ```

=== "Holmes CLI (Manual Deployment)"

    Add volume, volumeMount, and environment variables to your deployment:

    ```yaml
    spec:
      containers:
      - name: github-mcp
        env:
        - name: GITHUB_PERSONAL_ACCESS_TOKEN
          valueFrom:
            secretKeyRef:
              name: github-mcp-token
              key: token
        - name: GITHUB_HOST
          value: "https://github.mycompany.com"
        - name: SSL_CERT_FILE
          value: /etc/ssl/certs/ca.crt
        - name: SSL_CERT_DIR
          value: /etc/ssl/certs
        volumeMounts:
        - name: ca-cert
          mountPath: /etc/ssl/certs
          readOnly: true
      volumes:
      - name: ca-cert
        secret:
          secretName: github-ca-cert
          defaultMode: 420
    ```

### Tool Not Found Errors

**Problem:** Holmes reports a tool is not available

**Solution:** Verify the `config.toolsets` setting includes the toolset containing your tool. The default toolsets are `repos,issues,pull_requests,actions`. For individual tool control, use `config.tools`.

## Additional Resources

- [GitHub MCP Server (upstream)](https://github.com/github/github-mcp-server)
- [GitHub Personal Access Tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
- [GitHub Enterprise Server](https://docs.github.com/en/enterprise-server)

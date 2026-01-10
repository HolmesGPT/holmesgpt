# GitHub (MCP)

The GitHub MCP server provides access to GitHub repositories, pull requests, issues, and GitHub Actions. It enables Holmes to investigate CI/CD failures, search code, review changes, and delegate tasks to GitHub Copilot.

## Overview

The GitHub MCP server is deployed as a separate pod in your cluster when using the Holmes or Robusta Helm charts. For CLI users, you'll need to deploy the MCP server manually and configure Holmes to connect to it.

The server supports both GitHub.com and GitHub Enterprise Server, making it suitable for both cloud and on-premises deployments.

## Prerequisites

Before deploying the GitHub MCP server, ensure you have:

- A GitHub Personal Access Token (PAT) with appropriate permissions
- For GitHub Enterprise: Your GitHub Enterprise Server hostname

### Required PAT Permissions

Create a Personal Access Token with the following scopes:

- **repo**: Full control of private repositories (or `public_repo` for public only)
- **workflow**: Update GitHub Action workflows (required for CI/CD debugging)
- **read:org**: Read organization membership (optional, for org-level queries)

## Configuration

=== "Holmes CLI"

    For CLI usage, you need to deploy the GitHub MCP server first, then configure Holmes to connect to it.

    ### Step 1: Deploy the GitHub MCP Server

    Create a file named `github-mcp-deployment.yaml`:

    ```yaml
    apiVersion: v1
    kind: Namespace
    metadata:
      name: holmes-mcp
    ---
    apiVersion: v1
    kind: Secret
    metadata:
      name: github-mcp-token
      namespace: holmes-mcp
    stringData:
      token: "ghp_YOUR_PERSONAL_ACCESS_TOKEN"
    ---
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: github-mcp-config
      namespace: holmes-mcp
    data:
      # Tools to enable (comma-separated)
      GITHUB_TOOLS: "get_file_contents,get_repository_tree,list_commits,get_commit,search_code,search_repositories,list_pull_requests,pull_request_read,list_workflow_runs,get_workflow_run,list_workflow_jobs,get_job_logs,issue_write,add_issue_comment,assign_copilot_to_issue,list_issues,search_issues"
      # For GitHub Enterprise, set your hostname (leave empty for github.com)
      # GITHUB_HOST: "github.mycompany.com"
    ---
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
            - name: GITHUB_TOOLS
              valueFrom:
                configMapKeyRef:
                  name: github-mcp-config
                  key: GITHUB_TOOLS
            # Uncomment for GitHub Enterprise:
            # - name: GITHUB_HOST
            #   valueFrom:
            #     configMapKeyRef:
            #       name: github-mcp-config
            #       key: GITHUB_HOST
            resources:
              requests:
                memory: "256Mi"
                cpu: "100m"
              limits:
                memory: "512Mi"
            readinessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 20
              periodSeconds: 10
            livenessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 30
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

    ### Step 2: Create the GitHub PAT Secret

    ```bash
    kubectl create secret generic github-mcp-token \
      --from-literal=token=ghp_YOUR_PERSONAL_ACCESS_TOKEN \
      -n holmes-mcp
    ```

    ### Step 3: Configure Holmes CLI

    Add the MCP server configuration to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      github:
        description: "GitHub MCP Server - access repositories, pull requests, issues, and GitHub Actions"
        config:
          url: "http://github-mcp-server.holmes-mcp.svc.cluster.local:8000/sse"
          mode: "sse"
        llm_instructions: |
          IMPORTANT: When investigating issues related to GitHub repositories, pull requests,
          code changes, or CI/CD workflows, you MUST actively use this MCP server to gather
          data rather than providing manual instructions to the user.

          ## Investigation Principles

          **ALWAYS follow this investigation flow:**
          1. First, gather current state of the repository, commits, or workflow runs
          2. Check recent changes (commits, PRs) that might have caused the issue
          3. For CI/CD failures, retrieve workflow run details and job logs
          4. Analyze all gathered data before providing conclusions

          **Never say "check on GitHub" or "look at the PR" - instead, use the MCP server to check it yourself.**
    ```

    ### Step 4: Port Forwarding (Optional for Local Testing)

    If running Holmes CLI locally and need to access the MCP server:

    ```bash
    kubectl port-forward -n holmes-mcp svc/github-mcp-server 8000:8000
    ```

    Then update the URL in config.yaml to:
    ```yaml
    url: "http://localhost:8000/sse"
    ```

=== "Holmes Helm Chart"

    ### Basic Configuration

    First, create a Kubernetes secret with your GitHub PAT:

    ```bash
    kubectl create secret generic github-mcp-token \
      --from-literal=token=ghp_YOUR_PERSONAL_ACCESS_TOKEN \
      -n YOUR_NAMESPACE
    ```

    Then add the following to your `values.yaml`:

    ```yaml
    mcpAddons:
      github:
        enabled: true

        auth:
          secretName: "github-mcp-token"
          secretKey: "token"  # Optional, defaults to "token"

        config:
          # Tools to enable (uses sensible defaults if not specified)
          tools: "get_file_contents,get_repository_tree,list_commits,get_commit,search_code,search_repositories,list_pull_requests,pull_request_read,list_workflow_runs,get_workflow_run,list_workflow_jobs,get_job_logs,issue_write,add_issue_comment,assign_copilot_to_issue,list_issues,search_issues"
    ```

    ### GitHub Enterprise Configuration

    For GitHub Enterprise Server, add the `host` configuration:

    ```yaml
    mcpAddons:
      github:
        enabled: true

        auth:
          secretName: "github-mcp-token"

        config:
          host: "github.mycompany.com"  # Your GitHub Enterprise hostname
          tools: "get_file_contents,get_repository_tree,list_commits,get_commit,search_code,search_repositories,list_pull_requests,pull_request_read,list_workflow_runs,get_workflow_run,list_workflow_jobs,get_job_logs,issue_write,add_issue_comment,assign_copilot_to_issue,list_issues,search_issues"
    ```

    For additional configuration options (resources, network policy, node selectors, etc.), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml).

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    ### Basic Configuration

    First, create a Kubernetes secret with your GitHub PAT:

    ```bash
    kubectl create secret generic github-mcp-token \
      --from-literal=token=ghp_YOUR_PERSONAL_ACCESS_TOKEN \
      -n YOUR_NAMESPACE
    ```

    Then add the following to your `generated_values.yaml`:

    ```yaml
    globalConfig:
      # Your existing Robusta configuration

    # Add the Holmes MCP addon configuration
    holmes:
      mcpAddons:
        github:
          enabled: true

          auth:
            secretName: "github-mcp-token"
            secretKey: "token"

          config:
            tools: "get_file_contents,get_repository_tree,list_commits,get_commit,search_code,search_repositories,list_pull_requests,pull_request_read,list_workflow_runs,get_workflow_run,list_workflow_jobs,get_job_logs,issue_write,add_issue_comment,assign_copilot_to_issue,list_issues,search_issues"
    ```

    ### GitHub Enterprise Configuration

    ```yaml
    globalConfig:
      # Your existing Robusta configuration

    holmes:
      mcpAddons:
        github:
          enabled: true

          auth:
            secretName: "github-mcp-token"

          config:
            host: "github.mycompany.com"
            tools: "get_file_contents,get_repository_tree,list_commits,get_commit,search_code,search_repositories,list_pull_requests,pull_request_read,list_workflow_runs,get_workflow_run,list_workflow_jobs,get_job_logs,issue_write,add_issue_comment,assign_copilot_to_issue,list_issues,search_issues"
    ```

    For additional configuration options (resources, network policy, node selectors, etc.), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml).

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Available Tools

The GitHub MCP server provides 17 tools by default, organized into four categories:

### Repository & Code Tools

| Tool | Description |
|------|-------------|
| `get_file_contents` | Get contents of a file in a repository |
| `get_repository_tree` | Get the file/directory structure of a repository |
| `list_commits` | List commits in a repository |
| `get_commit` | Get details of a specific commit including diff |
| `search_code` | Search for code across repositories |
| `search_repositories` | Search for repositories |

### Pull Request Tools

| Tool | Description |
|------|-------------|
| `list_pull_requests` | List pull requests in a repository |
| `pull_request_read` | Get details of a PR including diff, comments, reviews |

### GitHub Actions Tools

| Tool | Description |
|------|-------------|
| `list_workflow_runs` | List workflow runs for a repository |
| `get_workflow_run` | Get details of a specific workflow run |
| `list_workflow_jobs` | List jobs in a workflow run |
| `get_job_logs` | Get logs from a specific job (critical for debugging) |

### Issue Tools

| Tool | Description |
|------|-------------|
| `list_issues` | List issues in a repository |
| `search_issues` | Search for issues across repositories |
| `issue_write` | Create or update an issue |
| `add_issue_comment` | Add a comment to an issue |
| `assign_copilot_to_issue` | Delegate a task to GitHub Copilot |

## Testing the Connection

After deploying the GitHub MCP server, verify it's working:

### Test 1: Check Pod Status

```bash
kubectl get pods -n YOUR_NAMESPACE -l app.kubernetes.io/name=github-mcp-server
```

### Test 2: Check Logs

```bash
kubectl logs -n YOUR_NAMESPACE -l app.kubernetes.io/name=github-mcp-server
```

### Test 3: Ask Holmes

```bash
holmes ask "List the recent commits in the owner/repo repository"
```

## Common Use Cases

### Debugging GitHub Actions Failures

```
"The CI build failed on PR #123 in myorg/myrepo. What went wrong?"
```

Holmes will:
1. Get the workflow runs for the repository
2. Find the failed run associated with the PR
3. List the jobs in that run to identify which failed
4. Retrieve the job logs to find the actual error
5. Provide root cause analysis and suggestions

### Investigating Recent Changes

```
"What changes were made to the authentication module in the last week?"
```

Holmes will:
1. List recent commits on the repository
2. Filter for changes to authentication-related files
3. Summarize the changes and their authors

### Code Search

```
"Find all usages of the deprecated API endpoint /v1/users in our codebase"
```

Holmes will:
1. Search code across repositories for the pattern
2. List files and locations where it's used
3. Provide context for each usage

### Delegating Tasks to Copilot

```
"Create an issue to add retry logic to the payment service and assign it to Copilot"
```

Holmes will:
1. Create an issue with clear requirements
2. Assign GitHub Copilot to work on it

## Troubleshooting

### Authentication Issues

**Problem:** Pod logs show authentication errors

**Solution:** Verify the secret exists and contains a valid PAT

```bash
# Check secret exists
kubectl get secret github-mcp-token -n YOUR_NAMESPACE

# Verify PAT has correct permissions (test locally)
curl -H "Authorization: token ghp_YOUR_TOKEN" https://api.github.com/user
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

### Tool Not Found Errors

**Problem:** Holmes reports a tool is not available

**Solution:** Verify the `GITHUB_TOOLS` environment variable includes the tool you need. The default configuration includes 17 commonly used tools.

## Security Best Practices

1. **Use fine-grained PATs**: Create tokens with minimal required permissions
2. **Rotate tokens regularly**: Update your PAT every 90 days
3. **Use secrets properly**: Never commit tokens to version control
4. **Enable network policies**: Set `networkPolicy.enabled: true` to restrict traffic
5. **Audit token usage**: Monitor GitHub's security log for token activity

## Additional Resources

- [GitHub MCP Server (upstream)](https://github.com/github/github-mcp-server)
- [GitHub Personal Access Tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
- [GitHub Enterprise Server](https://docs.github.com/en/enterprise-server)

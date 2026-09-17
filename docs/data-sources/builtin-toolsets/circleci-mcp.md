# CircleCI (MCP)

The CircleCI MCP server connects Holmes to the CircleCI-hosted MCP endpoint — no self-hosted pod required. It provides access to pipelines, workflows, jobs, logs, artifacts, and deployments.

!!! note "Two ways to authenticate"
    This page covers **Personal API Token** authentication, which is the right choice for Holmes running in Kubernetes or any other non-interactive environment: the credential is static and no browser login is involved.

    If you want each user to authenticate with their own CircleCI account through a browser consent screen, use the OAuth 2.1 flow described in [OAuth MCP Servers](../oauth-mcp-servers.md) instead.

## Prerequisites

A CircleCI Personal API Token with access to the projects you want Holmes to investigate.

**Creating a CircleCI Personal API Token:**

1. Go to [app.circleci.com/settings/user/tokens](https://app.circleci.com/settings/user/tokens)
2. Click **Create New Token**
3. Enter a descriptive name (e.g., "Holmes MCP")
4. Click **Add API Token**
5. **Copy the token immediately** — it won't be shown again

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    mcp_servers:
      circleci:
        description: "CircleCI CI/CD - pipelines, workflows, jobs, and deployments"
        config:
          url: "https://mcp.circleci.com/v1/mcp"
          mode: streamable-http
          extra_headers:
            Authorization: "Bearer <YOUR_CIRCLECI_API_TOKEN>"
        icon_url: "https://cdn.simpleicons.org/circleci/343434"
    ```

    Replace `<YOUR_CIRCLECI_API_TOKEN>` with your token.

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**

    ```bash
    kubectl create secret generic circleci-mcp-token \
      --from-literal=token=<YOUR_CIRCLECI_API_TOKEN> \
      -n <NAMESPACE>
    ```

    **Configure Helm Values:**

    ```yaml
    # values.yaml
    mcpAddons:
      circleciMcp:
        enabled: true
        auth:
          secretName: "circleci-mcp-token"
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**

    ```bash
    kubectl create secret generic circleci-mcp-token \
      --from-literal=token=<YOUR_CIRCLECI_API_TOKEN> \
      -n <NAMESPACE>
    ```

    **Configure Helm Values:**

    ```yaml
    # generated_values.yaml
    holmes:
      mcpAddons:
        circleciMcp:
          enabled: true
          auth:
            secretName: "circleci-mcp-token"
    ```

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Testing the Connection

```bash
holmes ask "Who am I authenticated as in CircleCI?"
```

## Common Use Cases

**Investigate a failed pipeline:**
```bash
holmes ask "Why did the last pipeline run for gh/myorg/myrepo fail?"
```

**Check a specific workflow:**
```bash
holmes ask "What failed in the deploy workflow on the last run of gh/myorg/myrepo?"
```

**Get job logs:**
```bash
holmes ask "Show me the logs from the failing test job in the last CircleCI run of gh/myorg/myrepo"
```

**Check deployment status:**
```bash
holmes ask "What was deployed to production for gh/myorg/myrepo and when?"
```

## Troubleshooting

```bash
# Authentication errors - verify the secret is mounted
kubectl exec -n YOUR_NAMESPACE deployment/YOUR_HOLMES_DEPLOYMENT -- \
  env | grep CIRCLECI_API_TOKEN

# Test the token directly
curl -H "Circle-Token: <YOUR_TOKEN>" https://circleci.com/api/v2/me
```

## Additional Resources

- [CircleCI Personal API Tokens](https://circleci.com/docs/managing-api-tokens/)
- [CircleCI API v2 Reference](https://circleci.com/docs/api/v2/)

# Jenkins (MCP)

The Jenkins MCP Server Plugin enables Holmes to interact with your Jenkins CI/CD infrastructure.

## Prerequisites

- A running Jenkins instance with the [MCP Server Plugin](https://plugins.jenkins.io/mcp-server/) installed
- A Jenkins API token for authentication
- Network connectivity from Holmes to the Jenkins MCP endpoint

**Installing the Jenkins MCP Plugin:**

1. In Jenkins, go to **Manage Jenkins** → **Plugins** → **Available plugins**
2. Search for "MCP Server" and install it
3. Restart Jenkins if required

**Creating a Jenkins API Token:**

1. Sign in to Jenkins
2. Click your username in the upper-right corner → **Security**
3. Under **API Token**, click **Add new Token**
4. Enter a descriptive name and click **Generate**
5. Copy the token immediately (it won't be shown again)
6. Click **Save**

**Encoding credentials for Basic authentication:**

The Jenkins MCP server uses HTTP Basic authentication. Encode your credentials:

=== "Linux / macOS"

    ```bash
    echo -n "username:api_token" | base64
    ```

=== "Windows (PowerShell)"

    ```powershell
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("username:api_token"))
    ```

Store the encoded credential securely for use in the configuration below.

## Configuration

=== "Holmes CLI"

    Set the environment variable:

    ```bash
    export JENKINS_AUTH_TOKEN="$(echo -n "username:api_token" | base64 | tr -d '\n')"
    ```

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    mcp_servers:
      jenkins:
        description: "Jenkins CI/CD server"
        config:
          url: "https://your-jenkins-instance/mcp-server/mcp"
          mode: streamable-http
          headers:
            Authorization: "Basic {{ env.JENKINS_AUTH_TOKEN }}"
          verify_ssl: false  # Set to true if using valid SSL certificates
        icon_url: "https://cdn.simpleicons.org/jenkins/D24939"
        llm_instructions: |
          When investigating build failures, start with recent build status and then examine console output.
          Use pagination for large result sets to avoid token overflow.
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-jenkins-mcp \
      --from-literal=JENKINS_AUTH_TOKEN="$(echo -n "username:api_token" | base64 | tr -d '\n')" \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-jenkins-mcp

    mcp_servers:
      jenkins:
        description: "Jenkins CI/CD server"
        config:
          url: "https://your-jenkins-instance/mcp-server/mcp"
          mode: streamable-http
          headers:
            Authorization: "Basic {{ env.JENKINS_AUTH_TOKEN }}"
          verify_ssl: false  # Set to true if using valid SSL certificates
        icon_url: "https://cdn.simpleicons.org/jenkins/D24939"
        llm_instructions: |
          When investigating build failures, start with recent build status and then examine console output.
          Use pagination for large result sets to avoid token overflow.
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-jenkins-mcp \
      --from-literal=JENKINS_AUTH_TOKEN="$(echo -n "username:api_token" | base64 | tr -d '\n')" \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-jenkins-mcp

      mcp_servers:
        jenkins:
          description: "Jenkins CI/CD server"
          config:
            url: "https://your-jenkins-instance/mcp-server/mcp"
            mode: streamable-http
            headers:
              Authorization: "Basic {{ env.JENKINS_AUTH_TOKEN }}"
            verify_ssl: false  # Set to true if using valid SSL certificates
          icon_url: "https://cdn.simpleicons.org/jenkins/D24939"
          llm_instructions: |
            When investigating build failures, start with recent build status and then examine console output.
            Use pagination for large result sets to avoid token overflow.
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

!!! warning "MCP endpoint path"
    The Jenkins MCP server serves on `/mcp-server/mcp` for Streamable HTTP transport. Other available endpoints:

    - **Streamable HTTP**: `/mcp-server/mcp` (recommended)
    - **SSE**: `/mcp-server/sse`
    - **Stateless**: `/mcp-server/stateless`

## Testing the Connection

```bash
holmes ask "List all Jenkins jobs"
```

## Common Use Cases

**Investigate a failed build:**
```bash
holmes ask "Why did the last build of my-app-pipeline fail?"
```

**Check build status:**
```bash
holmes ask "What is the status of the most recent builds for the deploy-production job?"
```

**View build logs:**
```bash
holmes ask "Show me the console output from the last failed build of backend-service"
```

**Monitor pipeline stages:**
```bash
holmes ask "What stages failed in the latest run of the CI pipeline?"
```

**Check queue status:**
```bash
holmes ask "Are there any builds waiting in the Jenkins queue?"
```

**Analyze build trends:**
```bash
holmes ask "Show me the build history and success rate for the integration-tests job"
```

## Troubleshooting

**Authentication Errors**

If you receive 401 or 403 errors:

1. Verify your API token is valid and not expired
2. Ensure the credentials are properly base64 encoded (username:token format)
3. Check that the Jenkins user has appropriate permissions

**Connection Issues**

If Holmes cannot connect to Jenkins:

1. Verify the Jenkins URL is accessible from the Holmes pod/container
2. Check if SSL certificate verification is causing issues (`verify_ssl: false` for self-signed certs)
3. Ensure the MCP Server plugin is installed and enabled in Jenkins

**Plugin Not Found**

If the `/mcp-server/mcp` endpoint returns 404:

1. Verify the MCP Server plugin is installed in Jenkins
2. Restart Jenkins after plugin installation
3. Check Jenkins system logs for plugin errors

## Additional Resources

- [Jenkins MCP Server Plugin](https://plugins.jenkins.io/mcp-server/)
- [Jenkins API Token Documentation](https://www.jenkins.io/doc/book/system-administration/authenticating-scripted-clients/)
- [Model Context Protocol Specification](https://modelcontextprotocol.io/)

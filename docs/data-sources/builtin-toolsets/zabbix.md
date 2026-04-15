# Zabbix

Connect HolmesGPT to Zabbix for monitoring and alerting via the Zabbix JSON-RPC 2.0 API. Query hosts, problems, events, triggers, services, and historical metrics to investigate infrastructure issues and correlate alerts across your monitoring stack.

## Prerequisites

- A running Zabbix instance (6.0 or later)
- A Zabbix API token with appropriate permissions
- Network connectivity from Holmes to the Zabbix API endpoint

**Creating a Zabbix API Token:**

1. Sign in to Zabbix as an admin user
2. Navigate to **Administration** → **Users** → **API tokens**
3. Click **Create API token**
4. Enter a descriptive name (e.g., "HolmesGPT")
5. Select the user account that will be used for API access
6. Set the expiration date (optional)
7. Click **Create**
8. Copy the token immediately (it won't be shown again)

!!! important "User Permissions"
    The selected user must have appropriate permissions to access the data you want Holmes to query. Ensure the user has at least "User" role with read access to hosts, problems, and events.

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      zabbix:
        type: http
        enabled: true
        description: "Zabbix monitoring system"
        config:
          endpoints:
            - hosts: ["your-zabbix-instance.com"]
              paths: ["/zabbix/api_jsonrpc.php"]
              methods: ["POST"]
              auth:
                type: bearer
                token: "{{ env.ZABBIX_TOKEN }}"
        llm_instructions: |
          Use the zabbix_request tool to query Zabbix via its JSON-RPC 2.0 API.
          All requests go to POST https://<your-zabbix>/zabbix/api_jsonrpc.php with this structure:
            {"jsonrpc": "2.0", "method": "<method>", "params": {...}, "id": 1}

          Always set "limit" to avoid token overflow. Use Unix timestamps for time fields.
    ```

    Set the environment variable:

    ```bash
    export ZABBIX_TOKEN="your-zabbix-api-token"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**

    ```bash
    kubectl create secret generic zabbix-credentials \
      --from-literal=token="your-zabbix-api-token" \
      -n <namespace>
    ```

    **Configure Helm Values:**

    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: ZABBIX_TOKEN
        valueFrom:
          secretKeyRef:
            name: zabbix-credentials
            key: token

    toolsets:
      zabbix:
        type: http
        enabled: true
        description: "Zabbix monitoring system"
        config:
          endpoints:
            - hosts: ["your-zabbix-instance.com"]
              paths: ["/zabbix/api_jsonrpc.php"]
              methods: ["POST"]
              auth:
                type: bearer
                token: "{{ env.ZABBIX_TOKEN }}"
        llm_instructions: |
          Use the zabbix_request tool to query Zabbix via its JSON-RPC 2.0 API.
          All requests go to POST https://<your-zabbix>/zabbix/api_jsonrpc.php with this structure:
            {"jsonrpc": "2.0", "method": "<method>", "params": {...}, "id": 1}

          Always set "limit" to avoid token overflow. Use Unix timestamps for time fields.
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**

    ```bash
    kubectl create secret generic zabbix-credentials \
      --from-literal=token="your-zabbix-api-token" \
      -n <namespace>
    ```

    **Configure Helm Values:**

    ```yaml
    # generated_values.yaml
    holmes:
      additionalEnvVars:
        - name: ZABBIX_TOKEN
          valueFrom:
            secretKeyRef:
              name: zabbix-credentials
              key: token

      toolsets:
        zabbix:
          type: http
          enabled: true
          description: "Zabbix monitoring system"
          config:
            endpoints:
              - hosts: ["your-zabbix-instance.com"]
                paths: ["/zabbix/api_jsonrpc.php"]
                methods: ["POST"]
                auth:
                  type: bearer
                  token: "{{ env.ZABBIX_TOKEN }}"
          llm_instructions: |
            Use the zabbix_request tool to query Zabbix via its JSON-RPC 2.0 API.
            All requests go to POST https://<your-zabbix>/zabbix/api_jsonrpc.php with this structure:
              {"jsonrpc": "2.0", "method": "<method>", "params": {...}, "id": 1}

            Always set "limit" to avoid token overflow. Use Unix timestamps for time fields.
    ```

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Testing the Connection

```bash
holmes ask "List all monitored hosts in Zabbix"
```

## Common Use Cases

**Check active problems:**

    holmes ask "What are the current active problems in Zabbix?"

**Investigate a specific host:**

    holmes ask "Show me all recent events and problems for the database server host"

**Query historical metrics:**

    holmes ask "What was the CPU usage for the web server over the last 24 hours?"

**Find triggered alerts:**

    holmes ask "Which triggers are currently in a problem state?"

**Analyze event trends:**

    holmes ask "Show me the events that occurred in the last 6 hours and identify patterns"

**Monitor specific metrics:**

    holmes ask "Get the memory usage metrics for all production hosts"


## Troubleshooting

**Common issues and solutions:**

```bash
# Authentication Errors (401/403)
# - Verify your API token is valid and not expired
# - Check that the token has not been revoked in Zabbix
# - Ensure the user account associated with the token has appropriate permissions
# - Verify the token is correctly set in the environment variable or secret

# Connection Issues
# - Verify the Zabbix URL is accessible from the Holmes pod/container
# - Check if SSL certificate verification is causing issues (use verify_ssl: false for self-signed certificates)
# - Ensure the API endpoint path is correct (/zabbix/api_jsonrpc.php)
# - Verify network connectivity and firewall rules allow access to the Zabbix server

# API Errors
# - Check the error message returned by the API for details
# - Verify the JSON-RPC request format is correct
# - Ensure all required parameters are included in the request
# - Check that the method name is spelled correctly
# - Verify that the user has permission to access the requested data

# Token Overflow Errors
# - Reduce the limit parameter in your queries
# - Use more specific filters to reduce the amount of data returned
# - Query a shorter time range for historical data
# - Split large queries into multiple smaller requests
```

## Additional Resources

- [Zabbix API Reference](https://www.zabbix.com/documentation/current/en/api)
- [Zabbix API Authentication](https://www.zabbix.com/documentation/current/en/api/reference/authentication/token/create)
- [Zabbix JSON-RPC Protocol](https://www.zabbix.com/documentation/current/en/api/reference)

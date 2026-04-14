# Zabbix

Connect HolmesGPT to Zabbix for monitoring and alerting via the Zabbix JSON-RPC 2.0 API.

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
```bash
holmes ask "What are the current active problems in Zabbix?"
```

**Investigate a specific host:**
```bash
holmes ask "Show me all recent events and problems for the database server host"
```

**Query historical metrics:**
```bash
holmes ask "What was the CPU usage for the web server over the last 24 hours?"
```

**Find triggered alerts:**
```bash
holmes ask "Which triggers are currently in a problem state?"
```

**Analyze event trends:**
```bash
holmes ask "Show me the events that occurred in the last 6 hours and identify patterns"
```

**Monitor specific metrics:**
```bash
holmes ask "Get the memory usage metrics for all production hosts"
```


## Troubleshooting

**Authentication Errors**

If you receive 401 or 403 errors:

1. Verify your API token is valid and not expired
2. Check that the token has not been revoked in Zabbix
3. Ensure the user account associated with the token has appropriate permissions
4. Verify the token is correctly set in the environment variable or secret

**Connection Issues**

If Holmes cannot connect to Zabbix:

1. Verify the Zabbix URL is accessible from the Holmes pod/container
2. Check if SSL certificate verification is causing issues (use `verify_ssl: false` for self-signed certificates)
3. Ensure the API endpoint path is correct (`/zabbix/api_jsonrpc.php`)
4. Verify network connectivity and firewall rules allow access to the Zabbix server

**API Errors**

If you receive API errors from Zabbix:

1. Check the error message returned by the API for details
2. Verify the JSON-RPC request format is correct
3. Ensure all required parameters are included in the request
4. Check that the method name is spelled correctly
5. Verify that the user has permission to access the requested data

**Token Overflow**

If you receive token overflow errors:

1. Reduce the `limit` parameter in your queries
2. Use more specific filters to reduce the amount of data returned
3. Query a shorter time range for historical data
4. Split large queries into multiple smaller requests

## Additional Resources

- [Zabbix API Reference](https://www.zabbix.com/documentation/current/en/api)
- [Zabbix API Authentication](https://www.zabbix.com/documentation/current/en/api/reference/authentication/token/create)
- [Zabbix JSON-RPC Protocol](https://www.zabbix.com/documentation/current/en/api/reference)

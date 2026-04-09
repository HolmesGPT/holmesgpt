# Splunk (MCP)

The Splunk MCP server provides access to Splunk's search and analysis capabilities. It enables Holmes to query Splunk indexes, investigate logs, analyze security events, and troubleshoot application issues using Splunk's powerful search processing language (SPL).

## Overview

The Splunk MCP server is installed directly on your Splunk instance (Cloud or Enterprise). Holmes connects to your Splunk MCP server endpoint using token-based authentication.

## Prerequisites

Before configuring Holmes to connect to Splunk MCP, you need to:

1. Install the Splunk MCP Server app on your Splunk instance
2. Create a dedicated role with MCP permissions
3. Create a user with the MCP role
4. Generate an authentication token

### Step 1: Install Splunk MCP Server

=== "Splunk Cloud"

    1. Navigate to **Apps** → **Manage Apps** → **Browse more apps**
    2. Search for "MCP"
    3. Click **Install** on the Splunk MCP Server app

=== "Splunk Enterprise"

    1. Download the MCP app from [Splunkbase](https://splunkbase.splunk.com/app/7931)
    2. Navigate to **Apps** → **Manage Apps** → **Install app from file**
    3. Upload the downloaded MCP file and install

### Step 2: Create MCP Role

1. Navigate to **Settings** → **Roles** → **New Role**
2. Set role name: `mcp_user`
3. Under **Capabilities**, enable:
      - `mcp_tool_admin`
      - `mcp_tool_execute`
4. Click **Create**

### Step 3: Create MCP User

1. Navigate to **Settings** → **Users** → **New User**
2. Set username: `mcp_user_1` (or your preferred name)
3. Assign the `mcp_user` role
4. Click **Save**

### Step 4: Generate Authentication Token

1. Navigate to **Settings** → **Tokens** → **New Token**
2. Set **User** to `mcp_user_1`
3. Set **Audience** to `mcp`
4. Click **Create**
5. Copy the token

### Step 5: Get MCP Endpoint URL

1. Navigate to **Apps** → **Splunk MCP Server**
2. Copy the endpoint URL displayed on the app page

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    mcp_servers:
      splunk:
        description: "Splunk MCP server for log analysis and investigation"
        config:
          url: "https://your-splunk-instance:8089/services/mcp/"
          mode: streamable-http
          headers:
            Authorization: "Bearer <YOUR_TOKEN>"
          # verify_ssl: false # Uncomment if using self-signed certificates:
        # You can modify the llm_instructions according to the data stored in Splunk in your organization 
        llm_instructions: |
          Use SPL (Search Processing Language) for queries.
          Always specify a time range to limit results. Always limit large result sets.
          Use Splunk to fetch logs and traces. Splunk contains historical data as well
    ```

    Replace:

    - `your-splunk-instance:8089` with your Splunk instance hostname and management port
    - `<YOUR_TOKEN>` with the token generated in Prerequisites Step 4

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your Splunk token:

    ```bash
    kubectl create secret generic splunk-mcp-token \
      --from-literal=token=<YOUR_TOKEN> \
      -n <NAMESPACE>
    ```

    Then add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: SPLUNK_MCP_TOKEN
          valueFrom:
            secretKeyRef:
              name: splunk-mcp-token
              key: token

      mcp_servers:
        splunk:
          description: "Splunk MCP server for log analysis and investigation"
          config:
            url: "https://your-splunk-instance:8089/services/mcp/"
            mode: streamable-http
            headers:
              Authorization: "Bearer {{ env.SPLUNK_MCP_TOKEN }}"
            # verify_ssl: false # Uncomment if using self-signed certificates:
            # You can modify the llm_instructions according to the data stored in Splunk in your organization 
            llm_instructions: |
              Use SPL (Search Processing Language) for queries.
              Always specify a time range to limit results. Always limit large result sets.
              Use Splunk to fetch logs and traces. Splunk contains historical data as well
    ```

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Available Tools

The Splunk MCP server provides tools for searching and analyzing data in Splunk. For the complete list of available tools and their parameters, see the [Splunk MCP Server Tools documentation](https://help.splunk.com/en/splunk-cloud-platform/mcp-server-for-splunk-platform/mcp-server-tools).

## Testing the Connection

After configuring Holmes to connect to Splunk MCP, verify it's working:

```bash
holmes ask "List the available Splunk indexes"
```

Or test a simple search:

```bash
holmes ask "Search Splunk for the most recent 10 error events"
```

## Common Use Cases

- "Search Splunk for authentication failures in the last hour"
- "Find all error logs from the payment service in the main index"
- "What were the top 10 error types in production yesterday?"
- "Search for events containing 'connection timeout' in the last 24 hours"
- "Analyze the trend of 5xx errors over the past week"

## Additional Resources

- [Splunk MCP Server on Splunkbase](https://splunkbase.splunk.com/app/7931)
- [Splunk MCP Server Tools Reference](https://help.splunk.com/en/splunk-cloud-platform/mcp-server-for-splunk-platform/mcp-server-tools)


# ServiceNow

Connect HolmesGPT to ServiceNow to analyze ITSM data via the [Table API](https://docs.servicenow.com/bundle/vancouver-api-reference/page/integrate/inbound-rest/concept/c_TableAPI.html). Query changes, incidents, configuration items, and other tables to investigate infrastructure issues.

## Prerequisites

- A ServiceNow instance
- Admin access to configure API authentication

## Setup Instructions

Follow these steps to configure API access in your ServiceNow instance. For detailed instructions, see the [ServiceNow API Key Configuration Guide](https://www.servicenow.com/docs/bundle/yokohama-platform-security/page/integrate/authentication/task/configure-api-key.html).

### 1. Create an Inbound Authentication Profile

   1. Navigate to **All** > **System Web Services** > **API Access Policies** > **Inbound Authentication Profiles**
   2. Click **New**
   3. Select **Create API Key authentication profiles**
   4. In the **Auth Parameter** field, add: `x-sn-apikey: Auth Header`
   5. Submit

### 2. Create a REST API Key

   1. Navigate to **All** > **System Web Services** > **API Access Policies** > **REST API Key**
   2. Click **New**
   3. Select the **User** account that will be used for API access
   4. Submit
   5. Open the created record to copy the generated API token - you'll use this as the `api_key` in the configuration below

!!! important
    The selected user's permissions determine which tables and records HolmesGPT can access. Ensure the user has appropriate read permissions for the tables you want to query.

### 3. Create REST API Access Policy

   1. Navigate to **All** > **System Web Services** > **REST API Access Policies**
   2. Click **New**
   3. Configure:
      - **REST API**: Select "Table API"
      - **Apply to all tables**: Leave this checked (recommended)
      - **Authentication Profile**: Select the profile created in Step 1
      - **Apply to all methods**: Uncheck this option, then select "GET" from the HTTP Method dropdown that appears
   4. Submit

!!! tip
    Enable "Apply to all tables" for best results. Limiting access to specific tables reduces HolmesGPT's investigative capabilities.

### 4. Test Your Configuration

Verify your setup with either of these test queries:

```bash
# Test with incident table
curl -X GET "https://<your-instance>.service-now.com/api/now/table/incident?sysparm_limit=1" \
  -H "Accept: application/json" \
  -H "x-sn-apikey: <your-api-key>"

# Or test with system table (always has data)
curl -X GET "https://<your-instance>.service-now.com/api/now/table/sys_db_object?sysparm_limit=1" \
  -H "Accept: application/json" \
  -H "x-sn-apikey: <your-api-key>"
```

You should receive a JSON response. If you get an authentication error, check your API key and permissions.

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      servicenow/tables:
        enabled: true
        config:
          api_key: <your servicenow API key>  # e.g. now_1234567890abcdef
          api_url: <your servicenow instance URL>  # e.g. https://dev12345.service-now.com

          # Optional
          api_key_header: x-sn-apikey  # HTTP header name for the API key (default: x-sn-apikey)
          health_check_table: sys_user  # Table used to verify connectivity on startup (default: sys_user)
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "Show me all change requests from the last 24 hours"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        servicenow/tables:
          enabled: true
          config:
            api_key: <your servicenow API key>  # e.g. now_1234567890abcdef
            api_url: <your servicenow instance URL>  # e.g. https://dev12345.service-now.com

            # Optional
            api_key_header: x-sn-apikey  # HTTP header name for the API key (default: x-sn-apikey)
            health_check_table: sys_user  # Table used to verify connectivity on startup (default: sys_user)
    ```

### Optional Fields

| Option | Default | Description |
|--------|---------|-------------|
| `api_key_header` | `x-sn-apikey` | HTTP header name used to pass the API key. Change this if your ServiceNow instance uses a custom authentication header. |
| `health_check_table` | `sys_user` | Table queried on startup to verify connectivity and permissions. Change this if your API key doesn't have access to the default table. |

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| servicenow_get_records | Query multiple records from any ServiceNow table with powerful filtering, sorting, and field selection capabilities |
| servicenow_get_record | Retrieve a single record by its sys_id with full details from any accessible table |

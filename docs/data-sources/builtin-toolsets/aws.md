# AWS (Deprecated)

!!! warning "Deprecated"
    This toolset is deprecated. Please use the new [AWS (MCP)](aws-mcp.md) data source instead, which provides:

    - Comprehensive access to all AWS services
    - Better memory management for large queries
    - Active CloudTrail investigation capabilities
    - Improved error handling and pagination support

    The legacy AWS toolset will be removed in a future release.

## Security

Set of tools to audit AWS CloudTrail events and audit logs.

### Configuration

=== "Holmes CLI"

    First, add the following environment variables:

    ```bash
    export AWS_ACCESS_KEY_ID="<your AWS access key ID>"
    export AWS_SECRET_ACCESS_KEY="<your AWS secret access key>"
    export AWS_DEFAULT_REGION="us-west-2"
    ```

    Then, Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
        aws/security:
            enabled: true
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "Are there any security misconfigurations in my signup application, particularly in the database?"
    ```

=== "Robusta Helm Chart"

    This builtin toolset is currently only available in HolmesGPT CLI.

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| aws_cloudtrail_event_lookup | Fetches events of a specified type from AWS CloudTrail along with the users that called them |
| aws_cloudtrail_event_details | Fetches and returns full event details for an AWS CloudTrail event in JSON format given an event ID |
| aws_user_audit_logs | Fetches audit logs for a specified user from AWS CloudTrail in the past 24 hours. Provide username as was output by aws_event_lookup or aws_event_details |

## RDS

Read access to Amazon RDS instances, events, and logs.

### Configuration

=== "Holmes CLI"

    Configure RDS access with your AWS credentials and region settings.

=== "Robusta Helm Chart"

    This builtin toolset is currently only available in HolmesGPT CLI.

### Capabilities

| Tool Name | Description |
|-----------|-------------|
| aws_rds_describe_instances | Describe RDS instances |
| aws_rds_events | Get RDS events |
| aws_rds_logs | Retrieve RDS logs |

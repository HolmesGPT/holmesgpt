# Robusta

!!! warning "Optional - Requires Robusta"
    This toolset is **NOT** enabled by default. It requires integration with the Robusta platform and proper authentication credentials.

The Robusta toolset provides advanced observability capabilities by connecting HolmesGPT to the Robusta platform. When enabled, it gives HolmesGPT access to historical data, change tracking, and resource recommendations that are not available from standard Kubernetes APIs.

## Prerequisites

To use this toolset, you need:

1. An active Robusta account
2. Valid authentication credentials (provided via environment variables or configuration file)
3. The Robusta platform deployed in your cluster

## What It Adds

When connected to Robusta, HolmesGPT gains access to:

- **Historical Alert Data**: Fetch detailed metadata about past alerts and incidents, including context that may no longer be available in Prometheus or AlertManager
- **Change Tracking**: Query configuration changes across your entire cluster within specific time ranges, helping identify what changed before an incident
- **Resource Recommendations**: Get AI-powered recommendations for resource requests and limits based on actual historical usage patterns

## Configuration

The toolset requires authentication to Robusta. You can provide credentials in three ways:

### Option 1: Automatic (via Robusta Helm Chart)

If you deploy HolmesGPT as part of the Robusta Helm chart, credentials are automatically configured. The Helm chart handles mounting the necessary secrets and configuration files.

### Option 2: Environment Variables

```bash
export ROBUSTA_UI_TOKEN="<base64-encoded-token>"
# OR provide individual credentials:
export ROBUSTA_ACCOUNT_ID="<account-id>"
export STORE_URL="<store-url>"
export STORE_API_KEY="<api-key>"
export STORE_EMAIL="<email>"
export STORE_PASSWORD="<password>"
```

### Option 3: Configuration File

The toolset will automatically look for credentials in `/etc/robusta/config/active_playbooks.yaml` (or the path specified by `ROBUSTA_CONFIG_PATH`).

### Enabling the Toolset

```yaml
holmes:
    toolsets:
        robusta:
            enabled: true
```

## Tools

--8<-- "snippets/toolset_capabilities_intro.md"

| Tool Name | Description |
|-----------|-------------|
| fetch_finding_by_id | Fetch a Robusta finding (an event such as a Prometheus alert, deployment update, or configuration change) |
| fetch_resource_recommendation | Fetch KRR (Kubernetes Resource Recommendations) for CPU and memory right-sizing based on historical usage |
| fetch_configuration_changes_metadata | Fetch configuration changes metadata in a given time range, including Kubernetes and external sources (e.g., LaunchDarkly) |
| fetch_resource_issues_metadata | Fetch issues and alert metadata in a given time range, optionally filtered by namespace or resource |

### Multi-Cluster Support

All tools that fetch data from Robusta support querying across multiple clusters:

- **Default behavior**: Queries the current cluster only
- **all_clusters=true**: Searches across all clusters in your account
- **clusters=['cluster-a', 'cluster-b']**: Queries specific clusters by name

### External Changes

The `fetch_configuration_changes_metadata` tool includes external configuration changes by default (e.g., LaunchDarkly feature flag changes). Set `include_external=false` to exclude these and only see Kubernetes cluster changes.

## Use Cases

This toolset is particularly useful for:

- **Root Cause Analysis**: Understanding what configuration changes occurred before an incident
- **Resource Optimization**: Getting data-driven recommendations for right-sizing workloads
- **Historical Investigation**: Accessing alert context and metadata that may have been lost or expired in other systems
- **Change Management**: Tracking who changed what and when across your infrastructure

## Notes

- The toolset will only be functional if valid Robusta credentials are provided
- If credentials are missing or invalid, the toolset will be disabled automatically
- This integration provides read-only access to your Robusta data

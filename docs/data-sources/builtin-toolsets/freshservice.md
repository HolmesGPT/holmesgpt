# Freshservice

Connect HolmesGPT to [Freshservice](https://www.freshworks.com/freshservice/) (Freshworks ITSM) to work with tickets, problems, changes, releases, assets, requesters, agents, the service catalog, the knowledge base and every other Freshservice object via the [Freshservice API v2](https://api.freshservice.com/).

Access is read-only by default. Create/update/delete tools can be enabled with `enable_write_tools: true`, and each write requires human approval unless you disable that too (see [Write access](#write-access-optional)).

## Prerequisites

- A Freshservice instance (e.g. `https://your-domain.freshservice.com`)
- A Freshservice API key. In the Freshservice UI, click your profile picture → **Profile settings** — the API key is shown below the change password section.

The API key inherits the permissions of its user, so the tickets, changes and other objects HolmesGPT can read are determined by that user's role. Some object types (e.g. assets/CMDB) are only available on certain Freshservice plans; HolmesGPT reports the exact API error when an object type is not accessible.

Verify your credentials:

```bash
curl -u <your-api-key>:X "https://<your-domain>.freshservice.com/api/v2/tickets?per_page=1"
```

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      freshservice:
        enabled: true
        config:
          api_url: <your Freshservice URL>  # e.g. https://your-domain.freshservice.com
          api_key: <your Freshservice API key>

          # Optional
          default_page_size: 30  # Records per page when the LLM doesn't specify (max 100)
          timeout_seconds: 30  # HTTP timeout for Freshservice API requests
          health_check_object: tickets  # Object type listed on startup to verify connectivity
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "Show me all open urgent tickets in Freshservice"
    ```

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your Freshservice API key:

    ```bash
    kubectl create secret generic freshservice-credentials \
      --from-literal=api-key=your-freshservice-api-key \
      -n holmes
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Holmes Helm values:

    ```yaml
    additionalEnvVars:
      - name: FRESHSERVICE_API_KEY
        valueFrom:
          secretKeyRef:
            name: freshservice-credentials
            key: api-key

    toolsets:
      freshservice:
        enabled: true
        config:
          api_url: <your Freshservice URL>  # e.g. https://your-domain.freshservice.com
          api_key: "{{ env.FRESHSERVICE_API_KEY }}"
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your Freshservice API key:

    ```bash
    kubectl create secret generic freshservice-credentials \
      --from-literal=api-key=your-freshservice-api-key \
      -n default
    ```

    --8<-- "snippets/secret_namespace_note.md"

    Then add to your Robusta Helm values:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: FRESHSERVICE_API_KEY
          valueFrom:
            secretKeyRef:
              name: freshservice-credentials
              key: api-key
      toolsets:
        freshservice:
          enabled: true
          config:
            api_url: <your Freshservice URL>  # e.g. https://your-domain.freshservice.com
            api_key: "{{ env.FRESHSERVICE_API_KEY }}"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

### Optional Fields

| Option | Default | Description |
|--------|---------|-------------|
| `default_page_size` | `30` | Number of records returned per page when the LLM does not specify one (max 100). |
| `timeout_seconds` | `30` | Timeout for Freshservice API requests. |
| `health_check_object` | `tickets` | Object type listed on startup to verify connectivity and permissions. Change this if your API key cannot access tickets. |
| `enable_write_tools` | `false` | Expose tools that create, update and delete Freshservice objects. When `false`, only read tools are available. |
| `require_approval_for_writes` | `true` | When write tools are enabled, require human approval before each create/update/delete call. Set to `false` for fully autonomous writes. |

## Write Access (optional)

By default HolmesGPT can only read from Freshservice. To let it create, update and delete objects (tickets, problems, changes, notes, tasks, time entries, custom object records and more), enable write tools:

```yaml
toolsets:
  freshservice:
    enabled: true
    config:
      api_url: <your Freshservice URL>
      api_key: <your Freshservice API key>
      enable_write_tools: true
      # require_approval_for_writes: false  # only for fully autonomous writes
```

With writes enabled, six additional tools become available: `freshservice_create_object`, `freshservice_update_object`, `freshservice_delete_object` and their `*_related_object` counterparts for notes, replies, tasks and time entries.

!!! warning
    Every write call requires interactive human approval by default. Only set `require_approval_for_writes: false` in automated flows where the API key's own Freshservice role is scoped to what Holmes should be allowed to touch — deletes move tickets to trash and deactivate requesters/agents. Some object types are read-only in the Freshservice API itself (roles, workspaces, form fields, SLA policies, business hours, service catalog) regardless of this setting.

## Multiple Instances

```multi-instance
toolset: freshservice
name: Freshservice
config: |
  api_url: <your Freshservice URL>
  api_key: <your Freshservice API key>
```

## Common Use Cases

```
Which urgent Freshservice tickets are currently open, and what do their latest conversations say?
```

```
Are there any Freshservice changes planned for this week that could affect the payment service?
```

```
Find the Freshservice problem records related to database connectivity and summarize their root cause notes.
```

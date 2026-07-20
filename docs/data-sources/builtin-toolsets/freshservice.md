# Freshservice

Connect HolmesGPT to [Freshservice](https://www.freshworks.com/freshservice/) (Freshworks ITSM) to read tickets, problems, changes, releases, assets, requesters, agents, the service catalog, the knowledge base and every other Freshservice object via the [Freshservice API v2](https://api.freshservice.com/).

All access is read-only.

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

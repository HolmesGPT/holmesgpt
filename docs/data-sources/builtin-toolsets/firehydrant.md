# FireHydrant

Connect HolmesGPT to [FireHydrant](https://firehydrant.com/) so it acts as the investigation engine behind FireHydrant's AI investigations. When the toolset is enabled, the Holmes server polls the FireHydrant [investigations API](https://docs.firehydrant.com/reference/firehydrant-api) for pending investigations, claims each one, investigates the incident with every other enabled data source, and posts the report back to FireHydrant. The toolset also lets Holmes read investigations and incidents when you ask about them.

## Prerequisites

- A FireHydrant account with AI investigations.
- A FireHydrant API key (**Settings** > **API Keys**) that may read incidents and read, claim and complete investigations.
- HolmesGPT running as a server (Helm chart). The CLI exposes only the read tools and does not poll.

## Configuration

```yaml-toolset-config
toolsets:
  firehydrant/investigations:
    enabled: true
    config:
      api_key: "{{ env.FIREHYDRANT_API_KEY }}"  # the FireHydrant API key

      # Optional
      api_url: https://api.firehydrant.io  # FireHydrant API base URL (default)
      poll_investigations: true  # Claim and run pending investigations (default: true)
      poll_interval_seconds: 30  # How often to look for pending investigations (default: 30)
      request_timeout_seconds: 30  # Timeout of each FireHydrant API request (default: 30)
      # model: gpt-4.1  # Holmes model for FireHydrant investigations (default: the server's default model)
      # additional_system_prompt: "Our clusters run on EKS in eu-west-1."  # Extra instructions for every investigation
---
test: |
  holmes ask "List the pending FireHydrant investigations"
```

### Optional Fields

| Option | Default | Description |
|--------|---------|-------------|
| `api_url` | `https://api.firehydrant.io` | FireHydrant API base URL. Use `https://api.staging.firehydrant.io` for a staging organization. |
| `poll_investigations` | `true` | Poll for pending investigations, claim and run them. Set to `false` to keep only the read tools. |
| `poll_interval_seconds` | `30` | Seconds between two polls of `GET /v1/investigations?status=pending`. Minimum 5. |
| `request_timeout_seconds` | `30` | Timeout of each HTTP request to FireHydrant. |
| `model` | server default | Holmes model that runs the investigations. |
| `additional_system_prompt` | none | Instructions appended to the system prompt of every FireHydrant investigation. |

## How investigations run

1. Every `poll_interval_seconds`, Holmes lists pending investigations, oldest first.
2. Holmes claims an investigation with `POST /v1/investigations/{id}/claim`. If another engine claimed it first (HTTP 409), Holmes skips it.
3. Holmes reads the incident (`GET /v1/incidents/{id}`) and investigates it with every enabled data source.
4. Holmes checks that the investigation was not canceled, then posts the markdown report and a one-line summary with `POST /v1/investigations/{id}/complete`. If the investigation fails, Holmes reports it as `failed` with the error.

Investigations run one at a time per Holmes server. Enabling, reconfiguring or disabling the toolset takes effect on the next poll; no restart is needed.

Holmes logs every step under the `FireHydrant investigations` prefix:

```
FireHydrant investigations poller: polling https://api.firehydrant.io every 30s for pending investigations (model: default)
FireHydrant investigations: found 1 pending investigation(s)
FireHydrant investigations: claimed investigation 7f2c... (incident #42 'Checkout latency') as holmes-holmes-5d8f-..., starting investigation
FireHydrant investigations: completed investigation 7f2c... (incident #42 'Checkout latency') in 84.3s: Connection pool exhaustion in payments-db.
```

## Common Use Cases

```
List the pending FireHydrant investigations
```

```
What did the last completed FireHydrant investigation find?
```

```
Show me FireHydrant incident 3b1f... and investigate it
```

## Troubleshooting

```bash
# The toolset fails with 401/403 - verify the key against the API
curl -H "Authorization: Bearer $FIREHYDRANT_API_KEY" "https://api.firehydrant.io/v1/investigations?status=pending&per_page=1"

# Nothing is claimed - check the poller state in the Holmes logs
kubectl logs -n <namespace> deployment/<holmes-deployment> | grep "FireHydrant investigations"
```

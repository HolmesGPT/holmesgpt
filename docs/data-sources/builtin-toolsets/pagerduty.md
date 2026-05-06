# PagerDuty

Connect HolmesGPT to PagerDuty to read incidents, services, on-call schedules, and alerts. The toolset is **read-only** and supports both single-tenant (global API key) and multi-project deployments.

## Capabilities

The PagerDuty toolset exposes 5 tools:

- `list_pagerduty_incidents` — filter by status, service, urgency
- `get_pagerduty_incident` — full details for a specific incident
- `list_pagerduty_services` — services with optional name filter
- `list_pagerduty_alerts` — alert entries for a given incident
- `get_pagerduty_oncall` — currently on-call for an escalation policy or schedule

## Quick Start

### 1. Generate an API key

In PagerDuty, navigate to **Integrations → API Access Keys** and create a new **General Access** key (v2 REST API). Key format: `u+xxxxxxxxxxxxxxxxxxxx`.

PagerDuty API keys are **account-wide** — there is no native "project-scoped" key. HolmesGPT enforces project scope via `service_ids` and `team_ids` filters applied to every query.

### 2. Configure HolmesGPT

HolmesGPT supports two modes:

=== "Global (single-tenant)"

    Set an environment variable:

    ```bash
    export PAGERDUTY_API_KEY="u+xxxxxxxxxxxxxxxxxxxx"
    ```

    Or add to `~/.holmes/config.yaml`:

    ```yaml
    toolsets:
      pagerduty:
        enabled: true
        config:
          api_key: "{{ env.PAGERDUTY_API_KEY }}"
          default_limit: 25
          # Optional: restrict this global toolset to specific teams/services
          team_ids: ["PTEAM_A"]
          service_ids: ["PSVC1", "PSVC2"]
    ```

    The global toolset acts as a fallback when no per-project PagerDuty instance is configured for the active project.

=== "Per-project (HolmesGPT server)"

    Store the API key in AWS Secrets Manager as JSON:

    ```json
    { "api_key": "u+xxxxxxxxxxxxxxxxxxxx" }
    ```

    In the HolmesGPT UI:

    1. Go to **Instances → New Instance**.
    2. Pick type `pagerduty` and give it a name (e.g. `pd-acme`).
    3. Set the **Secret ARN** to the Secrets Manager secret above.
    4. Add **Service IDs** and/or **Team IDs** under "PagerDuty Project Scope" to define the scope for this instance. IDs look like `PSVC123` or `PTEAM456`.
    5. Click **Test Connection** to verify the key and scope.
    6. Tag the instance (e.g. `project=acme`) so the project picks it up via tag matching.

## What project scoping enforces

Once `service_ids` and/or `team_ids` are set on a per-project instance:

- `list_pagerduty_incidents`, `list_pagerduty_services`, and `get_pagerduty_oncall` auto-append the configured `service_ids[]` / `team_ids[]` filters to every request. (`get_pagerduty_oncall` uses only `team_ids`; `/oncalls` does not support `service_ids`.)
- `get_pagerduty_incident` blocks lookups of incidents whose service is outside the project's scope — the tool returns an error rather than the data.
- `list_pagerduty_alerts` verifies the parent incident is in scope before fetching alerts.
- If the user (via an LLM prompt) supplies filters outside the project's scope, those are intersected with the instance scope — users can narrow but never widen. A note explaining the narrowing is surfaced back to the LLM.

Scope guards are enforced in Python, not in prompt instructions — they cannot be defeated by LLM misbehavior.

## Common Use Cases

```
"List the active PagerDuty incidents for our services."
"Who is on-call for the checkout team right now?"
"Give me the details of PagerDuty incident PINC-ABC123."
"Show alerts for the most recent high-urgency incident."
```

## Troubleshooting

```bash
# Test credentials locally
curl -H "Authorization: Token token=$PAGERDUTY_API_KEY" \
  -H "Accept: application/vnd.pagerduty+json;version=2" \
  https://api.pagerduty.com/services?limit=1

# If 401 Unauthorized → API key is invalid or revoked
# If 429 Too Many Requests → rate-limited; wait Retry-After seconds
```

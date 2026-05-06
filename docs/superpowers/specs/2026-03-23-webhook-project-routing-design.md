# Webhook-to-Project Routing

**Date:** 2026-03-23
**Status:** Approved
**Scope:** ADO, PagerDuty, Salesforce webhooks

## Problem

All webhook-triggered investigations currently save with `project_id=""`, meaning they use global instances and are not associated with any Holmes project. When a defect is raised in "PDI Dispatch" in ADO, the investigation should use that project's specific integrations, not the global fallback.

## Design

### Data Model

Add `WebhookRouting` to the `Project` model in `frontend/projects.py`:

```python
class WebhookRouting(BaseModel):
    ado: list[str] = []          # ADO team project names (System.TeamProject)
    pagerduty: list[str] = []    # PagerDuty service names or IDs
    salesforce: list[str] = []   # Salesforce account names or IDs

class Project(BaseModel):
    # ... existing fields ...
    webhook_routing: Optional[WebhookRouting] = None
```

Projects without `webhook_routing` (or with empty lists) are never matched by webhooks.

### Payload Field Extraction

Each webhook type extracts a natural identifier from the incoming payload:

| Webhook | Payload field | Example |
|---|---|---|
| ADO | `resource.fields["System.TeamProject"]` | `"PDI Dispatch"` |
| PagerDuty | `data.service.summary` (fallback: `data.service.id`) | `"checkout-api"` |
| Salesforce | `AccountName` or `AccountId` from JSON/SOAP payload | `"Acme Corp"` |

For ADO, `System.TeamProject` is always present in `workitem.created` / `workitem.updated` events. It is a plain string in created events and may be a `{newValue: ...}` dict in updated events (reuse the existing `_ado_field()` helper).

For PagerDuty, the `data.service` object contains both `summary` (human name) and `id` (PXXXXXX). Match against both.

For Salesforce, extract from SOAP XML (`<AccountName>`) or JSON (`AccountName` / `AccountId`). Both formats are already parsed in the handler.

### Routing Function

A shared resolver in `frontend/projects.py`:

```python
def resolve_project_for_webhook(source: str, identifier: str) -> Optional[Project]:
    """Find the project whose webhook_routing matches the given source and identifier.

    Args:
        source: Webhook source type ("ado", "pagerduty", "salesforce")
        identifier: The extracted identifier from the webhook payload

    Returns:
        Matching Project, or None (use global instances)
    """
    if not identifier:
        return None
    for project in get_project_store().list():
        routing = project.webhook_routing
        if not routing:
            continue
        candidates = getattr(routing, source, [])
        if any(c.strip().lower() == identifier.strip().lower() for c in candidates):
            return project
    return None
```

Case-insensitive matching. First match wins. If multiple projects claim the same identifier, log a warning at startup or on save (not at request time, to avoid log spam).

### Webhook Handler Changes

Each of the three webhook handlers gets minimal changes:

1. **Extract identifier** from payload (1-3 lines)
2. **Call `resolve_project_for_webhook()`** (1 line)
3. **Pass `project_id`** to the investigation (change from `""` to `project.id` or `""`)
4. **Use project-scoped config** when creating the LLM — if a project is found, its tag filter determines which integration instances are used

#### ADO Example

```python
# After existing field extraction:
ado_team_project = _ado_field(fields.get("System.TeamProject"))
matched_project = resolve_project_for_webhook("ado", ado_team_project)
project_id = matched_project.id if matched_project else ""

# In _run_ado_investigation:
# Pass project_id to Investigation constructor (line ~2158)
# Use matched_project.tag_filter when loading toolsets
```

The same pattern applies to PagerDuty and Salesforce handlers.

### API Changes

No new endpoints. The existing project CRUD endpoints (`POST /api/projects`, `PUT /api/projects/{id}`, `GET /api/projects`) already serialize/deserialize the full `Project` model. Adding `webhook_routing` to the model makes it available automatically.

### Frontend UI

On the project create/edit form, add a "Webhook Routing" section below the existing fields:

- Three multi-value tag inputs (one per webhook source)
- Each input allows adding/removing string values
- Label: "ADO Projects", "PagerDuty Services", "Salesforce Accounts"
- Helper text: "Events matching these values will be investigated using this project's integrations."
- Section is collapsible and collapsed by default to avoid cluttering the form for users who don't use webhooks

### Fallback Behavior

| Scenario | Behavior |
|---|---|
| No routing match | `project_id=""`, use global instances (current behavior, unchanged) |
| Project matched | `project_id=project.id`, use project's tag-filtered instances |
| Multiple projects match same identifier | First match wins, log warning |
| Webhook source has no routing field (e.g., future "datadog" webhook) | Falls through to global |
| `webhook_routing` is `None` or all lists empty | Project is skipped during matching |

### Duplicate Routing Validation

When saving a project, check if any of its routing values are already claimed by another project. If so, return a 409 Conflict with a message like: `"ADO project 'PDI Dispatch' is already routed to project 'PDI Dispatch Ops'"`. This prevents ambiguous routing.

## Files Changed

| File | Change |
|---|---|
| `frontend/projects.py` | Add `WebhookRouting` model, `webhook_routing` field on `Project`, `resolve_project_for_webhook()` function, duplicate validation on save |
| `frontend/server_frontend.py` | ADO webhook: extract `System.TeamProject`, resolve project, pass `project_id`. PagerDuty webhook: extract service, same pattern. Salesforce webhook: extract account, same pattern. |
| `frontend/src/components/` | Project form: add webhook routing section with tag inputs |
| `frontend/src/lib/api.ts` | Add `WebhookRouting` and `webhook_routing` to `Project` TypeScript interface |

## Out of Scope

- Wildcard/regex matching (e.g., `PDI *`) -- keep it simple with exact match for now
- Webhook routing from Datadog (no webhook endpoint yet)
- Per-webhook-source write-back configuration changes (already exists via `webhook_write_back`)
- Automatic project creation from webhook events

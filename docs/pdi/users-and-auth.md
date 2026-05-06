# Users, Roles & API Keys

## Authentication Methods

Holmes supports three authentication methods. All requests must include a valid credential -- unauthenticated access is not possible.

**Okta SSO (browser)**

Users log in via Okta using the PKCE authorization code flow. The React frontend redirects to Okta, the user authenticates, and Okta returns a JWT that the frontend sends as a Bearer token on every API request. The backend validates the token against the Okta OIDC issuer (`https://pdisoftware.okta.com/oauth2/default`).

On first login, a user record is automatically created in DynamoDB. The Okta group `HolmesGPT-Users` controls who can access Holmes -- only members of this group will have a valid token.

**API Keys**

For programmatic access, Holmes supports API keys prefixed with `hgpt_`. These are scoped to specific projects and created by super-admins through the Settings page. The raw key is shown once at creation time; only a SHA-256 hash is stored in DynamoDB.

Auth header format: `Authorization: Bearer hgpt_<64 hex chars>`

**Legacy API Key**

The `HOLMES_API_KEY` environment variable provides backwards-compatible access with full super-admin privileges. This is primarily used for initial setup and migration scenarios.

## Roles

| Role | Scope | Permissions |
|---|---|---|
| `super-admin` | Global | Manage all projects, users, API keys, and settings |
| `project-admin` | Per-project | Manage project instances, assign users within their project |
| `read-only` | Per-project | Ask questions and view investigation results |

A user can have different roles on different projects. For example, `project-admin` on "Logistics" and `read-only` on "POS". The `super-admin` global role grants access to everything regardless of per-project assignments.

## User Lifecycle

**Step 1:** An admin adds the user's email to the Okta group `HolmesGPT-Users`.

**Step 2 (optional):** A super-admin can pre-invite the user in the Holmes UI (Settings > Users). This creates a placeholder record so that project role assignments can be configured before the user's first login.

**Step 3:** The user logs in via Okta. Their DynamoDB record is created (or the invited placeholder is linked to their Okta identity). If their email matches the `HOLMES_SUPER_ADMIN_EMAIL` environment variable, they are automatically granted `super-admin`.

**Step 4:** A super-admin assigns project roles via Settings > Users. The user can then ask questions scoped to their assigned projects.

## API Keys

API keys provide programmatic access for CI/CD webhooks, external integrations, and scripted queries.

**Format:** `hgpt_` followed by 64 hex characters. The raw key is displayed once at creation and cannot be retrieved later. Only the SHA-256 hash is stored.

**Project scoping:**

- Empty project list = access to all projects
- Specific project IDs = access restricted to those projects only

**Creating an API key:**

1. Navigate to Settings > API Keys in the Holmes UI
2. Enter a descriptive name (e.g., "ADO Webhook - Logistics")
3. Select which projects this key can access (or leave empty for all)
4. Copy the generated key immediately -- it will not be shown again

**Using an API key:**

```bash
curl -X POST https://holmesgpt.dev.platform.pditechnologies.com/api/chat \
  -H "Authorization: Bearer hgpt_abc123..." \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the status of the logistics prod cluster?"}'
```

## Removing a User

Users are soft-deleted: their status is set to `removed` and all project role assignments are deleted, but the record is preserved so that Okta sync does not re-create it. A super-admin can remove users from Settings > Users.

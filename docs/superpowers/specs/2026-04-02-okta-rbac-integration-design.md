# Okta Authentication & RBAC Integration Design

**Date:** 2026-04-02
**Status:** Draft

## Overview

Replace the existing single-user username/password authentication with Okta OIDC (PKCE flow) and add a role-based access control (RBAC) system. Users authenticate via Okta, and super-admins manage fine-grained project-level permissions from the UI. All role assignments are stored in DynamoDB.

## Goals

- Okta-based login with no client secret required (SPA PKCE flow)
- Three permission levels: super-admin, project-admin, read-only
- Super-admins manage all users and projects globally
- Project-admins have full control within their assigned projects
- Read-only users can investigate and view within assigned projects but cannot edit anything
- Pre-provision users by email before their first login
- Preserve existing API key auth for programmatic access

## Okta Configuration

### Okta Setup (One-Time, Outside the App)

- Create a single Okta **SPA Application** (Authorization Code + PKCE grant type)
- Create one Okta group: `HolmesGPT-Users` — membership controls who can log in
- Configure the app to include the `groups` claim in the ID token
- Redirect URIs: `https://<your-domain>/login/callback`, `https://<your-domain>`

### Why a Single Okta Group

All RBAC is managed in DynamoDB, not Okta. The single group only gates who can log in. Creating groups per project would be unmanageable at scale (many projects = many groups). One group keeps Okta administration simple.

## Authentication Flow

### Frontend (PKCE)

1. App boots — `OktaAuthProvider` initializes `OktaAuth` client with `clientId` + `issuer`
2. Check for valid cached Okta token in memory
3. No valid token — show Okta login page ("Sign in with Okta" button triggers redirect)
4. User authenticates — Okta redirects back with authorization code
5. Frontend exchanges code for tokens via PKCE (no secret needed)
6. Tokens stored in memory only (not localStorage — XSS protection)
7. Every API call includes `Authorization: Bearer <id_token>`
8. Token nearing expiry — silent refresh via hidden iframe
9. Logout — revoke token with Okta, clear memory, redirect to login

### Frontend Config

Only two values needed, from build-time environment variables:

```typescript
{
  oktaIssuer: "https://your-org.okta.com/oauth2/default",  // VITE_OKTA_ISSUER
  oktaClientId: "0oa..."                                    // VITE_OKTA_CLIENT_ID
}
```

### Backend JWT Validation

1. Extract `Authorization: Bearer <token>` from request
2. Detect token type: JWT (contains dots) vs API key (plain string)
3. For JWT: fetch Okta's JWKS from `{issuer}/.well-known/openid-configuration` — cache keys for 24h
4. Validate: signature, expiry, audience (must match client ID), issuer
5. Extract claims: `sub` (Okta user ID), `email`, `name`, `groups`
6. Verify `HolmesGPT-Users` is in the `groups` claim
7. Attach user identity to request context

### Backward Compatibility

Existing API key auth (`Authorization: Bearer <api_key>`) continues to work. The middleware detects whether the bearer token is a JWT or an API key and routes accordingly.

## RBAC Data Model (DynamoDB)

### User Entities

Extends the existing single-table design with new entity types:

```
# User profile — created on first Okta login or via invite-by-email
USER#<okta_sub> | META
{
  email: "alice@company.com",
  name: "Alice Smith",
  okta_sub: "00u1abc...",
  role: "super-admin" | null,       # global role (only super-admin is global)
  status: "active",
  created_at: "2026-04-02T...",
  last_login: "2026-04-02T..."
}

# Pre-provisioned user (invited by email, hasn't logged in yet)
USER#email:alice@company.com | META
{
  email: "alice@company.com",
  name: null,
  okta_sub: null,
  role: null,
  status: "invited",
  created_at: "2026-04-02T..."
}

# Project role assignment — one record per user-project pair
USER#<okta_sub> | PROJECT#<project_id>
{
  role: "project-admin" | "read-only",
  assigned_by: "00u1xyz...",
  assigned_at: "2026-04-02T..."
}
```

### GSI for Reverse Lookups

```
GSI name: gsi-sk-pk
GSI: sk (partition) -> pk (sort)
# Enables: "Give me all users assigned to project X"
PROJECT#<project_id> -> USER#<okta_sub>
```

### Invite-by-Email Linking Flow

1. Super-admin invites `alice@company.com` and assigns roles — creates `USER#email:alice@company.com` records
2. Alice logs in via Okta — JWT arrives with `sub: "00u1abc"` and `email: "alice@company.com"`
3. Backend checks `USER#00u1abc` — not found
4. Backend checks `USER#email:alice@company.com` — found (invited)
5. Migrate: create `USER#00u1abc` with all data + project role assignments, delete email-keyed records
6. Alice immediately has her pre-assigned permissions

Email matching uses the `email` claim from the Okta JWT. The invited email must match the user's primary Okta email exactly.

### Role Hierarchy and Resolution

1. If `user.role == "super-admin"` — full access to everything, skip project checks
2. Else, look up `USER#<sub> | PROJECT#<pid>` for the requested project
3. `project-admin` — full CRUD within that project
4. `read-only` — investigate and view within that project, no modifications
5. No record found — no access to that project

## Permission Matrix

| Action | super-admin | project-admin | read-only |
|--------|:-----------:|:-------------:|:---------:|
| Create/delete projects | Yes | No | No |
| Edit project config (tags, webhooks) | Yes | Yes (own projects) | No |
| Start investigations | Yes | Yes (own projects) | Yes (own projects) |
| View investigation history | Yes | Yes (own projects) | Yes (own projects) |
| View project config | Yes | Yes (own projects) | Yes (own projects) |
| Manage integrations/instances | Yes | Yes (own projects) | No |
| Manage users & assign roles | Yes | No | No |
| App settings & LLM config | Yes | No | No |
| View user list | Yes | No | No |

## Backend Middleware & API Enforcement

### Middleware Chain

```
Request
  -> OktaJWTMiddleware (validate token, extract identity)
  -> RBACMiddleware (load roles from DynamoDB, attach permissions)
  -> Route handler (check permissions per action)
```

### OktaJWTMiddleware

- Skips public paths: `/healthz`, `/readyz`, `/assets/*`, `/api/webhook/*`
- Detects token type: JWT (contains dots) -> Okta validation; plain string -> existing API key check
- On valid JWT: sets `request.state.user = { sub, email, name, groups }`
- On invalid/expired token: returns 401 Unauthorized
- JWKS keys cached in memory with 24h TTL, background refresh

### RBACMiddleware

- Reads `request.state.user.sub` -> queries DynamoDB for `USER#<sub> | META` and all `USER#<sub> | PROJECT#*`
- Attaches `request.state.permissions = { global_role, project_roles: { pid: role, ... } }`
- First-time user (no DynamoDB record): auto-creates `USER#<sub> | META` with `role: null` (no access)
- Bootstrap check: if email matches `HOLMES_SUPER_ADMIN_EMAIL` env var and user has no global role -> set `role: "super-admin"`
- DynamoDB query caching: in-memory with 5-minute TTL, invalidated on role changes

### Endpoint-Level Enforcement

```python
async def require_super_admin(request: Request):
    if request.state.permissions.global_role != "super-admin":
        raise HTTPException(403, "Super-admin required")

async def require_project_access(request: Request, project_id: str, min_role: str = "read-only"):
    perms = request.state.permissions
    if perms.global_role == "super-admin":
        return
    project_role = perms.project_roles.get(project_id)
    if not project_role:
        raise HTTPException(403, "No access to this project")
    if min_role == "project-admin" and project_role != "project-admin":
        raise HTTPException(403, "Project-admin required")
```

### Endpoint Protection Map

| Endpoint pattern | Required permission |
|---|---|
| `POST /api/projects` | super-admin |
| `DELETE /api/projects/{id}` | super-admin |
| `PUT /api/projects/{id}` | project-admin on that project |
| `GET /api/projects/{id}` | any role on that project |
| `POST /api/chat` (with project_id) | read-only+ on that project |
| `POST /api/investigate` (with project_id) | read-only+ on that project |
| `GET /api/investigations` | filtered to accessible projects |
| `POST /api/instances` | project-admin on associated project |
| `GET /api/users`, `PUT /api/users/{id}/roles` | super-admin |
| `PUT /api/settings/*` | super-admin |
| `GET /api/projects` | returns only projects user has access to |

## Frontend Architecture

### New Dependencies

- `@okta/okta-auth-js` — core OIDC/PKCE library (lightweight, no `@okta/okta-react` needed)

### Page Routing by Role

| User state | What they see |
|---|---|
| Not authenticated | Okta login page (sign-in button) |
| Authenticated, no roles | "Pending Access" page — contact an administrator |
| Authenticated, read-only | Project selector -> scoped view (chat, investigate, history — no edit controls) |
| Authenticated, project-admin | Project selector -> full project management |
| Authenticated, super-admin | Everything — plus Users page, Settings, project creation |

### Navigation Visibility

- **Always visible** (if user has any project access): Chat, Investigate, History, Analytics, Docs
- **Project-admin+ only**: Integrations, Instances, Project Config
- **Super-admin only**: Projects (create/delete), Users, Settings

### Project Scoping

- Project selector dropdown in the header (shows only accessible projects)
- All API calls include the selected `project_id`
- Super-admins see all projects; others see only their assigned ones

### API Client Changes

- Replace `credentials: 'include'` (cookies) with `Authorization: Bearer <id_token>` header
- Add interceptor: if any API call returns 401 -> trigger re-authentication

## User Management UI (Super-Admin Only)

### User List View

- Table: Name, Email, Global Role, Project Assignments, Last Login, Status
- Status: "Active" (has logged in), "Invited" (pre-provisioned, hasn't logged in)
- Search/filter by name or email

### User Detail / Role Assignment

- Click user -> detail panel
- Global role section: toggle super-admin on/off
- Project assignments: list of projects with role dropdown (`None` | `read-only` | `project-admin`)
- Add/remove project assignments
- Each assignment shows `assigned_by` and `assigned_at` for audit trail

### Invite by Email

- Super-admin types an email address and assigns roles
- Creates placeholder `USER#email:<email>` record in DynamoDB
- On first login, matched by email and linked to Okta identity
- User immediately has pre-assigned permissions

## Environment Variables

### Frontend (Build-Time)

| Variable | Required | Example |
|---|---|---|
| `VITE_OKTA_ISSUER` | Yes | `https://your-org.okta.com/oauth2/default` |
| `VITE_OKTA_CLIENT_ID` | Yes | `0oa1abc2def3ghi4j5k6` |

### Backend (Runtime)

| Variable | Required | Example |
|---|---|---|
| `OKTA_ISSUER` | Yes | `https://your-org.okta.com/oauth2/default` |
| `OKTA_CLIENT_ID` | Yes | `0oa1abc2def3ghi4j5k6` |
| `HOLMES_SUPER_ADMIN_EMAIL` | Yes (first deploy) | `admin@company.com` |

### Removed

| Variable | Status |
|---|---|
| `HOLMES_UI_USERNAME` | Removed |
| `HOLMES_UI_PASSWORD` | Removed |

## Migration & Backward Compatibility

### Breaking Changes

- Old username/password login is removed
- `HOLMES_UI_USERNAME` and `HOLMES_UI_PASSWORD` env vars no longer used

### What Stays

- API key auth (`Authorization: Bearer <api_key>`) — unchanged for programmatic access
- All existing DynamoDB entities (`PROJECT#*`, `INSTANCE#*`, `INVESTIGATION#*`) — untouched
- Webhook HMAC auth — unchanged

### DynamoDB Changes

- No schema migration — additive only (new `USER#*` entity types)
- GSI (`sk -> pk`) needs to be created once (can be added without downtime)

### First Deployment Checklist

1. Set `OKTA_ISSUER`, `OKTA_CLIENT_ID`, `HOLMES_SUPER_ADMIN_EMAIL` env vars on backend
2. Set `VITE_OKTA_ISSUER`, `VITE_OKTA_CLIENT_ID` on frontend build
3. Create Okta SPA application with PKCE, configure redirect URIs
4. Create `HolmesGPT-Users` group in Okta, add users
5. Deploy — first login by the bootstrap email auto-grants super-admin
6. Super-admin invites other users by email and assigns roles from the UI

### Rollback Plan

- Revert to previous deployment — old env vars restore original auth
- No DynamoDB data is modified or deleted — new entities are additive

### Project Deletion Cleanup

When a project is deleted, all `USER#<sub> | PROJECT#<project_id>` role assignment records for that project are also deleted. This is handled in the existing project deletion endpoint.

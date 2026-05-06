# Secure Public API Design

**Date:** 2026-04-14
**Status:** Approved
**Author:** srinivasreddy.v

## Goal

Expose HolmesGPT investigation APIs securely so that other gen AI applications can consume them programmatically. Provide auto-generated Swagger documentation, per-client API keys with project scoping, and WAF-level rate limiting.

## API Surface

Versioned router at `/api/v1/` — core investigation endpoints only:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/chat` | Conversational investigation (streaming SSE) |
| POST | `/api/v1/investigate` | One-shot investigation (synchronous JSON) |
| GET | `/api/v1/investigations` | List past investigations (filtered by project) |
| GET | `/api/v1/investigations/{id}` | Get single investigation |
| GET | `/api/v1/investigations/similar` | Search similar investigations (RAG) |
| GET | `/api/v1/models` | List available LLM models |

These are thin wrappers around existing internal endpoints — same business logic, with explicit Pydantic request/response models for OpenAPI schema generation.

Existing `/api/chat`, `/api/investigate`, etc. continue unchanged for the frontend.

## Authentication

Two auth methods, both via `Authorization: Bearer <token>`:

### Okta JWT (existing)

Unchanged. Validated via JWKS. Used by interactive users and the Swagger "Try it out" feature.

### Per-Client API Keys (new)

**Key format:** `hgpt_<32-random-hex>` — the `hgpt_` prefix makes keys identifiable in logs and secret scanners.

**Auth flow:**

1. Consumer sends `Authorization: Bearer hgpt_a3f2...`
2. Middleware detects the `hgpt_` prefix — API key path (not Okta JWT)
3. SHA-256 hash the key, look up in DynamoDB
4. Check `status == active`
5. Inject a synthetic user with `project_ids` from the key
6. If the request includes `project_id`, verify it's in the key's allowed list

**Backward compatibility:** The existing `HOLMES_API_KEY` env var continues to work as super-admin. New `hgpt_` keys are the per-client scoped mechanism.

## Data Model

New items in the existing `holmesgpt-dev-config` DynamoDB table:

```
PK: APIKEY#<key-hash>
SK: APIKEY#<key-hash>

Fields:
  key_hash: str        # SHA-256 hash of the API key (never store plaintext)
  key_prefix: str      # First 8 chars for display (e.g., "hgpt_a3f2...")
  name: str            # Human-readable label (e.g., "acme-ai-agent")
  project_ids: list    # Scoped projects (empty = all projects)
  created_by: str      # Email of user who created it
  created_at: str      # ISO timestamp
  last_used_at: str    # Updated on each API call
  status: str          # "active" | "revoked"
```

## API Key Management Endpoints

Behind Okta auth, super-admin only:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/api-keys` | List keys (prefix + name + metadata, never full key) |
| POST | `/api/api-keys` | Create key (returns full key ONCE) |
| DELETE | `/api/api-keys/{key_prefix}` | Revoke key |

## Swagger UI

FastAPI auto-generates Swagger from the typed `APIRouter`:

- **Swagger UI:** `/docs` (behind Okta auth — not in `EXEMPT_PATHS`)
- **OpenAPI spec:** `/openapi.json` (also behind Okta auth)
- **Authorize dialog:** supports pasting Okta JWT or `hgpt_` API key as Bearer token

OpenAPI metadata:

```python
app = FastAPI(
    title="HolmesGPT API",
    description="AI-powered infrastructure investigation API",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)
```

Request/response models use existing Pydantic models from `holmes/core/models.py` where possible, with explicit schemas for Swagger documentation.

## WAF Rate Limiting

AWS WAF on the ALB, managed via OpenTofu:

- **Scope:** `/api/v1/*` paths only (frontend and webhooks unaffected)
- **Default limit:** 100 requests per 5-minute window per IP
- **API key consumers:** 300 requests per 5-minute window (keyed by `Authorization` header)
- **Action:** Block with 429 response

Implementation: new `infra/waf.tf` with `aws_wafv2_web_acl` and ALB association.

## File Changes

| File | Change |
|------|--------|
| `frontend/api_v1.py` (new) | Public API router with typed endpoints, Pydantic models, OpenAPI tags |
| `frontend/server_frontend.py` | Mount the v1 router, update middleware for `hgpt_` key detection |
| `frontend/api_keys.py` (new) | API key store (DynamoDB CRUD), key generation, hash-based lookup |
| `frontend/rbac.py` | Add project-scoped permission check for API key users |
| `infra/waf.tf` (new) | WAF web ACL with rate-based rules, ALB association |
| `infra/main.tf` | Wire WAF outputs |

## What Stays Unchanged

- Existing `/api/chat`, `/api/investigate` — frontend continues using these
- Existing `HOLMES_API_KEY` env var — backward compatible
- Webhook endpoints — their own auth mechanisms
- Okta JWT flow — untouched

## Security Layers

1. **ALB + WAF** — rate limiting, IP blocking
2. **Auth middleware** — Okta JWT or API key validation
3. **Project scoping** — API key can only access its allowed projects
4. **Read-only toolsets** — Holmes tools are read-only by design

## Consumer Experience

1. Super-admin creates an API key in the UI, scoped to specific projects
2. Consumer receives the key once: `hgpt_a3f29e...`
3. Consumer opens `/docs` (after Okta login) to explore the API
4. Consumer calls endpoints with `Authorization: Bearer hgpt_a3f29e...` and `project_id` in the body
5. WAF enforces rate limits at the ALB level

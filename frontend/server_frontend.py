"""
FastAPI extension for HolmesGPT frontend: basic auth + static file serving.
This module is loaded by a custom server wrapper that imports the original server.py
and adds frontend routes.

Security: ALL routes require authentication except /auth/login, /healthz, /readyz.
This includes /api/* endpoints — no unauthenticated access.
"""

import hmac
import json
import logging
import os
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from starlette.middleware.base import BaseHTTPMiddleware

from okta_jwt import validate_okta_token, OKTA_ISSUER, OKTA_CLIENT_ID
from rbac import ensure_user_exists, UserPermissions

STATIC_DIR = Path("/app/static")


class OktaAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate requests via Okta JWT or API key."""

    EXEMPT_PATHS = ("/healthz", "/readyz", "/login/callback")
    EXEMPT_PREFIXES = ("/assets/", "/favicon", "/api/webhook/")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in self.EXEMPT_PATHS:
            return await call_next(request)

        if any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES):
            return await call_next(request)

        # Extract Authorization header
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            # No auth header -> serve SPA for browser requests, 401 for API
            if request.headers.get("accept", "").startswith("text/html"):
                index = STATIC_DIR / "index.html"
                if index.exists():
                    return FileResponse(index, media_type="text/html")
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        token = auth_header[7:]

        # Detect token type: JWT has 2+ dots, API key is a plain string
        if token.count(".") >= 2:
            # JWT token -> validate with Okta
            try:
                claims = validate_okta_token(token)
                permissions = ensure_user_exists(
                    sub=claims["sub"],
                    email=claims["email"],
                    name=claims["name"],
                )
                request.state.user = claims
                request.state.permissions = permissions
            except HTTPException:
                raise
            except Exception as e:
                logging.error("JWT validation error: %s", e)
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        else:
            # API key -> check against HOLMES_API_KEY env var
            api_key = os.environ.get("HOLMES_API_KEY", "")
            if not api_key or not hmac.compare_digest(token, api_key):
                return JSONResponse({"detail": "Invalid API key"}, status_code=401)

            # Synthetic admin user for API key auth
            from rbac import UserRecord, UserPermissions as UP
            synthetic_user = {
                "sub": "api-key",
                "email": "api@holmesgpt.internal",
                "name": "API Key",
                "groups": [],
            }
            synthetic_record = UserRecord(
                sub="api-key",
                email="api@holmesgpt.internal",
                name="API Key",
                global_role="super-admin",
                status="active",
            )
            request.state.user = synthetic_user
            request.state.permissions = UP(user=synthetic_record, project_roles={})

        return await call_next(request)


def _restore_llm_overrides_from_dynamodb(config) -> None:
    """Load persisted LLM instruction overrides from DynamoDB into the in-memory config."""
    if config is None:
        return
    table_name = os.environ.get("HOLMES_DYNAMODB_TABLE", "")
    if not table_name:
        return
    try:
        from projects import get_llm_store  # noqa: PLC0415

        overrides = get_llm_store().load_all()
        for toolset_name, instructions in overrides.items():
            is_mcp = bool(config.mcp_servers and toolset_name in config.mcp_servers)
            if is_mcp:
                if config.mcp_servers is None or toolset_name not in config.mcp_servers:
                    logging.debug(
                        "Ignoring DynamoDB LLM override for unknown MCP server '%s'",
                        toolset_name,
                    )
                    continue
                config.mcp_servers[toolset_name]["llm_instructions"] = instructions
            else:
                if config.toolsets is None or toolset_name not in config.toolsets:
                    logging.debug(
                        "Ignoring DynamoDB LLM override for unknown toolset '%s'",
                        toolset_name,
                    )
                    continue
                config.toolsets[toolset_name]["llm_instructions"] = instructions
        if overrides:
            logging.info(
                "Restored %d LLM instruction override(s) from DynamoDB", len(overrides)
            )
    except Exception:
        logging.warning("Failed to restore LLM overrides from DynamoDB", exc_info=True)


def _restore_toolset_state_from_dynamodb(config) -> None:
    """Load persisted toolset enable/disable state from DynamoDB into the in-memory config."""
    if config is None:
        return
    table_name = os.environ.get("HOLMES_DYNAMODB_TABLE", "")
    if not table_name:
        return
    try:
        from projects import get_toolset_state_store  # noqa: PLC0415

        states = get_toolset_state_store().load_all()
        for toolset_name, enabled in states.items():
            if config.toolsets is None or toolset_name not in config.toolsets:
                logging.debug(
                    "Ignoring DynamoDB state for unknown toolset '%s'",
                    toolset_name,
                )
                continue
            config.toolsets[toolset_name]["enabled"] = enabled
        if states:
            logging.info("Restored %d toolset state(s) from DynamoDB", len(states))
    except Exception:
        logging.warning("Failed to restore toolset states from DynamoDB", exc_info=True)


def _restore_toolset_config_from_dynamodb(config) -> None:
    """Load persisted toolset config overrides from DynamoDB into the in-memory config.

    Performs a shallow merge: DynamoDB keys override Helm keys.
    Keys containing {{ env.* }} in the Helm config are never overridden.
    """
    if config is None:
        return
    table_name = os.environ.get("HOLMES_DYNAMODB_TABLE", "")
    if not table_name:
        return
    try:
        from projects import get_toolset_config_store  # noqa: PLC0415

        overrides = get_toolset_config_store().load_all()
        restored = 0
        for toolset_name, config_override in overrides.items():
            if config.toolsets is None or toolset_name not in config.toolsets:
                logging.debug(
                    "Ignoring DynamoDB config override for unknown toolset '%s'",
                    toolset_name,
                )
                continue
            if "config" not in config.toolsets[toolset_name]:
                config.toolsets[toolset_name]["config"] = {}

            helm_config = config.toolsets[toolset_name]["config"]
            for key, value in config_override.items():
                # Never override {{ env.* }} secret references from Helm
                helm_value = helm_config.get(key, "")
                if isinstance(helm_value, str) and "{{ env." in helm_value:
                    continue
                helm_config[key] = value
            restored += 1

        if restored:
            logging.info(
                "Restored %d toolset config override(s) from DynamoDB", restored
            )
    except Exception:
        logging.warning("Failed to restore toolset config from DynamoDB", exc_info=True)


# In-memory flag for webhook development mode (auth bypass).
# Default is False — authentication is enforced. Persisted to DynamoDB.
_webhook_dev_mode: bool = False

# In-memory storage for admin-editable system prompt additions.
# Appended to the core system prompt for all investigations (chat + webhooks).
_system_prompt_additions: str = ""


def _restore_app_settings_from_dynamodb() -> None:
    """Load persisted app settings from DynamoDB into memory."""
    global _webhook_dev_mode, _system_prompt_additions
    table_name = os.environ.get("HOLMES_DYNAMODB_TABLE", "")
    if not table_name:
        return
    try:
        from projects import get_app_settings_store  # noqa: PLC0415

        store = get_app_settings_store()
        _webhook_dev_mode = bool(store.get("webhook_dev_mode", False))
        _system_prompt_additions = str(store.get("system_prompt_additions", "") or "")
        logging.info(
            "Restored app settings from DynamoDB: webhook_dev_mode=%s, "
            "system_prompt_additions=%d chars",
            _webhook_dev_mode,
            len(_system_prompt_additions),
        )
    except Exception:
        logging.warning("Failed to restore app settings from DynamoDB", exc_info=True)


# ── Tool-count cap for Anthropic API ──────────────────────────────────────────
# Anthropic's API has an internal limit on the number of tool definitions per
# request (~256). With all integrations enabled (ADO=72, Atlassian=44,
# Salesforce=19, plus built-ins) we reach 299 tools, which triggers an opaque
# "internal error". This helper builds a scoped ToolExecutor that:
#   1. Always includes toolsets relevant to the investigation source
#   2. Always includes core toolsets (bash, runbook, internet, etc.)
#   3. Fills remaining capacity with other toolsets up to MAX_TOOLS_PER_CALL
MAX_TOOLS_PER_CALL = 200

# Map investigation source names to toolset name prefixes that should be
# prioritised for that source.
_SOURCE_TOOLSET_PRIORITY: dict[str, list[str]] = {
    "azure_devops": ["ado"],
    "ado": ["ado"],
    "salesforce": ["salesforce"],
    "atlassian": ["atlassian"],
    "jira": ["atlassian"],
    "confluence": ["atlassian"],
    "pagerduty": [],
    "datadog": ["datadog"],
    "grafana": ["grafana"],
    "kubernetes": ["kubernetes"],
    "aws": ["aws_api"],
    "dbdash": ["dbdash"],
    "dbadash": ["dbdash"],
    "sql_server": ["dbdash"],
}

# Toolsets that are always included regardless of source
_CORE_TOOLSET_PREFIXES = [
    "bash",
    "runbook",
    "internet",
    "connectivity_check",
]

# Heavy toolsets excluded from focused mode (when source is specified) to reduce prompt size.
# core_investigation adds ~14K chars of TodoWrite instructions — irrelevant for source-scoped queries.
_UNFOCUSED_ONLY_PREFIXES = [
    "core_investigation",
]


def _create_scoped_toolcalling_llm(config, source: str, model: str = None):
    """
    Build a ToolCallingLLM whose executor is capped at MAX_TOOLS_PER_CALL tools.

    Toolsets are selected in priority order:
      1. Source-specific toolsets (e.g. 'ado' for Azure DevOps investigations)
      2. Core toolsets (bash, runbook, internet, etc.)
      3. All remaining toolsets, added until the tool cap is reached
    """
    from holmes.core.tool_calling_llm import ToolCallingLLM
    from holmes.core.tools_utils.tool_executor import ToolExecutor

    # Ensure the global executor is built
    config.create_tool_executor(config.dal)
    all_toolsets = list(config._server_tool_executor.toolsets)

    # Normalise source key
    source_key = source.lower().replace(" ", "_").replace("-", "_")
    priority_prefixes = _SOURCE_TOOLSET_PRIORITY.get(source_key, [])

    def _toolset_tool_count(ts) -> int:
        return len(ts.tools) if ts.tools else 0

    # Bucket toolsets into groups (preserving order within each group)
    source_ts: list = []
    core_ts: list = []
    unfocused_ts: list = []  # heavy toolsets only included when no source specified
    other_ts: list = []

    for ts in all_toolsets:
        name = ts.name or ""
        if any(
            name == p or name.startswith(p + "/") or name.startswith(p + "_")
            for p in priority_prefixes
        ):
            source_ts.append(ts)
        elif any(
            name == p or name.startswith(p + "/") or name.startswith(p + "_")
            for p in _CORE_TOOLSET_PREFIXES
        ):
            core_ts.append(ts)
        elif any(
            name == p or name.startswith(p + "/") or name.startswith(p + "_")
            for p in _UNFOCUSED_ONLY_PREFIXES
        ):
            unfocused_ts.append(ts)
        else:
            other_ts.append(ts)

    selected: list = []
    total_tools = 0

    # When a specific source is provided, use focused mode: only source + core toolsets.
    # Excludes heavy toolsets (core_investigation ~14K instructions) and all other toolsets.
    # This reduces system prompt from ~53K to ~33K chars, avoiding AI gateway errors.
    # When no source is provided, include all toolsets up to the cap (original behavior).
    if priority_prefixes:
        toolsets_to_include = source_ts + core_ts
    else:
        toolsets_to_include = source_ts + core_ts + unfocused_ts + other_ts

    for ts in toolsets_to_include:
        count = _toolset_tool_count(ts)
        if total_tools + count > MAX_TOOLS_PER_CALL:
            logging.info(
                "Tool cap: skipping toolset '%s' (%d tools) — would exceed limit of %d (currently at %d)",
                ts.name,
                count,
                MAX_TOOLS_PER_CALL,
                total_tools,
            )
            continue
        selected.append(ts)
        total_tools += count

    logging.info(
        "Scoped tool executor for source='%s': %d toolsets, %d tools (cap=%d)",
        source,
        len(selected),
        total_tools,
        MAX_TOOLS_PER_CALL,
    )

    scoped_executor = ToolExecutor(selected)
    return ToolCallingLLM(
        scoped_executor,
        config.max_steps,
        config._get_llm(model),
        tool_results_dir=None,
    )


def mount_frontend(app: FastAPI, config=None) -> None:
    """Add auth endpoints, integrations API, and static file serving to the FastAPI app."""

    # Add Okta auth middleware
    if OKTA_ISSUER and OKTA_CLIENT_ID:
        app.add_middleware(OktaAuthMiddleware)
        logging.info("Okta auth middleware enabled")
    else:
        logging.warning(
            "OKTA_ISSUER or OKTA_CLIENT_ID not set - Okta auth is DISABLED. "
            "Set both environment variables to enable authentication."
        )

    # ── DynamoDB persistence: restore state on startup ────────────────────────
    _restore_llm_overrides_from_dynamodb(config)
    _restore_toolset_state_from_dynamodb(config)
    _restore_toolset_config_from_dynamodb(config)
    _restore_app_settings_from_dynamodb()

    @app.get("/api/auth/me")
    async def auth_me(request: Request):
        """Return the current user's profile and permissions."""
        perms: UserPermissions = request.state.permissions
        user = perms.user

        project_roles = {}
        for pid, pr in perms.project_roles.items():
            project_roles[pid] = pr.role

        return JSONResponse({
            "sub": user.sub,
            "email": user.email,
            "name": user.name or "",
            "globalRole": user.global_role,
            "projectRoles": project_roles,
            "status": user.status,
        })

    def _require_super_admin(request: Request):
        perms = request.state.permissions
        if perms.user.global_role != "super-admin":
            raise HTTPException(403, "Super-admin required")

    def _require_project_access(request: Request, project_id: str, min_role: str = "read-only"):
        perms = request.state.permissions
        if perms.user.global_role == "super-admin":
            return
        project_role = perms.project_roles.get(project_id)
        if not project_role:
            raise HTTPException(403, "No access to this project")
        if min_role == "project-admin" and project_role.role != "project-admin":
            raise HTTPException(403, "Project-admin required")

    def _get_accessible_project_ids(request: Request) -> list[str] | None:
        """Return list of project IDs user can access, or None for super-admin (all)."""
        perms = request.state.permissions
        if perms.user.global_role == "super-admin":
            return None
        return list(perms.project_roles.keys())

    def _ensure_tool_executor():
        """Lazily initialize the tool executor if not yet created."""
        if config is None:
            return False
        if config._server_tool_executor is None:
            try:
                config.create_tool_executor(config.dal)
            except Exception:
                logging.warning("Failed to initialize tool executor")
                return False
        return True

    def _get_config_schema(toolset) -> list:
        """Extract config field schema from a toolset's config class."""
        schema_fields = []
        seen_names = set()

        # Try to get schema from the toolset's config_classes
        config_classes = getattr(toolset.__class__, "config_classes", [])

        # If there are multiple config classes, prefer the first one (primary config)
        # and only add unique fields from subsequent classes
        for config_cls in config_classes[:1]:  # Only use the primary config class
            if not hasattr(config_cls, "model_fields"):
                continue
            for field_name, field_info in config_cls.model_fields.items():
                if field_name.startswith("_") or field_name in seen_names:
                    continue
                seen_names.add(field_name)

                field_type = "str"
                annotation = field_info.annotation
                if annotation is not None:
                    type_name = getattr(annotation, "__name__", str(annotation))
                    if "bool" in str(type_name).lower():
                        field_type = "bool"
                    elif "int" in str(type_name).lower():
                        field_type = "int"
                    elif "float" in str(type_name).lower():
                        field_type = "float"
                    elif "dict" in str(type_name).lower():
                        field_type = "dict"
                    elif "list" in str(type_name).lower():
                        field_type = "list"

                required = field_info.is_required()
                default = field_info.default if field_info.default is not None else None
                # Skip PydanticUndefined
                if default is not None and "PydanticUndefined" in str(type(default)):
                    default = None

                description = field_info.description or ""
                sensitive = any(
                    kw in field_name.lower()
                    for kw in ("key", "password", "secret", "token")
                )

                schema_fields.append(
                    {
                        "name": field_name,
                        "type": field_type,
                        "required": required,
                        "default": default
                        if isinstance(default, (str, int, float, bool, type(None)))
                        else str(default)
                        if default is not None
                        else None,
                        "description": description,
                        "sensitive": sensitive,
                    }
                )

        return schema_fields

    def _serialize_toolset(toolset) -> dict:
        """Serialize a toolset to a JSON-safe dict."""
        # Extract config fields for the UI
        config_fields = {}
        if toolset.config and hasattr(toolset.config, "model_dump"):
            try:
                config_fields = toolset.config.model_dump(exclude_none=True)
                # Remove internal/private fields
                for key in list(config_fields.keys()):
                    if key.startswith("_"):
                        del config_fields[key]
                # Convert non-serializable values to strings
                for key, val in config_fields.items():
                    if not isinstance(
                        val, (str, int, float, bool, list, dict, type(None))
                    ):
                        config_fields[key] = str(val)
            except Exception:
                config_fields = {}
        elif isinstance(toolset.config, dict):
            config_fields = toolset.config

        return {
            "name": toolset.name,
            "description": toolset.description or "",
            "status": toolset.status.value
            if hasattr(toolset.status, "value")
            else str(toolset.status),
            "type": toolset.type.value
            if toolset.type and hasattr(toolset.type, "value")
            else "built-in",
            "error": toolset.error,
            "icon_url": getattr(toolset, "icon_url", None),
            "docs_url": getattr(toolset, "docs_url", None),
            "tool_count": len(toolset.tools) if toolset.tools else 0,
            "enabled": toolset.enabled,
            "config": config_fields,
            "config_schema": _get_config_schema(toolset),
        }

    @app.get("/api/integrations")
    async def get_integrations():
        """Return all toolsets with their status for the Integrations UI page."""
        if not _ensure_tool_executor():
            return JSONResponse({"integrations": []})

        integrations = [
            _serialize_toolset(t) for t in config._server_tool_executor.toolsets
        ]
        return JSONResponse({"integrations": integrations})

    @app.put("/api/integrations/{name:path}/toggle")
    async def toggle_integration(name: str, request: Request):
        """Enable or disable a toolset and hot-reload."""
        if not _ensure_tool_executor():
            raise HTTPException(status_code=503, detail="Tool executor not available")

        body = await request.json()
        enabled = body.get("enabled")
        if enabled is None:
            raise HTTPException(status_code=400, detail="'enabled' field required")

        # Update the toolsets config dict (this is what ToolsetManager reads)
        if config.toolsets is None:
            config.toolsets = {}
        if name not in config.toolsets:
            config.toolsets[name] = {}
        config.toolsets[name]["enabled"] = enabled

        # Persist to DynamoDB so the state survives pod restarts
        try:
            from projects import get_toolset_state_store  # noqa: PLC0415

            get_toolset_state_store().save(name, enabled)
        except Exception:
            logging.warning(
                "Failed to persist toolset state to DynamoDB", exc_info=True
            )

        # Hot-reload: force re-creation of tool executor with updated config
        try:
            config._toolset_manager = None
            config._server_tool_executor = None
            config.create_tool_executor(config.dal)
        except Exception as e:
            logging.error(f"Failed to reload toolsets after toggle: {e}")
            raise HTTPException(status_code=500, detail=f"Reload failed: {str(e)}")

        # Find the updated toolset
        for t in config._server_tool_executor.toolsets:
            if t.name == name:
                return JSONResponse(_serialize_toolset(t))

        return JSONResponse({"ok": True, "name": name, "enabled": enabled})

    @app.put("/api/integrations/{name:path}/config")
    async def update_integration_config(name: str, request: Request):
        """Update a toolset's configuration and hot-reload."""
        if not _ensure_tool_executor():
            raise HTTPException(status_code=503, detail="Tool executor not available")

        body = await request.json()
        new_config = body.get("config", {})
        enabled = body.get("enabled")

        if config.toolsets is None:
            config.toolsets = {}
        if name not in config.toolsets:
            config.toolsets[name] = {}

        # Merge config fields
        if "config" not in config.toolsets[name]:
            config.toolsets[name]["config"] = {}
        config.toolsets[name]["config"].update(new_config)

        # Persist config overrides to DynamoDB (exclude {{ env.* }} secret refs)
        if new_config:
            try:
                from projects import get_toolset_config_store  # noqa: PLC0415

                # Only persist non-secret config values
                persistable = {
                    k: v
                    for k, v in config.toolsets[name]["config"].items()
                    if not (isinstance(v, str) and "{{ env." in v)
                }
                if persistable:
                    get_toolset_config_store().save(name, persistable)
            except Exception:
                logging.warning(
                    "Failed to persist toolset config to DynamoDB", exc_info=True
                )

        if enabled is not None:
            config.toolsets[name]["enabled"] = enabled
            # Persist enabled state to DynamoDB so it survives pod restarts
            try:
                from projects import get_toolset_state_store  # noqa: PLC0415

                get_toolset_state_store().save(name, enabled)
            except Exception:
                logging.warning(
                    "Failed to persist toolset state to DynamoDB", exc_info=True
                )

        # Hot-reload
        try:
            config._toolset_manager = None
            config._server_tool_executor = None
            config.create_tool_executor(config.dal)
        except Exception as e:
            logging.error(f"Failed to reload toolsets after config update: {e}")
            raise HTTPException(status_code=500, detail=f"Reload failed: {str(e)}")

        # Find the updated toolset
        for t in config._server_tool_executor.toolsets:
            if t.name == name:
                return JSONResponse(_serialize_toolset(t))

        return JSONResponse({"ok": True, "name": name})

    @app.get("/api/aws/accounts")
    async def get_aws_accounts(project_id: str = ""):
        """Return configured AWS accounts for the multi-account MCP setup.

        When *project_id* is provided, only accounts that belong to ``aws_api``
        instances resolved for that project are returned.
        """
        import json as _json

        raw = os.environ.get("AWS_MCP_ACCOUNTS", "[]")
        try:
            accounts = _json.loads(raw)
        except Exception:
            accounts = []

        # Filter to project-scoped accounts when a project is selected
        if project_id and accounts:
            try:
                from projects import (  # noqa: PLC0415
                    get_instances_store,
                    get_store,
                    resolve_instances_for_project,
                )

                project = get_store().get(project_id)
                if project:
                    all_instances = get_instances_store().list()
                    resolved = resolve_instances_for_project(project, all_instances)
                    # Collect allowed account profile names from aws_api instances
                    allowed_names: set[str] = set()
                    for inst in resolved:
                        if inst.type == "aws_api" and inst.aws_accounts:
                            allowed_names.update(inst.aws_accounts)
                    if allowed_names:
                        accounts = [
                            a for a in accounts if a.get("name") in allowed_names
                        ]
            except Exception:
                logging.warning(
                    "Failed to filter AWS accounts for project %s",
                    project_id,
                    exc_info=True,
                )

        return JSONResponse(
            {
                "accounts": accounts,
                "irsa_role": os.environ.get("AWS_MCP_IRSA_ROLE", ""),
            }
        )

    @app.get("/api/webhooks")
    async def get_webhooks():
        """Return webhook configuration status (which env vars are set) and settings."""
        from projects import get_webhook_settings_store  # noqa: PLC0415

        wh_settings = get_webhook_settings_store().load_all()

        def _wb(webhook_id: str) -> bool:
            return wh_settings.get(webhook_id, {}).get("write_back_enabled", True)

        return JSONResponse(
            {
                "webhooks": [
                    {
                        "id": "pagerduty",
                        "name": "PagerDuty",
                        "url": "/api/webhook/pagerduty",
                        "auth_type": "HMAC-SHA256",
                        "trigger": "incident.triggered",
                        "configured": bool(
                            os.environ.get("PAGERDUTY_WEBHOOK_SECRET")
                            or os.environ.get("PAGERDUTY_API_KEY")
                        ),
                        "write_back_enabled": _wb("pagerduty"),
                        "write_back_capable": bool(
                            os.environ.get("PAGERDUTY_API_KEY")
                            and os.environ.get("PAGERDUTY_USER_EMAIL")
                        ),
                        "vars": {
                            "PAGERDUTY_WEBHOOK_SECRET": bool(
                                os.environ.get("PAGERDUTY_WEBHOOK_SECRET")
                            ),
                            "PAGERDUTY_API_KEY": bool(
                                os.environ.get("PAGERDUTY_API_KEY")
                            ),
                            "PAGERDUTY_USER_EMAIL": bool(
                                os.environ.get("PAGERDUTY_USER_EMAIL")
                            ),
                        },
                    },
                    {
                        "id": "ado",
                        "name": "Azure DevOps",
                        "url": "/api/webhook/ado",
                        "auth_type": "Basic Auth",
                        "trigger": "workitem.created",
                        "configured": bool(
                            os.environ.get("ADO_WEBHOOK_USERNAME")
                            or os.environ.get("ADO_PAT")
                        ),
                        "write_back_enabled": _wb("ado"),
                        "write_back_capable": bool(
                            os.environ.get("ADO_PAT")
                            and os.environ.get("ADO_ORGANIZATION")
                        ),
                        "vars": {
                            "ADO_WEBHOOK_USERNAME": bool(
                                os.environ.get("ADO_WEBHOOK_USERNAME")
                            ),
                            "ADO_WEBHOOK_PASSWORD": bool(
                                os.environ.get("ADO_WEBHOOK_PASSWORD")
                            ),
                            "ADO_PAT": bool(os.environ.get("ADO_PAT")),
                            "ADO_ORGANIZATION": bool(
                                os.environ.get("ADO_ORGANIZATION")
                            ),
                        },
                    },
                    {
                        "id": "salesforce",
                        "name": "Salesforce",
                        "url": "/api/webhook/salesforce",
                        "auth_type": "Token",
                        "trigger": "Case created",
                        "configured": bool(
                            os.environ.get("SALESFORCE_WEBHOOK_TOKEN")
                            or os.environ.get("SALESFORCE_INSTANCE_URL")
                        ),
                        "write_back_enabled": _wb("salesforce"),
                        "write_back_capable": bool(
                            os.environ.get("SALESFORCE_INSTANCE_URL")
                            and os.environ.get("SALESFORCE_ACCESS_TOKEN")
                        ),
                        "vars": {
                            "SALESFORCE_WEBHOOK_TOKEN": bool(
                                os.environ.get("SALESFORCE_WEBHOOK_TOKEN")
                            ),
                            "SALESFORCE_INSTANCE_URL": bool(
                                os.environ.get("SALESFORCE_INSTANCE_URL")
                            ),
                            "SALESFORCE_ACCESS_TOKEN": bool(
                                os.environ.get("SALESFORCE_ACCESS_TOKEN")
                            ),
                        },
                    },
                ],
                "webhook_dev_mode": _webhook_dev_mode,
            }
        )

    @app.put("/api/webhooks/{webhook_id}/settings")
    async def update_webhook_settings(webhook_id: str, request: Request):
        """Update per-webhook settings (e.g. write_back_enabled toggle)."""
        valid_ids = {"pagerduty", "ado", "salesforce"}
        if webhook_id not in valid_ids:
            raise HTTPException(
                status_code=404, detail=f"Unknown webhook id: {webhook_id}"
            )
        body = await request.json()
        write_back_enabled = bool(body.get("write_back_enabled", True))
        from projects import get_webhook_settings_store  # noqa: PLC0415

        get_webhook_settings_store().save(webhook_id, write_back_enabled)
        logging.info(
            "Webhook settings updated: %s write_back_enabled=%s",
            webhook_id,
            write_back_enabled,
        )
        return JSONResponse(
            {
                "ok": True,
                "webhook_id": webhook_id,
                "write_back_enabled": write_back_enabled,
            }
        )

    @app.get("/api/app-settings")
    async def get_app_settings():
        """Return global application settings."""
        return JSONResponse(
            {
                "webhook_dev_mode": _webhook_dev_mode,
                "system_prompt_additions": _system_prompt_additions,
            }
        )

    @app.put("/api/app-settings")
    async def update_app_settings(request: Request):
        """Update global application settings and persist to DynamoDB."""
        _require_super_admin(request)
        global _webhook_dev_mode, _system_prompt_additions
        body = await request.json()

        from projects import get_app_settings_store  # noqa: PLC0415

        store = get_app_settings_store()

        if "webhook_dev_mode" in body:
            new_value = bool(body["webhook_dev_mode"])
            store.set("webhook_dev_mode", new_value)
            _webhook_dev_mode = new_value
            logging.info("App settings updated: webhook_dev_mode=%s", new_value)

        if "system_prompt_additions" in body:
            new_value = str(body["system_prompt_additions"] or "")
            store.set("system_prompt_additions", new_value)
            _system_prompt_additions = new_value
            logging.info(
                "App settings updated: system_prompt_additions=%d chars",
                len(new_value),
            )

        return JSONResponse(
            {
                "webhook_dev_mode": _webhook_dev_mode,
                "system_prompt_additions": _system_prompt_additions,
            }
        )

    # ── LLM Instructions helpers ──────────────────────────────────────────────

    def _is_mcp_toolset(name: str) -> bool:
        """Return True if the named toolset is an MCP server."""
        if config is None:
            return False
        if config.mcp_servers and name in config.mcp_servers:
            return True
        if config._server_tool_executor:
            for t in config._server_tool_executor.toolsets:
                if t.name == name:
                    ts_type = (
                        t.type.value if t.type and hasattr(t.type, "value") else ""
                    )
                    return ts_type == "mcp"
        return False

    def _is_instructions_overridden(name: str) -> bool:
        """Return True if a user override exists for this toolset's llm_instructions."""
        if config is None:
            return False
        if config.mcp_servers and name in config.mcp_servers:
            return "llm_instructions" in config.mcp_servers[name]
        if config.toolsets and name in config.toolsets:
            return "llm_instructions" in config.toolsets[name]
        return False

    # ── LLM Instructions endpoints ────────────────────────────────────────────

    @app.get("/api/llm-instructions")
    async def get_llm_instructions():
        """Return current llm_instructions for every loaded toolset."""
        if not _ensure_tool_executor():
            return JSONResponse({"integrations": []})
        result = []
        for toolset in config._server_tool_executor.toolsets:
            result.append(
                {
                    "name": toolset.name,
                    "description": toolset.description or "",
                    "type": toolset.type.value
                    if toolset.type and hasattr(toolset.type, "value")
                    else "built-in",
                    "icon_url": getattr(toolset, "icon_url", None),
                    "enabled": toolset.enabled,
                    "instructions": toolset.llm_instructions or "",
                    "has_default": bool(toolset.llm_instructions),
                    "is_overridden": _is_instructions_overridden(toolset.name),
                }
            )
        return JSONResponse({"integrations": result})

    @app.put("/api/llm-instructions/{name:path}")
    async def update_llm_instructions(name: str, request: Request):
        """Override llm_instructions for one toolset and hot-reload."""
        if not _ensure_tool_executor():
            raise HTTPException(status_code=503, detail="Tool executor not available")
        body = await request.json()
        instructions: str = body.get("instructions", "")
        if _is_mcp_toolset(name):
            if config.mcp_servers is None:
                config.mcp_servers = {}
            config.mcp_servers.setdefault(name, {})["llm_instructions"] = instructions
        else:
            if config.toolsets is None:
                config.toolsets = {}
            config.toolsets.setdefault(name, {})["llm_instructions"] = instructions
        # Persist to DynamoDB so the override survives pod restarts
        try:
            from projects import get_llm_store  # noqa: PLC0415

            get_llm_store().save(name, instructions)
        except Exception:
            logging.warning("Failed to persist LLM override to DynamoDB", exc_info=True)
        try:
            config._toolset_manager = None
            config._server_tool_executor = None
            config.create_tool_executor(config.dal)
        except Exception as e:
            logging.error(
                f"Failed to reload toolsets after llm_instructions update: {e}"
            )
            raise HTTPException(status_code=500, detail=f"Reload failed: {str(e)}")
        for toolset in config._server_tool_executor.toolsets:
            if toolset.name == name:
                return JSONResponse(
                    {
                        "name": toolset.name,
                        "instructions": toolset.llm_instructions or "",
                        "is_overridden": True,
                    }
                )
        return JSONResponse({"ok": True, "name": name})

    @app.delete("/api/llm-instructions/{name:path}")
    async def reset_llm_instructions(name: str):
        """Remove a user override, restoring the toolset's default llm_instructions."""
        if not _ensure_tool_executor():
            raise HTTPException(status_code=503, detail="Tool executor not available")
        if _is_mcp_toolset(name):
            if config.mcp_servers and name in config.mcp_servers:
                config.mcp_servers[name].pop("llm_instructions", None)
        else:
            if config.toolsets and name in config.toolsets:
                config.toolsets[name].pop("llm_instructions", None)
        # Remove from DynamoDB so the override doesn't come back after pod restart
        try:
            from projects import get_llm_store  # noqa: PLC0415

            get_llm_store().delete(name)
        except Exception:
            logging.warning(
                "Failed to delete LLM override from DynamoDB", exc_info=True
            )
        try:
            config._toolset_manager = None
            config._server_tool_executor = None
            config.create_tool_executor(config.dal)
        except Exception as e:
            logging.error(
                f"Failed to reload toolsets after llm_instructions reset: {e}"
            )
            raise HTTPException(status_code=500, detail=f"Reload failed: {str(e)}")
        for toolset in config._server_tool_executor.toolsets:
            if toolset.name == name:
                return JSONResponse(
                    {
                        "name": toolset.name,
                        "instructions": toolset.llm_instructions or "",
                        "is_overridden": False,
                    }
                )
        return JSONResponse({"ok": True, "name": name})

    # ── Projects endpoints ────────────────────────────────────────────────────

    @app.get("/api/projects")
    async def list_projects(request: Request):
        """Return all projects."""
        try:
            from projects import get_store  # noqa: PLC0415

            all_projects = get_store().list()
            accessible = _get_accessible_project_ids(request)
            if accessible is not None:
                all_projects = [p for p in all_projects if p.id in accessible]
            return JSONResponse(
                {"projects": [p.model_dump() for p in all_projects]}
            )
        except Exception as e:
            logging.error("Failed to list projects: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/projects")
    async def create_project(request: Request):
        """Create a new project."""
        _require_super_admin(request)
        try:
            from projects import get_store  # noqa: PLC0415

            body = await request.json()
            p = get_store().create(
                name=body["name"],
                description=body.get("description", ""),
                tag_filter=body.get("tag_filter"),
                webhook_routing=body.get("webhook_routing"),
            )
            return JSONResponse(p.model_dump(), status_code=201)
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"Missing required field: {e}")
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            logging.error("Failed to create project: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str, request: Request):
        """Return a single project by ID."""
        _require_project_access(request, project_id)
        try:
            from projects import get_store  # noqa: PLC0415

            p = get_store().get(project_id)
            if not p:
                raise HTTPException(status_code=404, detail="Project not found")
            return JSONResponse(p.model_dump())
        except HTTPException:
            raise
        except Exception as e:
            logging.error("Failed to get project %s: %s", project_id, e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/projects/{project_id}")
    async def update_project(project_id: str, request: Request):
        """Update an existing project."""
        _require_project_access(request, project_id, min_role="project-admin")
        try:
            from projects import get_store  # noqa: PLC0415

            body = await request.json()
            p = get_store().update(project_id, **body)
            if not p:
                raise HTTPException(status_code=404, detail="Project not found")
            return JSONResponse(p.model_dump())
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            logging.error("Failed to update project %s: %s", project_id, e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str, request: Request):
        """Delete a project."""
        _require_super_admin(request)
        try:
            from projects import get_store  # noqa: PLC0415
            from rbac import delete_project_roles  # noqa: PLC0415

            if not get_store().delete(project_id):
                raise HTTPException(status_code=404, detail="Project not found")
            try:
                delete_project_roles(project_id)
            except Exception:
                logging.warning(
                    "Failed to clean up project roles for project %s", project_id, exc_info=True
                )
            return JSONResponse({"ok": True})
        except HTTPException:
            raise
        except Exception as e:
            logging.error("Failed to delete project %s: %s", project_id, e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/projects/{project_id}/preview")
    async def preview_project(project_id: str):
        """Return the instances that would be resolved for a project given its tag filter."""
        try:
            from projects import (  # noqa: PLC0415
                get_instances_store,
                get_store,
                resolve_instances_for_project,
            )

            p = get_store().get(project_id)
            if not p:
                raise HTTPException(status_code=404, detail="Project not found")
            all_instances = get_instances_store().list()
            resolved = resolve_instances_for_project(p, all_instances)
            return JSONResponse(
                {
                    "project_id": project_id,
                    "tag_filter": p.tag_filter.model_dump() if p.tag_filter else None,
                    "resolved_instances": [i.model_dump() for i in resolved],
                    "total_instances": len(all_instances),
                    "resolved_count": len(resolved),
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            logging.error("Failed to preview project %s: %s", project_id, e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/projects/{project_id}/webhook-settings")
    async def get_project_webhook_settings(project_id: str):
        """Return resolved write-back settings for each webhook in a project.

        For each webhook, returns the effective ``write_back_enabled`` value
        (project override if set, otherwise global default) and whether the
        value is a project-level override.
        """
        try:
            from projects import get_store, get_webhook_settings_store  # noqa: PLC0415

            p = get_store().get(project_id)
            if not p:
                raise HTTPException(status_code=404, detail="Project not found")

            store = get_webhook_settings_store()
            webhook_ids = ["pagerduty", "ado", "salesforce"]
            result: dict[str, dict] = {}
            for wh_id in webhook_ids:
                global_val = store.get(wh_id).get("write_back_enabled", True)
                override = (
                    p.webhook_write_back.get(wh_id) if p.webhook_write_back else None
                )
                result[wh_id] = {
                    "write_back_enabled": override
                    if override is not None
                    else global_val,
                    "is_override": override is not None,
                    "global_default": global_val,
                }
            return JSONResponse(result)
        except HTTPException:
            raise
        except Exception as e:
            logging.error(
                "Failed to get webhook settings for project %s: %s", project_id, e
            )
            raise HTTPException(status_code=500, detail=str(e))

    # ── Instances endpoints ────────────────────────────────────────────────────

    @app.get("/api/instances")
    async def list_instances():
        """Return all instances."""
        try:
            from projects import get_instances_store  # noqa: PLC0415

            return JSONResponse(
                {"instances": [i.model_dump() for i in get_instances_store().list()]}
            )
        except Exception as e:
            logging.error("Failed to list instances: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/instances")
    async def create_instance(request: Request):
        """Create a new instance."""
        perms = request.state.permissions
        if perms.user.global_role != "super-admin":
            has_admin = any(pr.role == "project-admin" for pr in perms.project_roles.values())
            if not has_admin:
                raise HTTPException(403, "Project-admin required to create instances")
        try:
            from projects import get_instances_store  # noqa: PLC0415

            body = await request.json()
            inst = get_instances_store().create(
                type=body["type"],
                name=body["name"],
                tags=body.get("tags", {}),
                secret_arn=body.get("secret_arn"),
                config=body.get("config"),
                mcp_url=body.get("mcp_url"),
                aws_accounts=body.get("aws_accounts"),
                aws_regions=body.get("aws_regions"),
                aws_account_name=body.get("aws_account_name"),
                aws_account_id=body.get("aws_account_id"),
                aws_role_arn=body.get("aws_role_arn"),
            )
            return JSONResponse(inst.model_dump(), status_code=201)
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"Missing required field: {e}")
        except Exception as e:
            logging.error("Failed to create instance: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/instances/{instance_id}")
    async def get_instance(instance_id: str):
        """Return a single instance by ID."""
        try:
            from projects import get_instances_store  # noqa: PLC0415

            inst = get_instances_store().get(instance_id)
            if not inst:
                raise HTTPException(status_code=404, detail="Instance not found")
            return JSONResponse(inst.model_dump())
        except HTTPException:
            raise
        except Exception as e:
            logging.error("Failed to get instance %s: %s", instance_id, e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/instances/{instance_id}")
    async def update_instance(instance_id: str, request: Request):
        """Update an existing instance."""
        perms = request.state.permissions
        if perms.user.global_role != "super-admin":
            has_admin = any(pr.role == "project-admin" for pr in perms.project_roles.values())
            if not has_admin:
                raise HTTPException(403, "Project-admin required to create instances")
        try:
            from projects import get_instances_store  # noqa: PLC0415

            body = await request.json()
            inst = get_instances_store().update(instance_id, **body)
            if not inst:
                raise HTTPException(status_code=404, detail="Instance not found")
            return JSONResponse(inst.model_dump())
        except HTTPException:
            raise
        except Exception as e:
            logging.error("Failed to update instance %s: %s", instance_id, e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/instances/{instance_id}")
    async def delete_instance(instance_id: str, request: Request):
        """Delete an instance."""
        perms = request.state.permissions
        if perms.user.global_role != "super-admin":
            has_admin = any(pr.role == "project-admin" for pr in perms.project_roles.values())
            if not has_admin:
                raise HTTPException(403, "Project-admin required to create instances")
        try:
            from projects import get_instances_store  # noqa: PLC0415

            if not get_instances_store().delete(instance_id):
                raise HTTPException(status_code=404, detail="Instance not found")
            return JSONResponse({"ok": True})
        except HTTPException:
            raise
        except Exception as e:
            logging.error("Failed to delete instance %s: %s", instance_id, e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/instances/{instance_id}/test-connection")
    async def test_instance_connection(instance_id: str):
        """Test AWS cross-account connection by attempting STS AssumeRole."""
        try:
            import boto3 as _boto3  # noqa: PLC0415
            from projects import get_instances_store  # noqa: PLC0415

            store = get_instances_store()
            inst = store.get(instance_id)
            if not inst:
                raise HTTPException(status_code=404, detail="Instance not found")
            if inst.type != "aws_api" or not inst.aws_role_arn:
                raise HTTPException(
                    status_code=400,
                    detail="Instance is not an AWS type or has no Role ARN",
                )

            # Attempt AssumeRole to validate the cross-account trust
            sts = _boto3.client(
                "sts", region_name=os.environ.get("AWS_REGION", "us-east-1")
            )
            try:
                resp = sts.assume_role(
                    RoleArn=inst.aws_role_arn,
                    RoleSessionName="holmesgpt-connection-test",
                    DurationSeconds=900,
                )
                caller = resp["AssumedRoleUser"]["Arn"]
                # Update instance with success status
                store.update(
                    instance_id,
                    aws_connection_status="success",
                    aws_connection_error=None,
                )
                return JSONResponse(
                    {"ok": True, "status": "success", "assumed_role": caller}
                )
            except Exception as assume_err:
                error_msg = str(assume_err)
                store.update(
                    instance_id,
                    aws_connection_status="error",
                    aws_connection_error=error_msg,
                )
                return JSONResponse(
                    {"ok": False, "status": "error", "error": error_msg}
                )
        except HTTPException:
            raise
        except Exception as e:
            logging.error(
                "Failed to test connection for instance %s: %s", instance_id, e
            )
            raise HTTPException(status_code=500, detail=str(e))

    # ── Investigation history endpoints ────────────────────────────────────────

    @app.get("/api/investigations")
    async def list_investigations(
        limit: int = 50,
        source: str = None,
        project_id: str = None,
        start_date: str = None,
        end_date: str = None,
    ):
        """List past investigations, newest first.

        tool_output is stripped from each tool call record to keep the response
        payload small (full tool outputs can be hundreds of KB per investigation).
        The detail endpoint GET /api/investigations/{id} returns the full record.
        """
        try:
            from projects import get_investigation_store  # noqa: PLC0415

            investigations = get_investigation_store().list(
                limit=limit,
                source=source or None,
                project_id=project_id or None,
                start_date=start_date or None,
                end_date=end_date or None,
            )
            rows = []
            for inv in investigations:
                d = inv.model_dump()
                # Strip tool_output to keep list payload small; detail endpoint has full data
                for tc in d.get("tool_calls", []):
                    tc["tool_output"] = ""
                rows.append(d)
            return JSONResponse(rows)
        except Exception as e:
            logging.error("Failed to list investigations: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/investigations/similar")
    async def similar_investigations(
        q: str = "",
        project_id: str = "",
        limit: int = 5,
    ):
        """Find past investigations similar to the given query text."""
        if not q.strip():
            return JSONResponse([])
        try:
            from projects import get_investigation_store  # noqa: PLC0415

            results = get_investigation_store().search_similar(
                query=q,
                project_id=project_id or None,
                limit=limit,
            )
            return JSONResponse(results)
        except Exception as e:
            logging.error("Failed to search similar investigations: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/investigations/{investigation_id}")
    async def get_investigation(investigation_id: str):
        """Get a single investigation by ID."""
        try:
            from projects import get_investigation_store  # noqa: PLC0415

            inv = get_investigation_store().get(investigation_id)
            if not inv:
                raise HTTPException(status_code=404, detail="Investigation not found")
            return JSONResponse(inv.model_dump())
        except HTTPException:
            raise
        except Exception as e:
            logging.error("Failed to get investigation %s: %s", investigation_id, e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/investigations/{investigation_id}")
    async def delete_investigation(investigation_id: str):
        """Delete an investigation record."""
        try:
            from projects import get_investigation_store  # noqa: PLC0415

            if not get_investigation_store().delete(investigation_id):
                raise HTTPException(status_code=404, detail="Investigation not found")
            return JSONResponse({"ok": True})
        except HTTPException:
            raise
        except Exception as e:
            logging.error("Failed to delete investigation %s: %s", investigation_id, e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/investigations/{investigation_id}/feedback")
    async def update_investigation_feedback(investigation_id: str, request: Request):
        """Update feedback and optional resolution summary on an investigation.

        Body: {"feedback": "helpful"|"not_helpful", "resolution_summary": "..."}
        Only investigations marked "helpful" with a resolution_summary will be
        injected into future LLM contexts.
        """
        try:
            from projects import get_investigation_store  # noqa: PLC0415

            body = await request.json()
            feedback = body.get("feedback")
            if feedback not in ("helpful", "not_helpful"):
                raise HTTPException(
                    status_code=400,
                    detail='feedback must be "helpful" or "not_helpful"',
                )
            resolution_summary = body.get("resolution_summary")
            inv = get_investigation_store().update_feedback(
                investigation_id, feedback, resolution_summary
            )
            if not inv:
                raise HTTPException(status_code=404, detail="Investigation not found")
            return JSONResponse(
                {
                    "id": inv.id,
                    "feedback": inv.feedback,
                    "resolution_summary": inv.resolution_summary,
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            logging.error(
                "Failed to update feedback for investigation %s: %s",
                investigation_id,
                e,
            )
            raise HTTPException(status_code=500, detail=str(e))

    # ── Manual investigate endpoint ───────────────────────────────────────────

    @app.post("/api/investigate")
    async def manual_investigate(request: Request):
        """
        Run a Holmes investigation from the UI Investigate page.
        Accepts: { source, title, description, subject, context, include_tool_calls, include_tool_call_results }

        Returns an SSE stream to keep the ALB connection alive during long investigations
        (ADO/Salesforce searches can exceed the 300s ALB idle timeout).

        SSE protocol:
          - ': keep-alive\\n\\n'  — heartbeat comment every 25s (resets ALB idle timer)
          - 'data: <json>\\n\\n'  — final result (same shape as the old JSON response)
          - 'data: {"error": "..."}\\n\\n' — on failure
        """
        import uuid as _uuid
        from datetime import datetime, timezone

        body = await request.json()
        source = body.get("source", "Manual")
        title = body.get("title", "")
        description = body.get("description", "")
        context = body.get("context", {})
        include_tool_calls = body.get("include_tool_calls", True)
        include_tool_call_results = body.get("include_tool_call_results", True)
        project_id = body.get("project_id", "")

        if not title or not description:
            raise HTTPException(
                status_code=400, detail="title and description are required"
            )

        if config is None:
            raise HTTPException(status_code=503, detail="Holmes config not available")

        # Resolve project if project_id provided
        resolved_project = None
        if project_id:
            try:
                import sys as _sys_proj

                _frontend_dir_proj = os.path.join(os.path.dirname(__file__))
                if _frontend_dir_proj not in _sys_proj.path:
                    _sys_proj.path.insert(0, _frontend_dir_proj)
                from projects import get_store as _get_store  # noqa: PLC0415

                resolved_project = _get_store().get(project_id)
            except Exception as e:
                logging.warning("Failed to resolve project %s: %s", project_id, e)

        investigation_id = _uuid.uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()

        # Build a rich cross-system investigation prompt.
        # Explicitly instruct Holmes to use ALL available integrations so it
        # doesn't limit itself to the source system named in the title.
        context_lines = "\n".join(f"  {k}: {v}" for k, v in context.items() if v)
        question = (
            f"## Investigation Request\n\n"
            f"**Source:** {source}\n"
            f"**Title:** {title}\n\n"
            f"**Description:**\n{description}\n"
        )
        if context_lines:
            question += f"\n**Additional Context:**\n{context_lines}\n"

        question += (
            "\n## Instructions\n\n"
            "Please conduct a thorough cross-system investigation using ALL available integrations and tools. "
            "Do not limit the investigation to the source system mentioned above. Work through each system below:\n\n"
            "1. **Salesforce** — look up the case, any linked change requests, customer records, and case history\n"
            "2. **Azure DevOps (ADO)** — search for related work items AND search code repos for relevant changes:\n"
            "   - Use `search_workitem` to find linked bugs, features, or SWAT cases\n"
            "   - Use `search_code` to find code related to the issue (search by feature name, error message, or component)\n"
            "   - Use `repo_search_commits` or `repo_list_pull_requests_by_repo` to find recent changes in affected areas\n"
            "   - Use `pipelines_get_builds` to check if recent deployments may have introduced the issue\n"
            "3. **Datadog** — check multiple signal types, not just logs:\n"
            "   - Use `fetch_datadog_logs` to search for errors or warnings\n"
            "   - Use `list_active_datadog_metrics` and `query_datadog_metrics` to check for anomalies\n"
            "   - Use `fetch_datadog_spans` to look for APM traces showing slow or failing requests\n"
            "   - Use `datadog_api_get` to check monitors or dashboards if logs/metrics are empty\n"
            "4. **Confluence** — search for runbooks, known issues, architecture docs, or past incident reports\n"
            "5. **Grafana** — check dashboards and alerts for any correlated infrastructure signals\n"
            "6. **Any other available tools** — use all tools at your disposal\n\n"
            "Important: if a tool returns empty results, try alternative queries or related tools before moving on. "
            "Consolidate findings from all systems into a single comprehensive analysis. "
            "Identify root causes, correlations across systems, and provide clear recommended actions."
        )

        # ── Inject similar past investigations into LLM context ───────────
        try:
            from projects import (  # noqa: PLC0415
                get_investigation_store as _get_inv_store,
            )

            similar = _get_inv_store().search_similar(
                query=f"{title} {description}",
                project_id=project_id or None,
                limit=3,
                min_score=0.3,
            )
            # Only inject user-approved investigations with resolution summaries
            approved = [
                s
                for s in similar
                if s.get("feedback") == "helpful" and s.get("resolution_summary")
            ]
            if approved:
                question += (
                    "\n\n## Similar Past Investigations (verified resolutions)\n\n"
                )
                question += (
                    "The following past investigations were marked as helpful by the team. "
                    "Consider this context but verify independently with current data.\n\n"
                )
                for i, s in enumerate(approved, 1):
                    question += (
                        f"### Past Investigation {i} (match: {s['score']:.0%}, source: {s['source']})\n"
                        f"**Question:** {s['question']}\n"
                        f"**Resolution:** {s['resolution_summary']}\n"
                        f"**Tools used:** {', '.join(s['tools_used']) if s['tools_used'] else 'N/A'}\n\n"
                    )
        except Exception as e:
            logging.warning("Failed to inject similar investigations: %s", e)

        # Result container shared between the worker thread and the SSE generator
        result_q: queue.Queue = queue.Queue()

        def _run_investigation():
            """Execute the blocking LLM investigation in a background thread."""
            answer = ""
            tool_calls_data: list = []
            status = "completed"
            error_msg = ""

            try:
                if resolved_project is not None:
                    import sys as _sys_inv

                    _frontend_dir_inv = os.path.join(os.path.dirname(__file__))
                    if _frontend_dir_inv not in _sys_inv.path:
                        _sys_inv.path.insert(0, _frontend_dir_inv)
                    from projects import (
                        build_project_tool_executor,
                    )
                    from projects import (  # noqa: PLC0415
                        get_instances_store as _get_instances_store_inv,
                    )

                    from holmes.core.tool_calling_llm import (  # noqa: PLC0415
                        ToolCallingLLM as _ToolCallingLLM,
                    )

                    _project_executor = build_project_tool_executor(
                        resolved_project, config, config.dal, _get_instances_store_inv()
                    )
                    ai = _ToolCallingLLM(
                        _project_executor,
                        config.max_steps,
                        config._get_llm(),
                        tool_results_dir=None,
                    )
                else:
                    ai = _create_scoped_toolcalling_llm(config, source)
                global_instructions = config.dal.get_global_instructions_for_account()

                from holmes.core.conversations import (  # noqa: PLC0415
                    build_chat_messages,
                )

                messages = build_chat_messages(
                    question,
                    conversation_history=None,
                    ai=ai,
                    config=config,
                    global_instructions=global_instructions,
                    additional_system_prompt=_system_prompt_additions or None,
                )

                llm_call = ai.messages_call(messages=messages)
                answer = llm_call.result

                for tc in llm_call.tool_calls or []:
                    try:
                        result_obj = getattr(tc, "result", None)
                        if result_obj is not None and hasattr(
                            result_obj, "get_stringified_data"
                        ):
                            tool_output = result_obj.get_stringified_data() or ""
                        else:
                            tool_output = (
                                str(result_obj) if result_obj is not None else ""
                            )
                    except Exception:
                        tool_output = ""
                    tool_calls_data.append(
                        {
                            "tool_name": getattr(tc, "tool_name", str(tc)),
                            "description": getattr(tc, "description", ""),
                            "result": tool_output if include_tool_call_results else "",
                            "tool_input": {},
                            "tool_output": tool_output,
                            "called_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )

            except Exception as exc:
                status = "failed"
                error_msg = str(exc)
                logging.error(
                    "Manual investigate: investigation failed: %s", exc, exc_info=True
                )

            # Persist to investigation store
            try:
                import sys as _sys

                _frontend_dir = os.path.join(os.path.dirname(__file__))
                if _frontend_dir not in _sys.path:
                    _sys.path.insert(0, _frontend_dir)
                from projects import (  # noqa: PLC0415
                    Investigation,
                    ToolCallRecord,
                    get_investigation_store,
                )

                inv = Investigation(
                    id=investigation_id,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    trigger="manual",
                    source=source.lower().replace(" ", "_"),
                    source_id="",
                    source_url="",
                    question=question,
                    answer=answer,
                    tool_calls=[
                        ToolCallRecord(
                            **{
                                k: v
                                for k, v in tc.items()
                                if k
                                in (
                                    "tool_name",
                                    "tool_input",
                                    "tool_output",
                                    "called_at",
                                )
                            }
                        )
                        for tc in tool_calls_data
                    ],
                    project_id=project_id or "",
                    status=status,
                    error=error_msg,
                )
                get_investigation_store().save(inv)
            except Exception:
                logging.warning(
                    "Manual investigate: failed to persist investigation", exc_info=True
                )

            result_q.put(
                {
                    "answer": answer,
                    "tool_calls_data": tool_calls_data,
                    "status": status,
                    "error_msg": error_msg,
                }
            )

        # Start the investigation in a background thread
        worker = threading.Thread(target=_run_investigation, daemon=True)
        worker.start()

        def _sse_generator():
            """
            Yield SSE keepalive comments every 25s until the investigation finishes,
            then emit the final result as a data event.

            The ALB idle timeout is 300s. Keepalives every 25s ensure the connection
            is never idle long enough for the ALB to close it.
            """
            _KEEPALIVE_INTERVAL = 25  # seconds between heartbeats

            while True:
                try:
                    outcome = result_q.get(timeout=_KEEPALIVE_INTERVAL)
                    break
                except queue.Empty:
                    # Investigation still running — send a heartbeat to reset ALB idle timer
                    yield ": keep-alive\n\n"

            # Emit the final result
            answer = outcome["answer"]
            tool_calls_data = outcome["tool_calls_data"]
            status = outcome["status"]
            error_msg = outcome["error_msg"]

            if status == "failed":
                payload = json.dumps({"error": error_msg})
            else:
                response_tool_calls = []
                if include_tool_calls:
                    response_tool_calls = [
                        {
                            "tool_name": tc["tool_name"],
                            "description": tc.get("description", ""),
                            "result": tc.get("result", ""),
                        }
                        for tc in tool_calls_data
                    ]
                payload = json.dumps(
                    {"analysis": answer, "tool_calls": response_tool_calls}
                )

            yield f"data: {payload}\n\n"

        return StreamingResponse(
            _sse_generator(),
            media_type="text/event-stream",
            headers={
                # Disable proxy/nginx buffering so keepalives reach the client immediately
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache",
            },
        )

    # ── Webhook endpoints ─────────────────────────────────────────────────────
    # These are NOT protected by session auth — they use their own HMAC verification.
    # The /api/webhook/ prefix is in EXEMPT_PREFIXES above.

    @app.post("/api/webhook/pagerduty")
    async def pagerduty_webhook(request: Request):
        """
        Receive PagerDuty v3 webhook events, run a Holmes investigation for
        incident.triggered events, and write the answer back as a PD note.

        Required env vars:
          PAGERDUTY_API_KEY        — PD REST API token (used for write-back)
          PAGERDUTY_USER_EMAIL     — From: header required by PD API
          PAGERDUTY_WEBHOOK_SECRET — Shared secret for HMAC-SHA256 verification
                                     (leave empty to skip verification in dev)
        """
        import hashlib as _hashlib
        import hmac as _hmac
        import threading as _threading
        import uuid as _uuid
        from datetime import datetime, timezone

        # ── 1. Read raw body (needed for HMAC verification) ──────────────────
        raw_body = await request.body()

        # ── 2. Verify HMAC-SHA256 signature ──────────────────────────────────
        webhook_secret = os.environ.get("PAGERDUTY_WEBHOOK_SECRET", "")
        if webhook_secret and not _webhook_dev_mode:
            sig_header = request.headers.get("x-pagerduty-signature", "")
            # PD sends "v1=<hex>,v1=<hex>" (may have multiple signatures)
            expected_sig = _hmac.new(
                webhook_secret.encode(),
                raw_body,
                _hashlib.sha256,
            ).hexdigest()
            # Check if any of the provided signatures match
            provided_sigs = [
                part.split("=", 1)[1]
                for part in sig_header.split(",")
                if part.startswith("v1=")
            ]
            if not any(
                _hmac.compare_digest(expected_sig, sig) for sig in provided_sigs
            ):
                logging.warning("PagerDuty webhook: invalid HMAC signature")
                raise HTTPException(status_code=401, detail="Invalid signature")

        # ── 3. Parse payload ──────────────────────────────────────────────────
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        # PD v3 webhook wraps events in a list
        events = payload if isinstance(payload, list) else [payload]

        for event in events:
            event_type = event.get("event_type") or (event.get("event") or {}).get(
                "event_type", ""
            )
            # Only act on new incidents
            if event_type not in ("incident.triggered", "incident.trigger"):
                continue

            # Extract incident data — v3 structure: event.data
            data = event.get("data") or event.get("event", {}).get("data", {})
            incident_id = data.get("id", "")
            incident_title = data.get("title", "") or data.get("summary", "")
            incident_url = data.get("self", "") or data.get("html_url", "")
            incident_body = (data.get("body") or {}).get("details", "")

            # ── Resolve project from PD service name ────────────────────
            pd_service = (data.get("service") or {}).get("summary", "") or (
                data.get("service") or {}
            ).get("id", "")
            from projects import resolve_project_for_webhook  # noqa: PLC0415

            matched_project = resolve_project_for_webhook("pagerduty", pd_service)
            matched_project_id = matched_project.id if matched_project else ""
            if matched_project:
                logging.info(
                    "PagerDuty webhook: matched project '%s' for service '%s'",
                    matched_project.name,
                    pd_service,
                )

            if not incident_id:
                logging.warning("PagerDuty webhook: missing incident id in payload")
                continue

            logging.info(
                "PagerDuty webhook: queuing investigation for incident %s — %s",
                incident_id,
                incident_title,
            )

            # ── 4. Run investigation in background thread ─────────────────────
            def _run_investigation(
                inc_id=incident_id,
                inc_title=incident_title,
                inc_url=incident_url,
                inc_body=incident_body,
                project_id=matched_project_id,
                resolved_project=matched_project,
            ):
                investigation_id = _uuid.uuid4().hex
                started_at = datetime.now(timezone.utc).isoformat()
                question = (
                    f"PagerDuty incident triggered: {inc_title}\n\n"
                    + (f"Details: {inc_body}\n\n" if inc_body else "")
                    + "Please investigate this incident and provide a root cause analysis "
                    "with recommended remediation steps."
                )
                answer = ""
                tool_calls_data: list = []
                status = "completed"
                error_msg = ""

                try:
                    if config is None:
                        logging.error("PagerDuty webhook: config not available")
                        return

                    if resolved_project is not None:
                        from projects import build_project_tool_executor  # noqa: PLC0415
                        from projects import (
                            get_instances_store as _get_instances_store_inv,
                        )  # noqa: PLC0415
                        from holmes.core.tool_calling_llm import (
                            ToolCallingLLM as _ToolCallingLLM,
                        )  # noqa: PLC0415

                        _project_executor = build_project_tool_executor(
                            resolved_project,
                            config,
                            config.dal,
                            _get_instances_store_inv(),
                        )
                        ai = _ToolCallingLLM(
                            _project_executor,
                            config.max_steps,
                            config._get_llm(),
                            tool_results_dir=None,
                        )
                    else:
                        ai = _create_scoped_toolcalling_llm(config, "pagerduty")
                    global_instructions = (
                        config.dal.get_global_instructions_for_account()
                    )

                    from holmes.core.conversations import (  # noqa: PLC0415
                        build_chat_messages,
                    )

                    messages = build_chat_messages(
                        question,
                        conversation_history=None,
                        ai=ai,
                        config=config,
                        global_instructions=global_instructions,
                    )

                    llm_call = ai.messages_call(messages=messages)
                    answer = llm_call.result

                    # Collect tool call records
                    for tc in llm_call.tool_calls or []:
                        try:
                            result_obj = getattr(tc, "result", None)
                            if result_obj is not None and hasattr(
                                result_obj, "get_stringified_data"
                            ):
                                tool_output = result_obj.get_stringified_data() or ""
                            else:
                                tool_output = (
                                    str(result_obj) if result_obj is not None else ""
                                )
                        except Exception:
                            tool_output = ""
                        tool_calls_data.append(
                            {
                                "tool_name": getattr(tc, "tool_name", str(tc)),
                                "tool_input": {},
                                "tool_output": tool_output,
                                "called_at": datetime.now(timezone.utc).isoformat(),
                            }
                        )

                except Exception as exc:
                    status = "failed"
                    error_msg = str(exc)
                    logging.error(
                        "PagerDuty webhook: investigation failed for incident %s: %s",
                        inc_id,
                        exc,
                        exc_info=True,
                    )

                # ── 5. Persist investigation ──────────────────────────────────
                try:
                    import sys as _sys

                    _frontend_dir = os.path.join(os.path.dirname(__file__))
                    if _frontend_dir not in _sys.path:
                        _sys.path.insert(0, _frontend_dir)
                    from projects import (  # noqa: PLC0415
                        Investigation,
                        ToolCallRecord,
                        get_investigation_store,
                    )

                    inv = Investigation(
                        id=investigation_id,
                        started_at=started_at,
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        trigger="webhook",
                        source="pagerduty",
                        source_id=inc_id,
                        source_url=inc_url,
                        question=question,
                        answer=answer,
                        tool_calls=[ToolCallRecord(**tc) for tc in tool_calls_data],
                        project_id=project_id,
                        status=status,
                        error=error_msg,
                    )
                    get_investigation_store().save(inv)
                    logging.info(
                        "PagerDuty webhook: saved investigation %s for incident %s",
                        investigation_id,
                        inc_id,
                    )
                except Exception:
                    logging.warning(
                        "PagerDuty webhook: failed to persist investigation",
                        exc_info=True,
                    )

                # ── 6. Write answer back to PagerDuty incident ────────────────
                if answer and status == "completed":
                    try:
                        from projects import get_webhook_settings_store  # noqa: PLC0415

                        _wb_enabled = (
                            get_webhook_settings_store()
                            .get("pagerduty", project_id=None)
                            .get("write_back_enabled", True)
                        )
                        pd_api_key = os.environ.get("PAGERDUTY_API_KEY", "")
                        pd_user_email = os.environ.get("PAGERDUTY_USER_EMAIL", "")
                        if not _wb_enabled:
                            logging.info(
                                "PagerDuty webhook: write-back disabled by user setting — "
                                "skipping note for incident %s",
                                inc_id,
                            )
                        elif pd_api_key and pd_user_email:
                            import requests as _requests  # noqa: PLC0415

                            note_body = f"**HolmesGPT Investigation**\n\n{answer}"
                            resp = _requests.post(
                                f"https://api.pagerduty.com/incidents/{inc_id}/notes",
                                headers={
                                    "Authorization": f"Token token={pd_api_key}",
                                    "From": pd_user_email,
                                    "Content-Type": "application/json",
                                    "Accept": "application/vnd.pagerduty+json;version=2",
                                },
                                json={"note": {"content": note_body}},
                                timeout=15,
                            )
                            if resp.ok:
                                logging.info(
                                    "PagerDuty webhook: wrote note to incident %s",
                                    inc_id,
                                )
                            else:
                                logging.warning(
                                    "PagerDuty webhook: note write-back failed %s: %s",
                                    resp.status_code,
                                    resp.text,
                                )
                        else:
                            logging.info(
                                "PagerDuty webhook: PAGERDUTY_API_KEY or PAGERDUTY_USER_EMAIL "
                                "not configured — skipping write-back for incident %s",
                                inc_id,
                            )
                    except Exception:
                        logging.warning(
                            "PagerDuty webhook: write-back exception for incident %s",
                            inc_id,
                            exc_info=True,
                        )

            _threading.Thread(target=_run_investigation, daemon=True).start()

        return JSONResponse({"ok": True})

    @app.post("/api/webhook/ado")
    async def ado_webhook(request: Request):
        """
        Receive Azure DevOps service hook events, run a Holmes investigation for
        workitem.created events, and add a comment to the work item.

        Required env vars:
          ADO_WEBHOOK_USERNAME  — Basic-auth username configured in the ADO service hook
          ADO_WEBHOOK_PASSWORD  — Basic-auth password configured in the ADO service hook
          ADO_PAT               — Personal Access Token for writing comments back to ADO
          ADO_ORGANIZATION      — ADO organization name (e.g. "pditechnologies")
        """
        import base64 as _base64
        import threading as _threading
        import uuid as _uuid
        from datetime import datetime, timezone

        # ── 1. Basic-auth verification ────────────────────────────────────────
        ado_username = os.environ.get("ADO_WEBHOOK_USERNAME", "")
        ado_password = os.environ.get("ADO_WEBHOOK_PASSWORD", "")
        if (ado_username or ado_password) and not _webhook_dev_mode:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Basic "):
                try:
                    decoded = _base64.b64decode(auth_header[6:]).decode()
                    provided_user, _, provided_pass = decoded.partition(":")
                except Exception:
                    provided_user = provided_pass = ""
                valid_user = (
                    hmac.compare_digest(provided_user, ado_username)
                    if ado_username
                    else True
                )
                valid_pass = (
                    hmac.compare_digest(provided_pass, ado_password)
                    if ado_password
                    else True
                )
                if not (valid_user and valid_pass):
                    logging.warning("ADO webhook: invalid Basic-auth credentials")
                    raise HTTPException(status_code=401, detail="Invalid credentials")
            else:
                logging.warning("ADO webhook: missing Basic-auth header")
                raise HTTPException(status_code=401, detail="Missing credentials")

        # ── 2. Parse payload ──────────────────────────────────────────────────
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        event_type = payload.get("eventType", "")
        if event_type not in ("workitem.created", "workitem.updated"):
            return JSONResponse({"ok": True, "skipped": True})

        resource = payload.get("resource") or {}
        fields = resource.get("fields") or {}
        work_item_id = str(resource.get("id", ""))

        def _ado_field(val):
            """Extract field value: handles both str and {newValue: ...} dict formats."""
            if isinstance(val, str):
                return val
            if isinstance(val, dict):
                return val.get("newValue", "")
            return ""

        work_item_title = _ado_field(fields.get("System.Title")) or resource.get(
            "url", ""
        )
        work_item_type = _ado_field(fields.get("System.WorkItemType"))
        work_item_url = resource.get("url", "") or resource.get("_links", {}).get(
            "html", {}
        ).get("href", "")
        work_item_description = _ado_field(fields.get("System.Description"))
        work_item_area = _ado_field(fields.get("System.AreaPath"))
        work_item_iteration = _ado_field(fields.get("System.IterationPath"))
        work_item_state = _ado_field(fields.get("System.State"))
        work_item_severity = _ado_field(fields.get("Microsoft.VSTS.Common.Severity"))
        work_item_assigned = ""
        assigned_raw = fields.get("System.AssignedTo")
        if isinstance(assigned_raw, dict):
            work_item_assigned = assigned_raw.get(
                "displayName", ""
            ) or assigned_raw.get("newValue", "")
        elif isinstance(assigned_raw, str):
            work_item_assigned = assigned_raw
        work_item_reason = _ado_field(fields.get("System.Reason"))

        # ── 2b. Resolve project from ADO team project name ──────────────
        ado_team_project = _ado_field(fields.get("System.TeamProject"))
        from projects import resolve_project_for_webhook  # noqa: PLC0415

        matched_project = resolve_project_for_webhook("ado", ado_team_project)
        matched_project_id = matched_project.id if matched_project else ""
        if matched_project:
            logging.info(
                "ADO webhook: matched project '%s' (%s) for ADO team project '%s'",
                matched_project.name,
                matched_project.id,
                ado_team_project,
            )

        if not work_item_id:
            logging.warning("ADO webhook: missing work item id in payload")
            return JSONResponse({"ok": True})

        logging.info(
            "ADO webhook: queuing investigation for work item %s — %s",
            work_item_id,
            work_item_title,
        )

        # ── 3. Run investigation in background thread ─────────────────────────
        def _run_ado_investigation(
            wi_id=work_item_id,
            wi_title=work_item_title,
            wi_type=work_item_type,
            wi_url=work_item_url,
            wi_description=work_item_description,
            wi_area=work_item_area,
            wi_iteration=work_item_iteration,
            wi_state=work_item_state,
            wi_severity=work_item_severity,
            wi_assigned=work_item_assigned,
            wi_reason=work_item_reason,
            wi_project=ado_team_project,
            project_id=matched_project_id,
            resolved_project=matched_project,
        ):
            investigation_id = _uuid.uuid4().hex
            started_at = datetime.now(timezone.utc).isoformat()

            # Build a rich context block from available fields
            context_lines = []
            if wi_project:
                context_lines.append(f"- Project: {wi_project}")
            if wi_area:
                context_lines.append(f"- Area: {wi_area}")
            if wi_iteration:
                context_lines.append(f"- Iteration: {wi_iteration}")
            if wi_state:
                context_lines.append(f"- State: {wi_state}")
            if wi_severity:
                context_lines.append(f"- Severity: {wi_severity}")
            if wi_assigned:
                context_lines.append(f"- Assigned to: {wi_assigned}")
            if wi_reason:
                context_lines.append(f"- Reason: {wi_reason}")
            if wi_url:
                context_lines.append(f"- URL: {wi_url}")
            context_block = "\n".join(context_lines)

            question = (
                f"Azure DevOps {wi_type or 'work item'} #{wi_id}: {wi_title}\n\n"
                + (f"**Description:**\n{wi_description}\n\n" if wi_description else "")
                + (
                    f"**Work Item Details:**\n{context_block}\n\n"
                    if context_block
                    else ""
                )
                + "Investigate this work item using all available tools. "
                "Check for related recent incidents, deployments, code changes, "
                "logs, and metrics that could provide context. "
                "Provide a root cause analysis if applicable, potential impact, "
                "and recommended next steps."
            )
            answer = ""
            tool_calls_data: list = []
            status = "completed"
            error_msg = ""

            try:
                if config is None:
                    logging.error("ADO webhook: config not available")
                    return

                if resolved_project is not None:
                    from projects import build_project_tool_executor  # noqa: PLC0415
                    from projects import get_instances_store as _get_instances_store_inv  # noqa: PLC0415
                    from holmes.core.tool_calling_llm import (
                        ToolCallingLLM as _ToolCallingLLM,
                    )  # noqa: PLC0415

                    _project_executor = build_project_tool_executor(
                        resolved_project, config, config.dal, _get_instances_store_inv()
                    )
                    ai = _ToolCallingLLM(
                        _project_executor,
                        config.max_steps,
                        config._get_llm(),
                        tool_results_dir=None,
                    )
                else:
                    ai = _create_scoped_toolcalling_llm(config, "ado")
                global_instructions = config.dal.get_global_instructions_for_account()

                from holmes.core.conversations import (  # noqa: PLC0415
                    build_chat_messages,
                )

                messages = build_chat_messages(
                    question,
                    conversation_history=None,
                    ai=ai,
                    config=config,
                    global_instructions=global_instructions,
                    additional_system_prompt=_system_prompt_additions or None,
                )

                llm_call = ai.messages_call(messages=messages)
                answer = llm_call.result

                for tc in llm_call.tool_calls or []:
                    try:
                        result_obj = getattr(tc, "result", None)
                        if result_obj is not None and hasattr(
                            result_obj, "get_stringified_data"
                        ):
                            tool_output = result_obj.get_stringified_data() or ""
                        else:
                            tool_output = (
                                str(result_obj) if result_obj is not None else ""
                            )
                    except Exception:
                        tool_output = ""
                    tool_calls_data.append(
                        {
                            "tool_name": getattr(tc, "tool_name", str(tc)),
                            "tool_input": {},
                            "tool_output": tool_output,
                            "called_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )

            except Exception as exc:
                status = "failed"
                error_msg = str(exc)
                logging.error(
                    "ADO webhook: investigation failed for work item %s: %s",
                    wi_id,
                    exc,
                    exc_info=True,
                )

            # ── 4. Persist investigation ──────────────────────────────────────
            try:
                import sys as _sys

                _frontend_dir = os.path.join(os.path.dirname(__file__))
                if _frontend_dir not in _sys.path:
                    _sys.path.insert(0, _frontend_dir)
                from projects import (  # noqa: PLC0415
                    Investigation,
                    ToolCallRecord,
                    get_investigation_store,
                )

                inv = Investigation(
                    id=investigation_id,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    trigger="webhook",
                    source="ado",
                    source_id=wi_id,
                    source_url=wi_url,
                    question=question,
                    answer=answer,
                    tool_calls=[ToolCallRecord(**tc) for tc in tool_calls_data],
                    project_id=project_id,
                    status=status,
                    error=error_msg,
                )
                get_investigation_store().save(inv)
                logging.info(
                    "ADO webhook: saved investigation %s for work item %s",
                    investigation_id,
                    wi_id,
                )
            except Exception:
                logging.warning(
                    "ADO webhook: failed to persist investigation", exc_info=True
                )

            # ── 5. Write answer back to ADO work item as a comment ────────────
            if answer and status == "completed":
                try:
                    from projects import get_webhook_settings_store  # noqa: PLC0415

                    _wb_enabled = (
                        get_webhook_settings_store()
                        .get("ado", project_id=None)
                        .get("write_back_enabled", True)
                    )
                    ado_pat = os.environ.get("ADO_PAT", "")
                    ado_org = os.environ.get("ADO_ORGANIZATION", "")
                    if not _wb_enabled:
                        logging.info(
                            "ADO webhook: write-back disabled by user setting — "
                            "skipping comment for work item %s",
                            wi_id,
                        )
                    elif ado_pat and ado_org and wi_id:
                        import requests as _requests  # noqa: PLC0415

                        comment_html = f"<b>HolmesGPT Investigation</b><br><br>{answer.replace(chr(10), '<br>')}"
                        token_b64 = _base64.b64encode(f":{ado_pat}".encode()).decode()
                        resp = _requests.post(
                            f"https://dev.azure.com/{ado_org}/_apis/wit/workItems/{wi_id}/comments?api-version=7.1-preview.3",
                            headers={
                                "Authorization": f"Basic {token_b64}",
                                "Content-Type": "application/json",
                            },
                            json={"text": comment_html},
                            timeout=15,
                        )
                        if resp.ok:
                            logging.info(
                                "ADO webhook: wrote comment to work item %s", wi_id
                            )
                        else:
                            logging.warning(
                                "ADO webhook: comment write-back failed %s: %s",
                                resp.status_code,
                                resp.text,
                            )
                    else:
                        logging.info(
                            "ADO webhook: ADO_PAT or ADO_ORGANIZATION not configured — "
                            "skipping write-back for work item %s",
                            wi_id,
                        )
                except Exception:
                    logging.warning(
                        "ADO webhook: write-back exception for work item %s",
                        wi_id,
                        exc_info=True,
                    )

        _threading.Thread(target=_run_ado_investigation, daemon=True).start()

        return JSONResponse({"ok": True})

    @app.post("/api/webhook/salesforce")
    async def salesforce_webhook(request: Request):
        """
        Receive Salesforce outbound message / webhook events for Case created,
        run a Holmes investigation, and add a comment to the Case.

        Salesforce outbound messages use SOAP; this endpoint also accepts a
        simpler JSON format from Salesforce Flow HTTP callouts.

        Required env vars:
          SALESFORCE_WEBHOOK_TOKEN  — Shared token sent in X-Salesforce-Token header
          SALESFORCE_INSTANCE_URL   — e.g. https://myorg.my.salesforce.com
          SALESFORCE_ACCESS_TOKEN   — OAuth access token for writing comments back
        """
        import threading as _threading
        import uuid as _uuid
        from datetime import datetime, timezone

        # ── 1. Token verification ─────────────────────────────────────────────
        sf_token = os.environ.get("SALESFORCE_WEBHOOK_TOKEN", "")
        if sf_token and not _webhook_dev_mode:
            provided = request.headers.get("x-salesforce-token", "")
            if not provided or not hmac.compare_digest(provided, sf_token):
                logging.warning("Salesforce webhook: invalid token")
                raise HTTPException(status_code=401, detail="Invalid token")

        # ── 2. Parse payload ──────────────────────────────────────────────────
        content_type = request.headers.get("content-type", "")
        raw_body = await request.body()

        case_id = ""
        case_number = ""
        case_subject = ""
        case_description = ""
        case_url = ""
        sf_account = ""

        if "xml" in content_type or raw_body.lstrip().startswith(b"<"):
            # SOAP outbound message — extract fields with basic string parsing
            body_str = raw_body.decode(errors="replace")
            import re as _re  # noqa: PLC0415

            def _soap_field(tag: str) -> str:
                m = _re.search(
                    rf"<(?:\w+:)?{tag}[^>]*>(.*?)</(?:\w+:)?{tag}>",
                    body_str,
                    _re.DOTALL,
                )
                return m.group(1).strip() if m else ""

            case_id = _soap_field("Id") or _soap_field("CaseId")
            case_number = _soap_field("CaseNumber")
            case_subject = _soap_field("Subject")
            case_description = _soap_field("Description")
            sf_instance = os.environ.get("SALESFORCE_INSTANCE_URL", "").rstrip("/")
            case_url = f"{sf_instance}/{case_id}" if sf_instance and case_id else ""
            sf_account = _soap_field("AccountName") or _soap_field("Account")
        else:
            # JSON payload (Flow HTTP callout or custom webhook)
            try:
                payload = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid payload")
            case_id = (
                payload.get("Id") or payload.get("id") or payload.get("CaseId", "")
            )
            case_number = payload.get("CaseNumber") or payload.get("case_number", "")
            case_subject = payload.get("Subject") or payload.get("subject", "")
            case_description = payload.get("Description") or payload.get(
                "description", ""
            )
            sf_instance = os.environ.get("SALESFORCE_INSTANCE_URL", "").rstrip("/")
            case_url = payload.get("url") or (
                f"{sf_instance}/{case_id}" if sf_instance and case_id else ""
            )
            sf_account = payload.get("AccountName") or payload.get("Account", "")

        if not case_id:
            logging.warning("Salesforce webhook: missing case id in payload")
            return JSONResponse({"ok": True})

        logging.info(
            "Salesforce webhook: queuing investigation for case %s — %s",
            case_number or case_id,
            case_subject,
        )

        from projects import resolve_project_for_webhook  # noqa: PLC0415

        matched_project = resolve_project_for_webhook("salesforce", sf_account)
        matched_project_id = matched_project.id if matched_project else ""
        if matched_project:
            logging.info(
                "Salesforce webhook: matched project '%s' for account '%s'",
                matched_project.name,
                sf_account,
            )

        # ── 3. Run investigation in background thread ─────────────────────────
        def _run_sf_investigation(
            c_id=case_id,
            c_number=case_number,
            c_subject=case_subject,
            c_description=case_description,
            c_url=case_url,
            project_id=matched_project_id,
            resolved_project=matched_project,
        ):
            investigation_id = _uuid.uuid4().hex
            started_at = datetime.now(timezone.utc).isoformat()
            display_id = f"Case {c_number}" if c_number else f"Case ID {c_id}"
            question = (
                f"Salesforce case created: {display_id} — {c_subject}\n\n"
                + (f"Description: {c_description}\n\n" if c_description else "")
                + "Please investigate this support case and provide relevant context, "
                "potential root cause, and recommended resolution steps."
            )
            answer = ""
            tool_calls_data: list = []
            status = "completed"
            error_msg = ""

            try:
                if config is None:
                    logging.error("Salesforce webhook: config not available")
                    return

                if resolved_project is not None:
                    from projects import build_project_tool_executor  # noqa: PLC0415
                    from projects import get_instances_store as _get_instances_store_inv  # noqa: PLC0415
                    from holmes.core.tool_calling_llm import (
                        ToolCallingLLM as _ToolCallingLLM,
                    )  # noqa: PLC0415

                    _project_executor = build_project_tool_executor(
                        resolved_project, config, config.dal, _get_instances_store_inv()
                    )
                    ai = _ToolCallingLLM(
                        _project_executor,
                        config.max_steps,
                        config._get_llm(),
                        tool_results_dir=None,
                    )
                else:
                    ai = _create_scoped_toolcalling_llm(config, "salesforce")
                global_instructions = config.dal.get_global_instructions_for_account()

                from holmes.core.conversations import (  # noqa: PLC0415
                    build_chat_messages,
                )

                messages = build_chat_messages(
                    question,
                    conversation_history=None,
                    ai=ai,
                    config=config,
                    global_instructions=global_instructions,
                    additional_system_prompt=_system_prompt_additions or None,
                )

                llm_call = ai.messages_call(messages=messages)
                answer = llm_call.result

                for tc in llm_call.tool_calls or []:
                    try:
                        result_obj = getattr(tc, "result", None)
                        if result_obj is not None and hasattr(
                            result_obj, "get_stringified_data"
                        ):
                            tool_output = result_obj.get_stringified_data() or ""
                        else:
                            tool_output = (
                                str(result_obj) if result_obj is not None else ""
                            )
                    except Exception:
                        tool_output = ""
                    tool_calls_data.append(
                        {
                            "tool_name": getattr(tc, "tool_name", str(tc)),
                            "tool_input": {},
                            "tool_output": tool_output,
                            "called_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )

            except Exception as exc:
                status = "failed"
                error_msg = str(exc)
                logging.error(
                    "Salesforce webhook: investigation failed for case %s: %s",
                    c_id,
                    exc,
                    exc_info=True,
                )

            # ── 4. Persist investigation ──────────────────────────────────────
            try:
                import sys as _sys

                _frontend_dir = os.path.join(os.path.dirname(__file__))
                if _frontend_dir not in _sys.path:
                    _sys.path.insert(0, _frontend_dir)
                from projects import (  # noqa: PLC0415
                    Investigation,
                    ToolCallRecord,
                    get_investigation_store,
                )

                inv = Investigation(
                    id=investigation_id,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    trigger="webhook",
                    source="salesforce",
                    source_id=c_id,
                    source_url=c_url,
                    question=question,
                    answer=answer,
                    tool_calls=[ToolCallRecord(**tc) for tc in tool_calls_data],
                    project_id=project_id,
                    status=status,
                    error=error_msg,
                )
                get_investigation_store().save(inv)
                logging.info(
                    "Salesforce webhook: saved investigation %s for case %s",
                    investigation_id,
                    c_id,
                )
            except Exception:
                logging.warning(
                    "Salesforce webhook: failed to persist investigation", exc_info=True
                )

            # ── 5. Write answer back to Salesforce Case as a comment ──────────
            if answer and status == "completed":
                try:
                    from projects import get_webhook_settings_store  # noqa: PLC0415

                    _wb_enabled = (
                        get_webhook_settings_store()
                        .get("salesforce", project_id=None)
                        .get("write_back_enabled", True)
                    )
                    sf_instance_url = os.environ.get(
                        "SALESFORCE_INSTANCE_URL", ""
                    ).rstrip("/")
                    sf_access_token = os.environ.get("SALESFORCE_ACCESS_TOKEN", "")
                    if not _wb_enabled:
                        logging.info(
                            "Salesforce webhook: write-back disabled by user setting — "
                            "skipping comment for case %s",
                            c_id,
                        )
                    elif sf_instance_url and sf_access_token and c_id:
                        import requests as _requests  # noqa: PLC0415

                        comment_body = f"HolmesGPT Investigation\n\n{answer}"
                        resp = _requests.post(
                            f"{sf_instance_url}/services/data/v59.0/sobjects/CaseComment",
                            headers={
                                "Authorization": f"Bearer {sf_access_token}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "ParentId": c_id,
                                "CommentBody": comment_body,
                                "IsPublished": False,
                            },
                            timeout=15,
                        )
                        if resp.ok:
                            logging.info(
                                "Salesforce webhook: wrote comment to case %s", c_id
                            )
                        else:
                            logging.warning(
                                "Salesforce webhook: comment write-back failed %s: %s",
                                resp.status_code,
                                resp.text,
                            )
                    else:
                        logging.info(
                            "Salesforce webhook: SALESFORCE_INSTANCE_URL or SALESFORCE_ACCESS_TOKEN "
                            "not configured — skipping write-back for case %s",
                            c_id,
                        )
                except Exception:
                    logging.warning(
                        "Salesforce webhook: write-back exception for case %s",
                        c_id,
                        exc_info=True,
                    )

        _threading.Thread(target=_run_sf_investigation, daemon=True).start()

        return JSONResponse({"ok": True})

    from users_api import mount_users_api
    mount_users_api(app)

    # Static file serving - must be registered last (catch-all)
    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        """Serve React SPA static files with fallback to index.html."""
        if not STATIC_DIR.exists():
            raise HTTPException(status_code=404, detail="Frontend not built")

        # Try to serve the exact file
        file_path = STATIC_DIR / path
        if file_path.is_file() and STATIC_DIR in file_path.resolve().parents:
            return FileResponse(file_path)

        # Fallback to index.html for client-side routing
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index, media_type="text/html")

        raise HTTPException(status_code=404, detail="Not found")

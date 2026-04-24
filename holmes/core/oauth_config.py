"""OAuth configuration types, exceptions, token exchange, and exchange manager."""

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


# ── Exceptions ────────────────────────────────────────────────────────────


class OAuthTokenExchangeError(Exception):
    """Raised when an OAuth authorization code exchange fails."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Token exchange failed (HTTP {status_code}): {detail}")


class OAuthConfigLookupError(Exception):
    """Raised when a toolset's OAuth config cannot be found or is invalid."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


# ── Token exchange ────────────────────────────────────────────────────────


def exchange_code_for_tokens(
    token_url: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    code_verifier: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> dict:
    """Exchange an OAuth authorization code for tokens at the IdP's token endpoint.

    Returns the parsed JSON token response (containing at least ``access_token``).
    Raises :class:`OAuthTokenExchangeError` on HTTP failure or missing ``access_token``.
    """
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier

    # Some IdPs (e.g. Notion) require client credentials via HTTP Basic Auth,
    # while others (e.g. Supabase) accept them in the POST body.
    # Try Basic Auth first when client_secret is present, fall back to POST body.
    auth = None
    if client_secret:
        auth = httpx.BasicAuth(client_id, client_secret)

    try:
        resp = httpx.post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            auth=auth,
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise OAuthTokenExchangeError(0, f"Token request to {token_url} failed: {e}") from e

    # If Basic Auth failed, retry with client_secret in POST body
    if client_secret and not resp.is_success:
        data["client_secret"] = client_secret
        try:
            resp = httpx.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
        except httpx.HTTPError as e:
            raise OAuthTokenExchangeError(0, f"Token request to {token_url} failed: {e}") from e

    if not resp.is_success:
        detail = resp.text[:300] if resp.text else "Unknown error"
        raise OAuthTokenExchangeError(resp.status_code, detail)

    try:
        token_data = resp.json()
    except (ValueError, json.JSONDecodeError) as e:
        raise OAuthTokenExchangeError(resp.status_code, f"Invalid JSON in token response: {e}") from e

    if "access_token" not in token_data:
        raise OAuthTokenExchangeError(resp.status_code, f"Response missing 'access_token'. Keys: {list(token_data.keys())}")

    return token_data


# ── Data types ────────────────────────────────────────────────────────────


@dataclass
class OAuthEndpoints:
    """Minimal OAuth endpoint config

    Passed by toolset_mcp.py to the pure-OAuth functions so they
    don't depend on pydantic models or MCP-specific types.
    """

    authorization_url: Optional[str] = None
    token_url: Optional[str] = None
    client_id: Optional[str] = None
    scopes: Optional[List[str]] = None
    registration_endpoint: Optional[str] = None


class MCPOAuthConfig(BaseModel):
    """OAuth authorization_code config for MCP servers requiring user login.

    Set enabled=true with no other fields to auto-discover OAuth endpoints
    via the MCP OAuth flow (RFC 9728 Protected Resource Metadata + OIDC Discovery + DCR).

    If any of authorization_url, token_url, or client_id is set, enabled defaults to true.
    """

    enabled: bool = Field(default=False, description="Enable OAuth for this MCP server. Auto-set to true when other OAuth fields are provided.")
    authorization_url: Optional[str] = Field(default=None, description="IdP authorization endpoint URL. Auto-discovered if omitted.")
    token_url: Optional[str] = Field(default=None, description="IdP token endpoint URL. Auto-discovered if omitted.")
    client_id: Optional[str] = Field(default=None, description="OAuth public client ID. Auto-registered via DCR if omitted.")
    scopes: Optional[List[str]] = Field(default=None, description="OAuth scopes to request.")
    registration_endpoint: Optional[str] = Field(default=None, description="DCR endpoint (auto-populated during discovery, sent to frontend for client registration).")

    @model_validator(mode="after")
    def auto_enable_when_configured(self):
        """Auto-enable OAuth when any endpoint or client_id is explicitly set."""
        if not self.enabled and (self.authorization_url or self.token_url or self.client_id):
            self.enabled = True
        return self


class OAuthDecisionCode(BaseModel):
    """OAuth authorization code payload sent by the frontend after browser auth.

    The frontend builds this as the `decision` field on ToolApprovalDecision.
    """

    toolset_name: str
    code: str
    redirect_uri: str
    code_verifier: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


def parse_oauth_decision(decision: Optional[Dict[str, Any]]) -> Optional[OAuthDecisionCode]:
    """Try to parse a tool approval decision as an OAuth code exchange.

    Returns the parsed OAuthDecisionCode if valid, None otherwise.
    """
    if not decision:
        return None
    try:
        return OAuthDecisionCode(**decision)
    except Exception:
        return None


# ── Pending OAuth Exchange Manager ────────────────────────────────────────


class _PendingOAuthExchange:
    """State for a pending OAuth approval: PKCE verifier and config."""

    def __init__(self, code_verifier: str, oauth_config: MCPOAuthConfig, redirect_uri: str) -> None:
        self.code_verifier = code_verifier
        self.oauth_config = oauth_config
        self.redirect_uri = redirect_uri


class OAuthExchangeManager:
    """Manages pending OAuth authorization code exchanges.

    Bridges the gap between requires_approval() (which generates PKCE and registers
    a pending exchange) and complete_exchange() (which consumes the pending exchange
    and trades the auth code for tokens).
    """

    def __init__(self) -> None:
        self._pending: Dict[str, _PendingOAuthExchange] = {}
        self._lock = threading.Lock()

    def register_pending(
        self,
        tool_call_id: str,
        code_verifier: str,
        oauth_config: MCPOAuthConfig,
        redirect_uri: str = "",
    ) -> None:
        """Register a pending OAuth exchange for the given tool call."""
        with self._lock:
            self._pending[tool_call_id] = _PendingOAuthExchange(
                code_verifier=code_verifier,
                oauth_config=oauth_config,
                redirect_uri=redirect_uri,
            )

    def complete_exchange(
        self,
        tool_call_id: str,
        oauth_code: "OAuthDecisionCode",
        request_context: Optional[Dict[str, Any]],
        token_manager: Any = None,
    ) -> None:
        """Exchange an OAuth authorization code for an access token.

        Called from tool_calling_llm when a tool approval decision includes an
        OAuth payload from the frontend browser flow.

        Args:
            token_manager: OAuthTokenManager instance to store the resulting token.
                           If None, uses the module-level singleton via oauth_utils.
        """
        with self._lock:
            pending = self._pending.pop(tool_call_id, None)

        if pending is None:
            logger.error("OAuth exchange: no pending exchange for tool_call_id=%s", tool_call_id)
            return

        # Frontend may include client_id and client_secret from DCR
        client_id = oauth_code.client_id or pending.oauth_config.client_id
        if client_id and not pending.oauth_config.client_id:
            pending.oauth_config.client_id = client_id
            logger.info("OAuth: using client_id from frontend DCR: %s", client_id)

        try:
            token_data = exchange_code_for_tokens(
                token_url=pending.oauth_config.token_url,
                code=oauth_code.code,
                redirect_uri=oauth_code.redirect_uri,
                client_id=client_id,
                code_verifier=pending.code_verifier,
                client_secret=oauth_code.client_secret,
            )
        except (OAuthTokenExchangeError, KeyError, Exception):
            logger.exception("OAuth exchange failed (tool_call_id=%s, token_url=%s)", tool_call_id, pending.oauth_config.token_url)
            return

        if token_manager is None:
            from holmes.core.oauth_utils import _get_token_manager
            token_manager = _get_token_manager()

        token_manager.store_token(pending.oauth_config, token_data, request_context)
        logger.info(
            "OAuth token stored (idp=%s, expires_in=%s, has_refresh=%s)",
            pending.oauth_config.token_url, token_data.get("expires_in"), "refresh_token" in token_data,
        )


# ── Singleton ─────────────────────────────────────────────────────────────

_exchange_manager = OAuthExchangeManager()


def _get_exchange_manager() -> OAuthExchangeManager:
    return _exchange_manager

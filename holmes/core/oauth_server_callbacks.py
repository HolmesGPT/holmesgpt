"""OAuth server callback endpoint helpers.

Used by server.py for the /api/oauth/callback endpoint.
Kept separate from oauth_utils.py to avoid circular imports
(this module lazy-imports RemoteMCPToolset at call time).
"""

import logging
from typing import Any, List, Optional

from holmes.core.models import OAuthCallbackRequest, OAuthCallbackResponse
from holmes.core.oauth_config import OAuthConfigLookupError
from holmes.core.oauth_utils import exchange_code_for_tokens
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

logger = logging.getLogger(__name__)


def get_toolset_oauth_config(
    toolsets: List[Any],
    toolset_name: str,
    token_manager: Any,
    client_id_override: Optional[str] = None,
) -> tuple:
    """Look up a toolset's OAuth config from a list of toolsets.

    Returns ``(oauth_config, client_id, token_manager, toolset)``.
    Raises :class:`OAuthConfigLookupError` on failure.
    """
    toolset = None
    for ts in toolsets:
        if isinstance(ts, RemoteMCPToolset) and (ts.name == toolset_name or ts.connect_tool_name == toolset_name):
            toolset = ts
            break

    if not toolset:
        raise OAuthConfigLookupError(f"Toolset '{toolset_name}' not found")

    if not toolset.is_oauth_enabled:
        raise OAuthConfigLookupError(f"Toolset '{toolset_name}' does not have OAuth enabled")

    oauth = toolset._mcp_config.oauth
    if not oauth.token_url:
        raise OAuthConfigLookupError(f"OAuth config for '{toolset_name}' missing token_url")

    client_id = client_id_override or oauth.client_id
    if not client_id:
        raise OAuthConfigLookupError(f"No client_id available for '{toolset_name}'")

    return oauth, client_id, token_manager, toolset


def process_oauth_callback(
    request: OAuthCallbackRequest,
    toolsets: List[Any],
    token_manager: Any,
    executor: Optional[Any] = None,
) -> OAuthCallbackResponse:
    """Process an OAuth callback: look up config, exchange code, store tokens.

    Shared by both the HTTP endpoint and the in-flight tool-approval path.
    If executor is provided, also loads and caches real tools per user so
    subsequent requests skip the _connect placeholder.
    """
    oauth, client_id, mgr, toolset = get_toolset_oauth_config(
        toolsets, request.toolset_name, token_manager, request.client_id,
    )

    # Use DCR client_id from frontend for this exchange without mutating the
    # shared oauth config (other users may have different DCR client_ids)
    effective_client_id = client_id or oauth.client_id

    # Use client_secret from frontend (DCR) if available, otherwise fall back
    # to the server-side config (for pre-registered confidential clients like Azure AD).
    effective_client_secret = request.client_secret or oauth.client_secret

    logger.info("OAuth exchange: token_url=%s client_id=%s", oauth.token_url, effective_client_id)
    token_data = exchange_code_for_tokens(
        token_url=oauth.token_url,
        code=request.code,
        redirect_uri=request.redirect_uri,
        client_id=effective_client_id,
        code_verifier=request.code_verifier,
        client_secret=effective_client_secret,
    )
    # Include client_id in token_data so store_token persists it for refresh
    token_data["client_id"] = effective_client_id

    request_context = {"user_id": request.user_id} if request.user_id else None
    mgr.store_token(oauth, token_data, request_context=request_context)
    logger.info("OAuth tokens stored for toolset '%s'", request.toolset_name)

    # Load and cache real tools so subsequent requests skip _connect
    if request.user_id and executor and toolset:
        try:
            executor.oauth_connector.load_tools_for_user(request.user_id, toolset, request_context)
        except Exception:
            logger.warning("Failed to preload tools after OAuth for %s", toolset.name, exc_info=True)

    return OAuthCallbackResponse(success=True)

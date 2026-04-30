"""OAuthToolConnector: per-user OAuth tool lifecycle management.

Owned by ToolExecutor. Handles token exchange, tool loading from MCP servers,
and per-user tool storage so authenticated users see real tools instead of
_connect placeholders.
"""

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

import httpx

from holmes.core.oauth_config import (
    OAuthDecisionCode,
    OAuthTokenExchangeError,
    _get_exchange_manager,
    parse_oauth_decision,
)
from holmes.core.oauth_utils import _get_token_manager
from holmes.core.tools import Tool
from holmes.plugins.toolsets.mcp.oauth_token_store import DiskTokenStore

logger = logging.getLogger(__name__)


class OAuthToolConnector:
    """Handles OAuth tool lifecycle: token exchange, tool loading, per-user storage.

    Owned by ToolExecutor. ToolCallingLLM delegates OAuth decisions to it
    without needing OAuth-specific imports or logic.
    """

    def __init__(self) -> None:
        self._user_tools: Dict[str, Dict[str, List[Tool]]] = {}
        self._lock = threading.Lock()
        # Per-user tool→toolset mapping: {user_id: {tool_name: toolset}}
        self._user_tool_to_toolset: Dict[str, Dict[str, Any]] = {}

    # ── Decision processing ────────────────────────────────────────────

    def process_oauth_decision(
        self,
        tool_call_id: str,
        decision: Optional[Dict[str, Any]],
        request_context: Optional[Dict[str, Any]],
        toolset: Any = None,
    ) -> Optional[Tuple[str, List[Tool]]]:
        """Try to process a tool approval decision as an OAuth code exchange.

        If the decision contains an OAuth authorization code, exchanges it for
        a token and loads real tools from the MCP server.

        Args:
            tool_call_id: The tool call being approved.
            decision: The structured decision data from the frontend.
            request_context: Request context with user_id.
            toolset: The RemoteMCPToolset to load tools from.

        Returns:
            (toolset_name, tools) on success, None if not an OAuth decision.
        Raises:
            OAuthTokenExchangeError if the code exchange fails.
        """
        oauth_code = parse_oauth_decision(decision)
        if not oauth_code:
            return None

        # Exchange auth code for token
        success = self._try_exchange(tool_call_id, oauth_code, request_context)
        if not success:
            raise OAuthTokenExchangeError(0, "OAuth code exchange failed")

        # Load real tools now that we have a token
        user_id = _get_token_manager().require_user_id(request_context)
        if toolset:
            tools = self.load_tools_for_user(user_id, toolset, request_context)
            return (toolset.name, tools)

        return None

    @staticmethod
    def _try_exchange(
        tool_call_id: str,
        oauth_code: OAuthDecisionCode,
        request_context: Optional[Dict[str, Any]],
    ) -> bool:
        """Exchange an OAuth authorization code for tokens. Returns True on success."""
        try:
            _get_exchange_manager().complete_exchange(tool_call_id, oauth_code, request_context)
            return True
        except Exception as e:
            logger.error("Failed to process OAuth decision: %s", e, exc_info=True)
            return False

    # ── Tool loading and storage ───────────────────────────────────────

    def load_tools_for_user(
        self,
        user_id: str,
        toolset: Any,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> List[Tool]:
        """Load real OAuth tools from an MCP server and store per user.

        Single entry point for all OAuth tool loading — called from:
        - Startup preload (token exists in DB or disk)
        - OAuth callback (frontend browser flow)
        - process_oauth_decision (after code exchange)

        Returns the loaded tools, or empty list on failure.
        """
        try:
            tools = toolset._load_remote_tools(request_context)
            if tools:
                self.store_user_tools(user_id, toolset.name, tools)
                logger.info(
                    "Loaded %d OAuth tools for user %s on toolset %s",
                    len(tools), user_id[:6] if user_id else user_id, toolset.name,
                )
            return tools
        except Exception as e:
            if self._is_auth_error(e):
                logger.warning(
                    "OAuth credentials expired for user %s on toolset %s — removing cached token",
                    user_id, toolset.name,
                )
                self._evict_expired_token(user_id, toolset)
            else:
                logger.warning(
                    "Failed to load OAuth tools for user %s on toolset %s: %s",
                    user_id, toolset.name, self._extract_error_message(e),
                )
            return []

    def store_user_tools(self, user_id: str, toolset_name: str, tools: List[Tool]) -> None:
        """Store discovered OAuth tools for a user and register in tool_to_toolset."""
        with self._lock:
            if user_id not in self._user_tools:
                self._user_tools[user_id] = {}
            self._user_tools[user_id][toolset_name] = tools
        # Register per-user so get_toolset_name works for OAuth tools
        if user_id not in self._user_tool_to_toolset:
            self._user_tool_to_toolset[user_id] = {}
        for tool in tools:
            if hasattr(tool, "toolset"):
                self._user_tool_to_toolset[user_id][tool.name] = tool.toolset

    # ── Tool resolution ────────────────────────────────────────────────

    def resolve_tools(self, user_id: Optional[str]) -> Optional[Dict[str, List[Tool]]]:
        """Return per-user OAuth tools if available, or None."""
        key = user_id or _get_token_manager().require_user_id(None)
        with self._lock:
            user_tools = self._user_tools.get(key)
            return dict(user_tools) if user_tools else None

    def apply_user_tools(
        self,
        base_tools: list,
        user_id: Optional[str],
        tool_to_toolset: Dict[str, Any],
    ) -> list:
        """Replace _connect placeholders with real OAuth tools for this user.

        If the user has stored OAuth tools for a toolset, removes that toolset's
        placeholder from the list and appends the real tools.
        Returns the original list unchanged if no replacements apply.
        """
        from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

        oauth_replacements = self.resolve_tools(user_id)
        if not oauth_replacements:
            return base_tools

        replaced_toolsets = set(oauth_replacements.keys())
        filtered = []
        for t in base_tools:
            tool_name = t["function"]["name"]
            ts = tool_to_toolset.get(tool_name)
            if isinstance(ts, RemoteMCPToolset) and ts.name in replaced_toolsets:
                continue
            filtered.append(t)

        for user_tools in oauth_replacements.values():
            for tool in user_tools:
                filtered.append(tool.get_openai_format())

        return filtered

    def find_tool(self, name: str, user_id: Optional[str]) -> Optional[Tool]:
        """Look up a tool in the per-user OAuth tools store."""
        key = user_id or _get_token_manager().require_user_id(None)
        with self._lock:
            for toolset_tools in self._user_tools.get(key, {}).values():
                for tool in toolset_tools:
                    if tool.name == name:
                        return tool
        return None

    def get_toolset(self, tool_name: str, user_id: Optional[str]) -> Optional[Any]:
        """Return the toolset for a per-user OAuth tool, or None."""
        key = user_id or _get_token_manager().require_user_id(None)
        return self._user_tool_to_toolset.get(key, {}).get(tool_name)


    # ── Error handling helpers ─────────────────────────────────────────

    @staticmethod
    def _is_auth_error(exc: BaseException) -> bool:
        """Check if an exception (possibly wrapped in ExceptionGroup) is a 401/403."""
        current: BaseException = exc
        while hasattr(current, "exceptions") and current.exceptions:
            current = current.exceptions[0]
        if isinstance(current, httpx.HTTPStatusError):
            return current.response.status_code in (401, 403)
        return "401" in str(current) or "Unauthorized" in str(current)

    @staticmethod
    def _extract_error_message(exc: BaseException) -> str:
        """Extract the root error message from a possibly wrapped exception."""
        current: BaseException = exc
        while hasattr(current, "exceptions") and current.exceptions:
            current = current.exceptions[0]
        return str(current)

    @staticmethod
    def _evict_expired_token(user_id: str, toolset: Any) -> None:
        """Remove an expired/revoked token from cache and disk (CLI only).

        Only deletes from DiskTokenStore — DalTokenStore is shared across
        clusters, so another cluster may have already refreshed the token.
        """
        try:
            mgr = _get_token_manager()
            oauth_config = toolset._mcp_config.oauth
            cache_key = mgr._get_cache_key(oauth_config, {"user_id": user_id})
            mgr._cache.evict(cache_key)
            if isinstance(mgr._store, DiskTokenStore):
                provider_name = oauth_config.authorization_url or "unknown"
                mgr._store.delete_token(provider_name, user_id=user_id)
        except Exception:
            logger.debug("Failed to evict expired token for user %s", user_id, exc_info=True)

"""OAuthTokenManager: single interface for OAuth token lifecycle management.

Manages the 2-tier token storage (cache → persistent store), automatic background
refresh an hour before expiry, and token persistence across clusters.
"""

import hashlib
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

from holmes.common.env_vars import DEFAULT_CLI_USER
from holmes.plugins.toolsets.mcp.oauth_token_store import (
    DalTokenStore,
    DiskTokenStore,
    OAuthTokenCache,
    TokenStore,
)

logger = logging.getLogger(__name__)

# Background sweep interval and lookahead window (configurable via env vars)
OAUTH_CREDENTIAL_INTERVAL_SECONDS = int(os.environ.get("OAUTH_CREDENTIAL_INTERVAL_SECONDS", "3600"))
OAUTH_REFRESH_AHEAD_SECONDS = int(os.environ.get("OAUTH_REFRESH_AHEAD_SECONDS", "3600"))


class OAuthTokenManager:
    """Central manager for OAuth token lifecycle.

    Usage:
        manager = OAuthTokenManager()
        manager.set_dal(dal)  # optional, for DB storage

        # Store a token after initial OAuth flow
        manager.store_token(oauth_config, token_data, request_context)

        # Get a valid access token (checks cache → persistent store, refreshes if needed)
        token = manager.get_access_token(oauth_config, request_context)

        # Shutdown background refresh thread
        manager.shutdown()
    """

    def __init__(self) -> None:
        self._cache = OAuthTokenCache()
        self._store: Optional[TokenStore] = None

        # Background refresh thread
        self._shutdown_event = threading.Event()
        self._refresh_thread = threading.Thread(
            target=self._background_refresh_loop,
            name="oauth-token-refresh",
            daemon=True,
        )
        self._refresh_thread.start()

    # ── Configuration ──────────────────────────────────────────────────

    def enable_disk_store(self) -> None:
        """Enable disk-based token storage. Called in CLI mode only."""
        self._store = DiskTokenStore()

    def set_dal(self, dal: Any) -> None:
        """Switch to DB-backed storage. Called during server startup."""
        if dal and dal.enabled:
            self._store = DalTokenStore(dal)
            logger.info("OAuthTokenManager: DAL initialized for cross-cluster token storage")

    # ── Preload ────────────────────────────────────────────────────────

    def preload_from_store(self) -> None:
        """Preload all tokens from the persistent store into the in-memory cache.

        This ensures the background sweep thread can keep tokens alive by
        refreshing them before expiry. Without preloading, tokens only enter
        the cache on the first user request.
        """
        if not self._store:
            return
        preloaded = self._store.get_all_for_preload()
        if not preloaded:
            return

        loaded = 0
        for entry in preloaded:
            provider_name = entry.get("provider_name", "")
            user_id = entry.get("user_id")
            token_data = entry.get("token_data", {})

            if not token_data.get("access_token"):
                continue

            # Compute remaining TTL from the stored token_expiry
            expires_in = 300  # fallback
            token_expiry_str = entry.get("token_expiry")
            if token_expiry_str:
                try:
                    token_expiry = datetime.fromisoformat(token_expiry_str)
                    remaining = (token_expiry - datetime.now(timezone.utc)).total_seconds()
                    expires_in = max(int(remaining), 1)
                except (ValueError, TypeError):
                    pass

            cache_key = self._build_cache_key(user_id, provider_name)

            self._cache.set(
                cache_key,
                token_data["access_token"],
                expires_in=expires_in,
                refresh_token=token_data.get("refresh_token"),
                refresh_expires_in=token_data.get("refresh_expires_in"),
                token_url=token_data.get("token_url"),
                client_id=token_data.get("client_id"),
                authorization_url=provider_name,
                user_id=user_id if user_id != DEFAULT_CLI_USER else None,
            )
            loaded += 1

        if loaded:
            logger.debug("OAuthTokenManager: preloaded %d token(s) into cache", loaded)

    # ── Public API ─────────────────────────────────────────────────────

    def get_access_token(
        self,
        oauth_config: Any,
        request_context: Optional[Dict[str, Any]] = None,
        disk_key: Optional[str] = None,
        provider_aliases: Optional[list[str]] = None,
    ) -> Optional[str]:
        """Return a valid access token, checking cache → refresh → persistent store.

        Returns None if no token is available anywhere (caller should initiate OAuth flow).
        """
        cache_key = self._get_cache_key(oauth_config, request_context)
        user_id = _get_user_id(request_context)

        # 1. Check in-memory cache
        cached = self._cache.get_valid_access_token(cache_key)
        if cached:
            return cached

        # 2. Try refresh
        refreshed = self._refresh_token(cache_key, oauth_config, user_id=user_id)
        if refreshed:
            return refreshed

        # 3. Check persistent store
        if not self._store:
            return None
        provider_name = oauth_config.authorization_url or (disk_key or "unknown")
        stored_token = self._store.get_token(provider_name, user_id=user_id, provider_aliases=provider_aliases)
        if stored_token and stored_token.get("access_token"):
            self._cache.set(
                cache_key,
                stored_token["access_token"],
                expires_in=stored_token.get("_remaining_ttl", stored_token.get("expires_in", 300)),
                refresh_token=stored_token.get("refresh_token"),
                refresh_expires_in=stored_token.get("refresh_expires_in"),
                token_url=stored_token.get("token_url", oauth_config.token_url),
                client_id=stored_token.get("client_id", oauth_config.client_id),
                authorization_url=oauth_config.authorization_url,
                user_id=user_id,
            )
            logger.debug("OAuthTokenManager: loaded token from store (provider=%s)", oauth_config.authorization_url)
            return stored_token["access_token"]

        return None

    def has_token(
        self,
        oauth_config: Any,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if any token (access or refreshable) is available in cache."""
        cache_key = self._get_cache_key(oauth_config, request_context)
        return self._cache.has_token_or_refresh(cache_key)

    def store_token(
        self,
        oauth_config: Any,
        token_data: Dict[str, Any],
        request_context: Optional[Dict[str, Any]] = None,
        disk_key: Optional[str] = None,
        store_to_disk: bool = False,
    ) -> None:
        """Store a token to cache and persistent store."""
        cache_key = self._get_cache_key(oauth_config, request_context)
        user_id = _get_user_id(request_context)
        access_token = token_data.get("access_token")
        if not access_token:
            logger.warning("OAuthTokenManager: store_token called with no access_token")
            return

        expires_in = token_data.get("expires_in", 300)

        self._cache.set(
            cache_key,
            access_token,
            expires_in=expires_in,
            refresh_token=token_data.get("refresh_token"),
            refresh_expires_in=token_data.get("refresh_expires_in"),
            token_url=oauth_config.token_url,
            client_id=oauth_config.client_id,
            authorization_url=oauth_config.authorization_url,
            user_id=user_id,
        )

        if self._store:
            self._store.store_token(
                oauth_config.authorization_url or "unknown",
                token_data,
                user_id=user_id,
                token_url=oauth_config.token_url,
                client_id=oauth_config.client_id,
            )

        logger.debug(
            "OAuthTokenManager: token stored (cache_key=%s, expires_in=%s, has_refresh=%s)",
            cache_key, expires_in, "refresh_token" in token_data,
        )

    def require_user_id(self, request_context: Optional[Dict[str, Any]]) -> str:
        """Return a user_id, using DEFAULT_CLI_USER in CLI mode or raising in server mode.

        CLI mode (DiskTokenStore / no store): returns DEFAULT_CLI_USER.
        Server mode (DalTokenStore): raises ValueError if user_id is missing.
        """
        user_id = _get_user_id(request_context)
        if user_id:
            return user_id
        if isinstance(self._store, DalTokenStore):
            return None
        return DEFAULT_CLI_USER

    def shutdown(self) -> None:
        """Stop the background refresh thread."""
        self._shutdown_event.set()
        self._refresh_thread.join(timeout=5)

    # ── Cache / key helpers ────────────────────────────────────────────

    @property
    def cache(self) -> OAuthTokenCache:
        return self._cache

    def get_cache_key(self, oauth_config: Any, request_context: Optional[Dict[str, Any]] = None) -> str:
        """Public accessor for the cache key."""
        return self._get_cache_key(oauth_config, request_context)

    def get_cached_user_ids(self, oauth_config: Any) -> list[str]:
        """Return user_ids that have cached tokens for this OAuth provider."""
        idp_key = hashlib.sha256((oauth_config.authorization_url or "").encode()).hexdigest()[:12]
        user_ids = []
        with self._cache._lock:
            for cache_key in self._cache._cache:
                if cache_key.endswith(f":{idp_key}"):
                    user_id = cache_key.rsplit(":", 1)[0]
                    user_ids.append(user_id)
        return user_ids

    # ── Background sweep ────────────────────────────────────────────────

    def _background_refresh_loop(self) -> None:
        """Daemon thread: periodically preload from store and refresh expiring tokens."""
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(timeout=OAUTH_CREDENTIAL_INTERVAL_SECONDS)
            if self._shutdown_event.is_set():
                break
            try:
                self.preload_from_store()
                self._refresh_expiring_tokens()
            except Exception:
                logger.warning("OAuthTokenManager: refresh failed", exc_info=True)

    def _refresh_expiring_tokens(self) -> None:
        """Check all cached tokens and refresh those expiring soon."""
        expiring = self._cache.get_expiring_entries(OAUTH_REFRESH_AHEAD_SECONDS)
        if not expiring:
            return

        logger.debug("OAuthTokenManager: found %d tokens expiring within %ds", len(expiring), OAUTH_REFRESH_AHEAD_SECONDS)

        for cache_key, entry in expiring:
            try:
                self._refresh_single_token(cache_key, entry)
            except Exception:
                logger.warning("OAuthTokenManager: refresh failed for cache_key=%s", cache_key, exc_info=True)

    def _refresh_single_token(self, cache_key: str, entry: Any) -> None:
        """Refresh a single expiring token and push to persistent store."""
        refresh_token = entry.refresh_token
        if refresh_token and entry.token_url:
            result = self._do_refresh_request(entry.token_url, entry.client_id, refresh_token, cache_key)
            if result:
                token_data, _access_token, _expires_in = result
                if self._store:
                    self._store.store_token(
                        entry.authorization_url or "unknown",
                        token_data,
                        user_id=entry.user_id,
                        token_url=entry.token_url,
                        client_id=entry.client_id,
                    )
                logger.info("OAuthTokenManager: sweep refreshed token (cache_key=%s)", cache_key)
                return

        # No refresh token or refresh failed — try reloading from store
        if not self._store or not entry.authorization_url or not entry.user_id:
            return
        stored = self._store.get_token(entry.authorization_url, user_id=entry.user_id)
        if stored and stored.get("access_token"):
            expires_in = stored.get("_remaining_ttl", stored.get("expires_in", 300))
            self._cache.set(
                cache_key,
                stored["access_token"],
                expires_in=expires_in,
                refresh_token=stored.get("refresh_token"),
                refresh_expires_in=stored.get("refresh_expires_in"),
                token_url=entry.token_url,
                client_id=entry.client_id,
                authorization_url=entry.authorization_url,
                user_id=entry.user_id,
            )
            logger.info("OAuthTokenManager: sweep reloaded token from store (cache_key=%s)", cache_key)

    # ── Synchronous (reactive) refresh ─────────────────────────────────

    def _refresh_token(self, cache_key: str, oauth_config: Any, user_id: Optional[str] = None) -> Optional[str]:
        """Attempt to refresh an expired access token using the cached refresh token."""
        refresh_token = self._cache.get_refresh_token(cache_key)
        if not refresh_token:
            return None

        try:
            result = self._do_refresh_request(oauth_config.token_url, oauth_config.client_id, refresh_token, cache_key)
            if not result:
                self._cache.evict(cache_key)
                return None

            token_data, access_token, expires_in = result
            if self._store:
                self._store.store_token(
                    oauth_config.authorization_url or "unknown",
                    token_data,
                    user_id=user_id,
                    token_url=oauth_config.token_url,
                    client_id=oauth_config.client_id,
                )
            return access_token
        except Exception:
            logger.warning("OAuthTokenManager: refresh failed (cache_key=%s)", cache_key, exc_info=True)
            self._cache.evict(cache_key)
            return None

    def _do_refresh_request(
        self, token_url: str, client_id: Optional[str], refresh_token: str, cache_key: str,
    ) -> Optional[Tuple[Dict[str, Any], str, int]]:
        """POST to token endpoint, validate response, update cache.

        Returns (token_data, access_token, expires_in) on success, None on failure.
        """
        logger.debug("OAuthTokenManager: refreshing token at %s (cache_key=%s)", token_url, cache_key)
        response = httpx.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if response.status_code != 200:
            logger.warning("OAuthTokenManager: refresh HTTP %d (cache_key=%s)", response.status_code, cache_key)
            return None

        token_data = response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            logger.warning("OAuthTokenManager: refresh response missing access_token (cache_key=%s)", cache_key)
            return None

        expires_in = token_data.get("expires_in", 300)
        self._cache.set(
            cache_key,
            access_token,
            expires_in=expires_in,
            refresh_token=token_data.get("refresh_token", refresh_token),
            refresh_expires_in=token_data.get("refresh_expires_in"),
        )
        logger.debug("OAuthTokenManager: token refreshed (cache_key=%s, expires_in=%s)", cache_key, expires_in)
        return token_data, access_token, expires_in

    # ── Key helpers ────────────────────────────────────────────────────

    def _get_cache_key(self, oauth_config: Any, request_context: Optional[Dict[str, Any]]) -> str:
        user_id = _get_user_id(request_context) or DEFAULT_CLI_USER
        return self._build_cache_key(user_id, oauth_config.authorization_url)

    @staticmethod
    def _build_cache_key(user_id: str, authorization_url: str) -> str:
        """Build a cache key from user_id and authorization_url."""
        idp_key = hashlib.sha256((authorization_url or "").encode()).hexdigest()[:12]
        return f"{user_id}:{idp_key}"

    @staticmethod
    def _default_disk_key(oauth_config: Any) -> str:
        """Derive a disk store key from the oauth config."""
        return oauth_config.authorization_url or "unknown"


# ── Module-level helpers ──────────────────────────────────────────────────


def _get_user_id(request_context: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract user_id from request context."""
    if request_context:
        return request_context.get("user_id")
    return None



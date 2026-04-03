"""
Okta JWT validation for HolmesGPT frontend.

Validates ID tokens issued by Okta using JWKS (JSON Web Key Set).
Keys are fetched from the Okta OpenID Connect discovery endpoint
and cached in memory with a 24-hour TTL.
"""

import logging
import os
import threading
import time

import requests
from fastapi import HTTPException
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

OKTA_ISSUER = os.environ.get("OKTA_ISSUER", "")
OKTA_CLIENT_ID = os.environ.get("OKTA_CLIENT_ID", "")
OKTA_REQUIRED_GROUP = os.environ.get("OKTA_REQUIRED_GROUP", "")

# JWKS cache TTL: 24 hours
_JWKS_CACHE_TTL = 86400


class JWKSClient:
    """Fetches and caches JWKS keys from the Okta discovery endpoint."""

    def __init__(self, issuer: str):
        self._issuer = issuer
        self._keys: list[dict] = []
        self._fetched_at: float = 0
        self._lock = threading.Lock()
        self._jwks_uri: str = ""

    def _discover_jwks_uri(self) -> str:
        """Fetch the JWKS URI from the OpenID Connect discovery document."""
        if self._jwks_uri:
            return self._jwks_uri
        discovery_url = f"{self._issuer}/.well-known/openid-configuration"
        resp = requests.get(discovery_url, timeout=10)
        resp.raise_for_status()
        self._jwks_uri = resp.json()["jwks_uri"]
        return self._jwks_uri

    def get_keys(self) -> list[dict]:
        """Return cached JWKS keys, refreshing if stale."""
        now = time.time()
        if self._keys and (now - self._fetched_at) < _JWKS_CACHE_TTL:
            return self._keys

        with self._lock:
            # Double-check after acquiring lock
            if self._keys and (time.time() - self._fetched_at) < _JWKS_CACHE_TTL:
                return self._keys

            try:
                jwks_uri = self._discover_jwks_uri()
                resp = requests.get(jwks_uri, timeout=10)
                resp.raise_for_status()
                self._keys = resp.json().get("keys", [])
                self._fetched_at = time.time()
                logger.info("Refreshed JWKS keys from %s (%d keys)", jwks_uri, len(self._keys))
            except Exception:
                logger.exception("Failed to fetch JWKS keys")
                if not self._keys:
                    raise
                # Use stale keys if refresh fails
                logger.warning("Using stale JWKS keys (age: %.0fs)", now - self._fetched_at)

            return self._keys

    def force_refresh(self) -> None:
        """Force a JWKS key refresh (e.g., after a key-not-found error)."""
        self._fetched_at = 0
        self.get_keys()


# Module-level singleton -- initialized lazily
_jwks_client: JWKSClient | None = None


def _get_jwks_client() -> JWKSClient:
    global _jwks_client
    if _jwks_client is None:
        if not OKTA_ISSUER:
            raise HTTPException(
                status_code=500,
                detail="OKTA_ISSUER environment variable not set",
            )
        _jwks_client = JWKSClient(OKTA_ISSUER)
    return _jwks_client


def validate_okta_token(token: str, issuer: str = "", client_id: str = "") -> dict:
    """
    Validate an Okta ID token and return its claims.

    Args:
        token: The raw JWT string
        issuer: Expected issuer (defaults to OKTA_ISSUER env var)
        client_id: Expected audience (defaults to OKTA_CLIENT_ID env var)

    Returns:
        Dict with claims: sub, email, name, groups

    Raises:
        HTTPException(401) on any validation failure
    """
    iss = issuer or OKTA_ISSUER
    aud = client_id or OKTA_CLIENT_ID

    if not iss or not aud:
        raise HTTPException(
            status_code=500,
            detail="Okta configuration missing: OKTA_ISSUER and OKTA_CLIENT_ID required",
        )

    client = _get_jwks_client()
    keys = client.get_keys()

    try:
        claims = jwt.decode(
            token,
            {"keys": keys},
            algorithms=["RS256"],
            audience=aud,
            issuer=iss,
            options={
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_at_hash": False,
            },
        )
    except JWTError as e:
        error_str = str(e)
        # If the key wasn't found, try refreshing JWKS (Okta may have rotated keys)
        if "signature" in error_str.lower() or "key" in error_str.lower():
            logger.info("JWT validation failed, refreshing JWKS keys and retrying")
            client.force_refresh()
            keys = client.get_keys()
            try:
                claims = jwt.decode(
                    token,
                    {"keys": keys},
                    algorithms=["RS256"],
                    audience=aud,
                    issuer=iss,
                    options={
                        "verify_aud": True,
                        "verify_iss": True,
                        "verify_exp": True,
                        "verify_iat": True,
                    },
                )
            except JWTError:
                logger.warning("JWT validation failed after JWKS refresh: %s", e)
                raise HTTPException(status_code=401, detail="Invalid token")
        else:
            logger.warning("JWT validation failed: %s", e)
            raise HTTPException(status_code=401, detail="Invalid token")

    # Group membership check — skipped if OKTA_REQUIRED_GROUP is empty or groups scope not configured
    groups = claims.get("groups", [])
    if OKTA_REQUIRED_GROUP and groups and OKTA_REQUIRED_GROUP not in groups:
        logger.warning(
            "User %s not in required group '%s' (groups: %s)",
            claims.get("email", "unknown"),
            OKTA_REQUIRED_GROUP,
            groups,
        )
        raise HTTPException(
            status_code=403,
            detail=f"User is not a member of the required group: {OKTA_REQUIRED_GROUP}",
        )

    return {
        "sub": claims.get("sub", ""),
        "email": claims.get("email", ""),
        "name": claims.get("name", ""),
        "groups": groups,
    }

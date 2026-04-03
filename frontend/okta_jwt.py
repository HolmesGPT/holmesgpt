"""
Okta JWT validation for HolmesGPT frontend.

Validates ID tokens issued by Okta using JWKS (JSON Web Key Set).
Keys are fetched from the Okta OpenID Connect discovery endpoint
and cached in memory with a 24-hour TTL.

Uses PyJWT (actively maintained) instead of python-jose.
"""

import logging
import os
import threading
import time

import jwt
import requests
from fastapi import HTTPException
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

OKTA_ISSUER = os.environ.get("OKTA_ISSUER", "")
OKTA_CLIENT_ID = os.environ.get("OKTA_CLIENT_ID", "")
OKTA_REQUIRED_GROUP = os.environ.get("OKTA_REQUIRED_GROUP", "")

# JWKS cache TTL: 24 hours
_JWKS_CACHE_TTL = 86400


class CachedJWKSClient:
    """Wraps PyJWKClient with manual cache TTL control and retry on key rotation."""

    def __init__(self, issuer: str):
        self._issuer = issuer
        self._lock = threading.Lock()
        self._jwks_uri: str = ""
        self._client: PyJWKClient | None = None
        self._created_at: float = 0

    def _discover_jwks_uri(self) -> str:
        """Fetch the JWKS URI from the OpenID Connect discovery document."""
        if self._jwks_uri:
            return self._jwks_uri
        discovery_url = f"{self._issuer}/.well-known/openid-configuration"
        resp = requests.get(discovery_url, timeout=10)
        resp.raise_for_status()
        self._jwks_uri = resp.json()["jwks_uri"]
        return self._jwks_uri

    def _get_client(self) -> PyJWKClient:
        """Return a PyJWKClient, recreating if cache is stale."""
        now = time.time()
        if self._client and (now - self._created_at) < _JWKS_CACHE_TTL:
            return self._client

        with self._lock:
            if self._client and (time.time() - self._created_at) < _JWKS_CACHE_TTL:
                return self._client

            try:
                jwks_uri = self._discover_jwks_uri()
                self._client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=_JWKS_CACHE_TTL)
                self._created_at = time.time()
                logger.info("Created JWKS client for %s", jwks_uri)
            except Exception:
                logger.exception("Failed to create JWKS client")
                if not self._client:
                    raise

            return self._client

    def get_signing_key(self, token: str):
        """Get the signing key for a token, with retry on cache miss."""
        client = self._get_client()
        try:
            return client.get_signing_key_from_jwt(token)
        except Exception:
            # Key not found — might be rotated, force refresh
            logger.info("Signing key not found, refreshing JWKS client")
            with self._lock:
                jwks_uri = self._discover_jwks_uri()
                self._client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=_JWKS_CACHE_TTL)
                self._created_at = time.time()
            return self._client.get_signing_key_from_jwt(token)


# Module-level singleton -- initialized lazily
_jwks_client: CachedJWKSClient | None = None


def _get_jwks_client() -> CachedJWKSClient:
    global _jwks_client
    if _jwks_client is None:
        if not OKTA_ISSUER:
            raise HTTPException(
                status_code=500,
                detail="OKTA_ISSUER environment variable not set",
            )
        _jwks_client = CachedJWKSClient(OKTA_ISSUER)
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

    try:
        signing_key = client.get_signing_key(token)
        claims = jwt.decode(
            token,
            signing_key.key,
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
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token has expired")
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidAudienceError:
        logger.warning("JWT audience mismatch")
        raise HTTPException(status_code=401, detail="Invalid token audience")
    except jwt.InvalidIssuerError:
        logger.warning("JWT issuer mismatch")
        raise HTTPException(status_code=401, detail="Invalid token issuer")
    except jwt.PyJWTError as e:
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

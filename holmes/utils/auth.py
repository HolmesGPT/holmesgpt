from typing import Callable

from fastapi import HTTPException, Request

from holmes.common import env_vars

OPT_IN_API_FLAGS = (
    "ENABLE_CHAT_API",
    "ENABLE_CHECKS_API",
    "ENABLE_OAUTH_API",
    "ENABLE_INFO_API",
)

AUTH_EXEMPT_PATHS = {"/healthz", "/readyz"}


def extract_api_key(request: Request) -> str:
    """Extract API key from X-API-Key header or Authorization Bearer token.

    Checks X-API-Key first; falls back to Authorization header with
    case-insensitive "Bearer " prefix per RFC 7235.
    """
    key = request.headers.get("X-API-Key", "")
    if key:
        return key
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def require_api_enabled(flag: str) -> Callable[[], None]:
    """FastAPI dependency answering 404 unless the opt-in API `flag` is on."""
    if flag not in OPT_IN_API_FLAGS:
        raise ValueError(f"Unknown API flag: {flag}")

    def check() -> None:
        if not getattr(env_vars, flag):
            raise HTTPException(
                status_code=404,
                detail=f"This API is disabled. Set {flag}=true on the Holmes server to enable it.",
            )

    return check

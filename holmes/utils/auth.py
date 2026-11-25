from typing import Callable, Optional

from fastapi import HTTPException, Request
from starlette._utils import get_route_path

from holmes.common import env_vars

# Path prefix of each opt-in API -> the env_vars flag that turns it on.
OPT_IN_API_PATHS = {
    "/api/chat": "ENABLE_CHAT_API",
    "/api/checks": "ENABLE_CHECKS_API",
    "/api/oauth/callback": "ENABLE_OAUTH_API",
    "/api/info": "ENABLE_INFO_API",
}
OPT_IN_API_FLAGS = tuple(OPT_IN_API_PATHS.values())

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


def disabled_api_detail(flag: str) -> str:
    return f"This API is disabled. Set {flag}=true on the Holmes server to enable it."


def disabled_api_flag(scope: dict) -> Optional[str]:
    """The flag that would enable the request's path, if it belongs to an opt-in API that is off."""
    # The path the router matches: without the ASGI root_path a proxy prefix adds.
    path = get_route_path(scope)
    for prefix, flag in OPT_IN_API_PATHS.items():
        if (path == prefix or path.startswith(prefix + "/")) and not getattr(env_vars, flag):
            return flag
    return None


def require_api_enabled(flag: str) -> Callable[[], None]:
    """Route dependency that holds even if a request reaches the route past the path gate."""
    if flag not in OPT_IN_API_FLAGS:
        raise ValueError(f"Unknown API flag: {flag}")

    def check() -> None:
        if not getattr(env_vars, flag):
            raise HTTPException(status_code=404, detail=disabled_api_detail(flag))

    return check

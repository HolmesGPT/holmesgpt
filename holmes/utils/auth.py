from fastapi import Request

AUTH_EXEMPT_PATHS = {"/healthz", "/readyz"}

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


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


def validate_auth_config(
    api_key: str,
    host: str,
    unsafe_allow_unauthenticated: bool,
) -> None:
    """Fail closed: refuse to serve a privileged, unauthenticated API beyond loopback.

    Non-health endpoints execute LLM-driven infrastructure tools, so binding
    them to a non-loopback address without an API key is an open privileged
    API (CWE-306). Raises ValueError unless at least one of these holds:
    HOLMES_API_KEY is set, the bind address is loopback, or the operator
    explicitly opted out via HOLMES_UNSAFE_ALLOW_UNAUTHENTICATED=true.
    """
    if api_key or unsafe_allow_unauthenticated:
        return
    if host.strip().lower() in LOOPBACK_HOSTS:
        return
    raise ValueError(
        f"Refusing to start: HOLMES_API_KEY is not set and HOLMES_HOST={host!r} is not "
        "a loopback address, which would expose privileged API endpoints (chat, checks, "
        "investigations) to unauthenticated callers. Fix one of the following: "
        "(1) set HOLMES_API_KEY to a secret value and send it as an X-API-Key header "
        "(the official Helm chart does this automatically), "
        "(2) set HOLMES_HOST=127.0.0.1 to serve on loopback only, or "
        "(3) set HOLMES_UNSAFE_ALLOW_UNAUTHENTICATED=true if the network already "
        "restricts access to trusted callers."
    )

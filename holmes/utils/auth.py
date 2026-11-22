"""API key authentication for the Holmes HTTP server.

Authentication is on by default. A request passes when it carries one of the
configured API keys, as ``X-API-Key: <key>`` or ``Authorization: Bearer <key>``:

- ``HOLMES_API_KEY``: the key an operator sets for their own clients.
- The default key: generated once by the Helm chart and shared with the
  in-cluster callers the chart deploys (the Robusta runner and the Holmes
  operator). The chart mounts it as a file and sets
  ``HOLMES_DEFAULT_API_KEY_FILE``; ``HOLMES_DEFAULT_API_KEY`` holds it inline
  outside Kubernetes. It is separate from ``HOLMES_API_KEY`` so the chart
  never overrides a user's own key, and both are accepted.

The key file is re-read when it changes, so a rotated Secret reaches a running
server without a restart.

With no key configured, every endpoint except the health probes answers 401.
``HOLMES_AUTH_DISABLED=true`` is the only way to serve the API without a key.
"""

import hmac
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Mapping, MutableMapping, Optional, Tuple

from fastapi import Request
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

# Kubernetes probes. Matched exactly against the raw ASGI path, never against
# request.url (see ApiKeyAuthMiddleware).
AUTH_EXEMPT_PATHS = frozenset({"/healthz", "/readyz"})

USER_API_KEY_ENV_VAR = "HOLMES_API_KEY"
DEFAULT_API_KEY_ENV_VAR = "HOLMES_DEFAULT_API_KEY"
DEFAULT_API_KEY_FILE_ENV_VAR = "HOLMES_DEFAULT_API_KEY_FILE"
API_KEY_ENV_VARS = (USER_API_KEY_ENV_VAR, DEFAULT_API_KEY_ENV_VAR)
AUTH_DISABLED_ENV_VAR = "HOLMES_AUTH_DISABLED"

UNAUTHORIZED_DETAIL = (
    "Invalid or missing API key. "
    "See https://holmesgpt.dev/reference/http-api/#authentication"
)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


@dataclass(frozen=True)
class ApiAuthSettings:
    """Resolved authentication settings of the server.

    ``keys`` maps the env var a key came from to the key, so logs can name the
    source without printing the value.
    """

    keys: Dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    # HOLMES_DEFAULT_API_KEY_FILE as configured, even while the file is empty.
    key_file: str = ""

    @property
    def enforced(self) -> bool:
        return not self.disabled

    def startup_message(self) -> tuple[int, str]:
        """The (log level, message) the server logs once at startup."""
        if self.disabled:
            return (
                logging.WARNING,
                f"API authentication is disabled ({AUTH_DISABLED_ENV_VAR}=true): every "
                "endpoint is open to anyone who can reach this server",
            )
        if not self.keys:
            waiting = (
                f" The default key file {self.key_file} is missing or empty; it is "
                "picked up as soon as it appears."
                if self.key_file
                else ""
            )
            return (
                logging.WARNING,
                "API authentication is on and no API key is configured: every endpoint "
                f"except {', '.join(sorted(AUTH_EXEMPT_PATHS))} answers 401. Set "
                f"{USER_API_KEY_ENV_VAR}, or {AUTH_DISABLED_ENV_VAR}=true to serve the "
                "API without authentication." + waiting,
            )
        return (
            logging.INFO,
            "API key authentication enabled, accepting keys from: "
            + ", ".join(self.keys),
        )


def _read_key_file(path: str) -> str:
    """The key in *path*, or "" when the file is missing or unreadable.

    A missing file is normal: the chart mounts the Secret with optional: true,
    and the Secret appears once the bootstrap hook has created it.
    """
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except OSError as e:
        logging.warning("Cannot read the default API key file %s: %s", path, e)
        return ""


def load_api_auth_settings(environ: Mapping[str, str] = os.environ) -> ApiAuthSettings:
    """Read the authentication settings from the environment and the key file.

    Raises ValueError when HOLMES_AUTH_DISABLED is not "true" or "false", so a
    typo cannot silently open (or close) the API.
    """
    keys = {}
    for name in API_KEY_ENV_VARS:
        value = environ.get(name, "").strip()
        if value:
            keys[name] = value

    key_file = environ.get(DEFAULT_API_KEY_FILE_ENV_VAR, "").strip()
    if key_file:
        value = _read_key_file(key_file)
        if value:
            keys[DEFAULT_API_KEY_FILE_ENV_VAR] = value

    raw_disabled = environ.get(AUTH_DISABLED_ENV_VAR, "").strip().lower()
    if raw_disabled not in ("", "true", "false"):
        raise ValueError(
            f"{AUTH_DISABLED_ENV_VAR} must be 'true' or 'false', got {raw_disabled!r}"
        )
    return ApiAuthSettings(
        keys=keys, disabled=raw_disabled == "true", key_file=key_file
    )


def _file_signature(path: str) -> Optional[Tuple[int, int, int]]:
    try:
        st = os.stat(path)  # follows the symlink kubelet swaps on update
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


class ApiAuthSettingsLoader:
    """The server's current ApiAuthSettings, refreshed when the key file changes.

    Kubelet updates a mounted Secret in place (an atomic symlink swap), so the
    default key file can appear or change while the server runs: on a fresh
    install where the pod started before the hook created the Secret, or after
    the Secret is deleted and recreated to rotate the key. One stat() per
    request detects that; the file is only read when its signature changes.
    """

    def __init__(self, environ: Mapping[str, str] = os.environ):
        self._environ = environ
        self._key_file = environ.get(DEFAULT_API_KEY_FILE_ENV_VAR, "").strip()
        self._lock = threading.Lock()
        self._signature = _file_signature(self._key_file) if self._key_file else None
        self._settings = load_api_auth_settings(environ)

    def get(self) -> ApiAuthSettings:
        if not self._key_file:
            return self._settings
        signature = _file_signature(self._key_file)
        if signature != self._signature:
            with self._lock:
                if signature != self._signature:
                    had_key = DEFAULT_API_KEY_FILE_ENV_VAR in self._settings.keys
                    self._settings = load_api_auth_settings(self._environ)
                    self._signature = signature
                    has_key = DEFAULT_API_KEY_FILE_ENV_VAR in self._settings.keys
                    logging.info(
                        "Default API key file %s changed: key %s",
                        self._key_file,
                        "reloaded" if has_key else ("removed" if had_key else "still empty"),
                    )
        return self._settings


def _api_key_from_headers(headers: Headers) -> str:
    key = headers.get("X-API-Key", "")
    if key:
        return key
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def extract_api_key(request: Request) -> str:
    """Extract API key from X-API-Key header or Authorization Bearer token.

    Checks X-API-Key first; falls back to Authorization header with
    case-insensitive "Bearer " prefix per RFC 7235.
    """
    return _api_key_from_headers(request.headers)


def is_valid_api_key(presented: str, settings: ApiAuthSettings) -> bool:
    """Constant-time check of a presented key against every configured key."""
    if not presented:
        return False
    presented_bytes = presented.encode()
    matched = False
    # No early return: the time taken does not reveal which key matched.
    for key in settings.keys.values():
        if hmac.compare_digest(presented_bytes, key.encode()):
            matched = True
    return matched


class ApiKeyAuthMiddleware:
    """ASGI middleware that rejects unauthenticated requests before routing.

    Added as the outermost middleware, so it also guards mounted sub-apps
    (/api/checks, /api/admin), the OpenAPI pages and unknown paths.

    The exemption check uses the raw ASGI scope path, not request.url.path:
    the latter is rebuilt from the (attacker-controlled) Host header and can
    be spoofed to bypass the check (CVE-2026-48710 / GHSA-86qp-5c8j-p5mr).

    ``get_settings`` is called per request so tests can swap the settings.
    """

    def __init__(self, app: ASGIApp, get_settings: Callable[[], ApiAuthSettings]):
        self.app = app
        self.get_settings = get_settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        settings = self.get_settings()
        path = scope.get("path", "")
        if settings.disabled or path in AUTH_EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        presented = _api_key_from_headers(Headers(scope=scope))
        if is_valid_api_key(presented, settings):
            await self.app(scope, receive, send)
            return

        if not settings.keys:
            reason = "no API key configured on the server"
        elif presented:
            reason = "invalid API key"
        else:
            reason = "missing API key"
        logging.warning(
            "Unauthorized request: %s %s (%s)",
            scope.get("method", "WEBSOCKET"),
            path,
            reason,
        )

        if scope["type"] == "websocket":
            # Closing before accept makes the server reject the handshake.
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(
            status_code=401,
            content={"detail": UNAUTHORIZED_DETAIL},
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)

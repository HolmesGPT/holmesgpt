"""API key authentication for the Holmes HTTP server.

Authentication is on by default. A request passes when it carries one of the
configured API keys, as ``X-API-Key: <key>`` or ``Authorization: Bearer <key>``:

- ``HOLMES_API_KEY``: the key an operator sets for their own clients.
- The default key: generated once by the Helm chart and shared with the
  in-cluster callers the chart deploys (the Robusta runner and the Holmes
  operator). Holmes only holds its SHA-256: the chart mounts the hash, never
  the key, and sets ``HOLMES_DEFAULT_API_KEY_SHA256_FILE``. So a tool that can
  read files in the Holmes container (the bash toolset, an exec) finds nothing
  it can authenticate with. ``HOLMES_DEFAULT_API_KEY`` holds a default key
  inline outside Kubernetes. Both are separate from ``HOLMES_API_KEY`` so the
  chart never overrides a user's own key, and all are accepted.

Every key is kept and compared as a SHA-256 digest. The hash file is re-read
when it changes, so a rotated Secret reaches a running server without a
restart.

With no key configured, every endpoint except the health probes answers 401.
``HOLMES_AUTH_DISABLED=true`` is the only way to serve the API without a key.
"""

import hashlib
import hmac
import logging
import os
import re
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
DEFAULT_API_KEY_SHA256_FILE_ENV_VAR = "HOLMES_DEFAULT_API_KEY_SHA256_FILE"
API_KEY_ENV_VARS = (USER_API_KEY_ENV_VAR, DEFAULT_API_KEY_ENV_VAR)
AUTH_DISABLED_ENV_VAR = "HOLMES_AUTH_DISABLED"

UNAUTHORIZED_DETAIL = (
    "Invalid or missing API key. "
    "See https://holmesgpt.dev/reference/http-api/#authentication"
)

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def api_key_digest(key: str) -> bytes:
    """The SHA-256 digest Holmes stores and compares for an API key."""
    return hashlib.sha256(key.encode()).digest()


@dataclass(frozen=True)
class ApiAuthSettings:
    """Resolved authentication settings of the server.

    ``key_digests`` maps the source of each accepted key (an env var name) to
    the key's SHA-256 digest, so logs can name the source and the server never
    keeps a plaintext key it does not need.
    """

    key_digests: Dict[str, bytes] = field(default_factory=dict)
    disabled: bool = False
    # HOLMES_DEFAULT_API_KEY_SHA256_FILE as configured, even while it is empty.
    hash_file: str = ""
    # The hash file exists but could not be read (e.g. permissions): retried.
    hash_file_unreadable: bool = False

    @classmethod
    def from_keys(cls, keys: Mapping[str, str], **kwargs: Any) -> "ApiAuthSettings":
        """Settings accepting the given plaintext keys (source name -> key)."""
        return cls(
            key_digests={name: api_key_digest(key) for name, key in keys.items()},
            **kwargs,
        )

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
        if not self.key_digests:
            waiting = (
                f" The default key hash file {self.hash_file} (Secret key "
                "token-sha256) is missing or empty; it is picked up as soon as it "
                "appears."
                if self.hash_file
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
            + ", ".join(self.key_digests),
        )


def _read_hash_file(path: str) -> Tuple[Optional[bytes], bool]:
    """(the digest in *path*, or None; whether the file could be read).

    A missing file is normal: the chart mounts the Secret with optional: true,
    and the Secret appears once the bootstrap hook has created it. A file that
    exists but cannot be read is reported as unreadable, so the loader tries
    again on the next request instead of caching the failure.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, True
    except OSError as e:
        logging.warning("Cannot read the default API key hash file %s: %s", path, e)
        return None, False
    content = raw.decode("ascii", errors="replace").strip().lower()
    if not content:
        return None, True
    if not _SHA256_HEX.fullmatch(content):
        logging.warning(
            "Ignoring the default API key hash file %s: it must hold the key's "
            "SHA-256 as 64 hex characters (the Secret key token-sha256)",
            path,
        )
        return None, True
    return bytes.fromhex(content), True


def load_api_auth_settings(environ: Mapping[str, str] = os.environ) -> ApiAuthSettings:
    """Read the authentication settings from the environment and the hash file.

    Raises ValueError when HOLMES_AUTH_DISABLED is not "true" or "false", so a
    typo cannot silently open (or close) the API.
    """
    digests = {}
    for name in API_KEY_ENV_VARS:
        value = environ.get(name, "").strip()
        if value:
            digests[name] = api_key_digest(value)

    hash_file = environ.get(DEFAULT_API_KEY_SHA256_FILE_ENV_VAR, "").strip()
    readable = True
    if hash_file:
        digest, readable = _read_hash_file(hash_file)
        if digest:
            digests[DEFAULT_API_KEY_SHA256_FILE_ENV_VAR] = digest

    raw_disabled = environ.get(AUTH_DISABLED_ENV_VAR, "").strip().lower()
    if raw_disabled not in ("", "true", "false"):
        raise ValueError(
            f"{AUTH_DISABLED_ENV_VAR} must be 'true' or 'false', got {raw_disabled!r}"
        )
    return ApiAuthSettings(
        key_digests=digests,
        disabled=raw_disabled == "true",
        hash_file=hash_file,
        hash_file_unreadable=not readable,
    )


def _file_signature(path: str) -> Optional[Tuple[int, int, int]]:
    try:
        st = os.stat(path)  # follows the symlink kubelet swaps on update
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


class ApiAuthSettingsLoader:
    """The server's current ApiAuthSettings, refreshed when the hash file changes.

    Kubelet updates a mounted Secret in place (an atomic symlink swap), so the
    default key's hash file can appear or change while the server runs: on a
    fresh install where the pod started before the hook created the Secret, or
    after the Secret is deleted and recreated to rotate the key. One stat() per
    request detects that; the file is only read when its signature changes.
    """

    def __init__(self, environ: Mapping[str, str] = os.environ):
        self._environ = environ
        self._hash_file = environ.get(DEFAULT_API_KEY_SHA256_FILE_ENV_VAR, "").strip()
        self._lock = threading.Lock()
        self._signature = _file_signature(self._hash_file) if self._hash_file else None
        self._settings = load_api_auth_settings(environ)
        if self._settings.hash_file_unreadable:
            # Not cached: the next request reads it again.
            self._signature = None

    def get(self) -> ApiAuthSettings:
        if not self._hash_file:
            return self._settings
        signature = _file_signature(self._hash_file)
        if signature != self._signature:
            with self._lock:
                if signature != self._signature:
                    name = DEFAULT_API_KEY_SHA256_FILE_ENV_VAR
                    had_key = name in self._settings.key_digests
                    self._settings = load_api_auth_settings(self._environ)
                    # An unreadable file is not cached: the next request reads it again.
                    self._signature = None if self._settings.hash_file_unreadable else signature
                    has_key = name in self._settings.key_digests
                    logging.info(
                        "Default API key hash file %s changed: key %s",
                        self._hash_file,
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
    presented_digest = api_key_digest(presented)
    matched = False
    # No early return: the time taken does not reveal which key matched.
    for digest in settings.key_digests.values():
        if hmac.compare_digest(presented_digest, digest):
            matched = True
    return matched


def _carries_holmes_key(name: bytes, value: bytes, settings: ApiAuthSettings) -> bool:
    """Whether one raw header line holds a Holmes key: any X-API-Key, or an
    Authorization: Bearer whose value is an accepted key. Each line is judged
    on its own, so an unrelated Authorization value (also a repeated one) is
    kept for the app."""
    name = name.lower()
    if name == b"x-api-key":
        return True
    if name != b"authorization":
        return False
    auth = value.decode("latin-1")
    return auth.lower().startswith("bearer ") and is_valid_api_key(auth[7:].strip(), settings)


def _authenticate_and_strip_key(scope: Scope, settings: ApiAuthSettings) -> Optional[str]:
    """None when the request carries a valid key, else why it was rejected.

    On success the header that carried the key is removed from the request,
    in place, so nothing downstream sees it: not the toolsets (Holmes passes
    request headers through to them), not error reports (Sentry records
    request headers and the ``scope`` of every frame). The key only lives in
    this function's frame, which has returned before the app runs.
    """
    headers = Headers(scope=scope)
    presented = _api_key_from_headers(headers)
    if is_valid_api_key(presented, settings):
        kept = [(k, v) for k, v in scope["headers"] if not _carries_holmes_key(k, v, settings)]
        if isinstance(scope["headers"], list):
            scope["headers"][:] = kept  # the server may hold the same list
        else:
            scope["headers"] = kept
        return None
    if not settings.key_digests:
        return "no API key configured on the server"
    return "invalid API key" if presented else "missing API key"


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

        reason = _authenticate_and_strip_key(scope, settings)
        if reason is None:
            await self.app(scope, receive, send)
            return

        # %r: the path is caller-controlled; repr keeps it on one log line.
        logging.warning(
            "Unauthorized request: %s %r (%s)",
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

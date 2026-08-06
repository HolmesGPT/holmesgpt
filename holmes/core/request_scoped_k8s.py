"""Request-scoped Kubernetes credentials for the built-in ``kubernetes`` toolset.

When Holmes is embedded in a multi-user UI (e.g. the Headlamp ai-assistant
plugin) behind an OIDC proxy, every request carries the end user's identity as
a bearer/OIDC token (``Authorization: Bearer <id_token>`` and/or an identity
header such as ``X-Auth-Request-Id-Token``). We want ``kubectl`` invocations to
run *as that user* so the API server enforces per-user RBAC, instead of Holmes
using its pod ServiceAccount for everyone.

Design:

* The token is threaded through Holmes' existing ``request_context`` dict, which
  is passed *explicitly* as a function argument all the way from the server
  down to tool invocation (including across the tool-calling ThreadPoolExecutor).
  Because it is an explicit argument -- not global/process state and not a
  ``contextvar`` -- it is safe under concurrency: interleaved requests can never
  observe each other's credentials.
* For each tool invocation we build a **per-invocation** kubeconfig in a private
  temp file and expose it to the subprocess via ``KUBECONFIG`` in that call's
  ``env`` only. We never mutate ``os.environ`` or ``~/.kube/config`` -- doing so
  would let request A's credential bleed into request B (a cross-user
  privilege-escalation bug), which is exactly what this feature avoids.

The feature is opt-in and backward compatible: if ``HOLMES_K8S_AUTH_MODE`` is
not ``request_token`` (the default is ``service_account``) or no token is
present on the request, this module is a no-op and ``kubectl`` keeps using the
pod ServiceAccount / local kubeconfig as before.
"""

from __future__ import annotations

import logging
import os
import stat
import tempfile
from typing import Any, Dict, Optional, Tuple

from requests.structures import CaseInsensitiveDict

logger = logging.getLogger(__name__)

# --- Configuration (env-var driven, so no per-toolset plumbing is required) ---

#: ``service_account`` (default) keeps the legacy behaviour. ``request_token``
#: enables per-request kubeconfig generation from the forwarded OIDC token.
_AUTH_MODE_ENV = "HOLMES_K8S_AUTH_MODE"

#: Comma-separated, ordered list of headers to look for the user's token in.
#: First non-empty match wins. oauth2-proxy injects ``X-Auth-Request-Id-Token``;
#: a plain reverse proxy forwards ``Authorization``.
_TOKEN_HEADERS_ENV = "HOLMES_K8S_REQUEST_TOKEN_HEADERS"
_DEFAULT_TOKEN_HEADERS = "X-Auth-Request-Id-Token,Authorization"

#: Override for the API server URL. Defaults to the in-cluster endpoint derived
#: from KUBERNETES_SERVICE_HOST/PORT, falling back to kubernetes.default.svc.
_API_SERVER_ENV = "HOLMES_K8S_API_SERVER"

#: Path to the cluster CA bundle. Defaults to the projected ServiceAccount CA.
#: When Holmes runs with no ServiceAccount token mounted, mount the
#: ``kube-root-ca.crt`` ConfigMap and point this at it.
_CA_CERT_ENV = "HOLMES_K8S_CA_CERT"
_DEFAULT_CA_CERT = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

#: Opt-in escape hatch for clusters where no CA bundle can be mounted. Disabling
#: verification exposes the user's token to a MITM, so it is never the default.
_INSECURE_ENV = "HOLMES_K8S_INSECURE_SKIP_TLS_VERIFY"

REQUEST_TOKEN_MODE = "request_token"
SERVICE_ACCOUNT_MODE = "service_account"


def is_request_token_mode() -> bool:
    """True when per-request Kubernetes auth is enabled via config."""
    return (
        os.environ.get(_AUTH_MODE_ENV, SERVICE_ACCOUNT_MODE).strip().lower()
        == REQUEST_TOKEN_MODE
    )


def _token_header_names() -> list[str]:
    raw = os.environ.get(_TOKEN_HEADERS_ENV, _DEFAULT_TOKEN_HEADERS)
    return [h.strip() for h in raw.split(",") if h.strip()]


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _strip_bearer(token: str) -> str:
    """Return the bare credential. The k8s API server rejects a token that still
    carries the ``Bearer `` prefix, so it must never end up in the kubeconfig."""
    token = token.strip()
    if token.lower().startswith("bearer "):
        return token[len("bearer ") :].strip()
    return token


def _is_well_formed_token(token: str) -> bool:
    """Reject anything that isn't a single-line printable credential.

    The kubeconfig is rendered as text, so a token containing a newline or
    control character could inject arbitrary YAML (e.g. a rogue ``cluster``
    entry pointing kubectl elsewhere). HTTP forbids such values anyway; this is
    defence in depth against a malformed or malicious upstream proxy.
    """
    return bool(token) and all(ch.isprintable() and ch != " " for ch in token)


def _yaml_single_quote(value: str) -> str:
    """Render ``value`` as a YAML single-quoted scalar ('' escapes a quote)."""
    return "'" + value.replace("'", "''") + "'"


def extract_request_token(request_context: Optional[Dict[str, Any]]) -> Optional[str]:
    """Pull the user's token out of ``request_context['headers']`` using the
    configured, ordered header allow-list. Returns ``None`` if none present."""
    if not request_context:
        return None
    # Header names arrive with inconsistent casing (Starlette lowercases them,
    # server.py preserves original case). Do the lookup case-insensitively.
    headers = CaseInsensitiveDict(request_context.get("headers") or {})
    for name in _token_header_names():
        value = headers.get(name)
        if value:
            token = _strip_bearer(str(value))
            if not token:
                continue
            if not _is_well_formed_token(token):
                logger.warning(
                    "request-scoped k8s auth: ignoring malformed token in header %r",
                    name,
                )
                continue
            return token
    return None


def _api_server_url() -> str:
    override = os.environ.get(_API_SERVER_ENV)
    if override:
        return override
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if host:
        # IPv6 literals must be bracketed for a URL authority.
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"https://{host}:{port}"
    return "https://kubernetes.default.svc"


def _render_kubeconfig(token: str) -> str:
    server = _api_server_url()
    ca_path = os.environ.get(_CA_CERT_ENV, _DEFAULT_CA_CERT)

    cluster_lines = [f"    server: {_yaml_single_quote(server)}"]
    if ca_path and os.path.isfile(ca_path):
        cluster_lines.append(
            f"    certificate-authority: {_yaml_single_quote(ca_path)}"
        )
    elif _bool_env(_INSECURE_ENV):
        logger.warning(
            "request-scoped k8s auth: %s is set; API server certificate will NOT "
            "be verified and the user's token is exposed to interception.",
            _INSECURE_ENV,
        )
        cluster_lines.append("    insecure-skip-tls-verify: true")
    else:
        # Deliberately neither pin a CA nor disable verification: kubectl falls
        # back to the system trust store and fails loudly on an untrusted API
        # server. Silently skipping verification would leak the user's token.
        logger.warning(
            "request-scoped k8s auth: CA bundle %r not found; relying on the "
            "system trust store. Mount the kube-root-ca.crt ConfigMap and set "
            "%s, or set %s=true to accept the risk.",
            ca_path,
            _CA_CERT_ENV,
            _INSECURE_ENV,
        )

    cluster_block = "\n".join(cluster_lines)
    return (
        "apiVersion: v1\n"
        "kind: Config\n"
        "clusters:\n"
        "- name: holmes-request-scoped\n"
        "  cluster:\n"
        f"{cluster_block}\n"
        "users:\n"
        "- name: holmes-request-user\n"
        "  user:\n"
        f"    token: {_yaml_single_quote(token)}\n"
        "contexts:\n"
        "- name: holmes-request-scoped\n"
        "  context:\n"
        "    cluster: holmes-request-scoped\n"
        "    user: holmes-request-user\n"
        "current-context: holmes-request-scoped\n"
    )


def build_request_scoped_env(
    request_context: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Build a per-invocation environment overlay carrying request-scoped
    Kubernetes credentials.

    Returns ``(env_overrides, kubeconfig_path)`` where:

    * ``env_overrides`` is a dict to merge onto ``os.environ`` for *this*
      subprocess call only (currently ``{"KUBECONFIG": <temp path>}``), or
      ``None`` when the feature is disabled / no token is present.
    * ``kubeconfig_path`` is the temp file the caller MUST delete once the
      subprocess has finished, or ``None``.

    The temp kubeconfig is created with ``0600`` permissions and holds the raw
    bearer token, so it must be short-lived and unreadable by other users.
    """
    if not is_request_token_mode():
        return None, None

    token = extract_request_token(request_context)
    if not token:
        # request_token mode but no user token forwarded -- leave kubectl to its
        # default behaviour rather than silently generating a broken kubeconfig.
        return None, None

    fd, path = tempfile.mkstemp(prefix="holmes-kubeconfig-", suffix=".yaml")
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        with os.fdopen(fd, "w") as fh:
            fh.write(_render_kubeconfig(token))
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise

    return {"KUBECONFIG": path}, path


def cleanup_kubeconfig(path: Optional[str]) -> None:
    """Best-effort removal of a per-invocation kubeconfig temp file."""
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.debug("failed to remove request-scoped kubeconfig %s: %s", path, e)

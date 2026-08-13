"""SSRF protection for the internet toolsets.

``fetch_webpage`` (and the Notion variant) let the LLM choose an arbitrary URL.
Because the URL can be dictated by attacker-controlled observability text
(indirect prompt injection), an unguarded ``requests.get`` is a Server-Side
Request Forgery + data-exfiltration primitive: the model can be steered to
``http://169.254.169.254/...`` (cloud metadata), cluster-internal services, or
``http://attacker.tld/?x=<secrets>``.

This module centralises the defense so every caller of ``scrape()`` is protected:

* only ``http``/``https`` schemes are allowed;
* the host is resolved and every resolved address is rejected if it falls in a
  loopback / link-local / private / reserved / multicast / unspecified range
  (unless an operator allowlist explicitly opts the host in);
* the socket connection is pinned to the exact IP that was validated, so a DNS
  rebind between validation and connection cannot swap in an internal address
  while keeping the original hostname for the ``Host`` header, TLS SNI and cert
  verification.
"""

import ipaddress
import socket
from typing import List, Optional, Sequence, Union
from urllib.parse import urlparse

from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.poolmanager import PoolManager

ALLOWED_SCHEMES = frozenset({"http", "https"})

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


class SSRFValidationError(Exception):
    """Raised when a URL is rejected by the SSRF guard."""


def _normalize_ip(ip: IPAddress) -> IPAddress:
    """Unwrap IPv4-mapped / IPv4-compatible IPv6 addresses so the range checks
    below see the underlying v4 address (e.g. ``::ffff:169.254.169.254``)."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        # ::ffff:a.b.c.d style is covered by ipv4_mapped; also handle the
        # deprecated ipv4-compatible form and 6to4/teredo embedded addresses.
        sixtofour = getattr(ip, "sixtofour", None)
        if sixtofour is not None:
            return sixtofour
    return ip


def is_blocked_ip(ip: IPAddress, allow_private_ips: bool = False) -> bool:
    """Return True if ``ip`` is in a range the toolset must not reach.

    Loopback, link-local (incl. the ``169.254.169.254`` cloud-metadata
    endpoint), multicast, reserved and unspecified addresses are always
    blocked. Private/RFC1918 ranges are blocked too unless ``allow_private_ips``
    is set — the connectivity-check toolset legitimately probes internal cluster
    services, so it opts private ranges back in while still blocking metadata and
    loopback."""
    ip = _normalize_ip(ip)
    # Always-blocked ranges (checked before is_private, which is a superset of
    # loopback/link-local, so metadata is never let through by allow_private_ips).
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    if ip.is_private:
        return not allow_private_ips
    return False


def _host_in_allowlist(host: str, allowed_hosts: Sequence[str]) -> bool:
    host = host.lower().rstrip(".")
    for entry in allowed_hosts:
        entry = entry.lower().lstrip(".").rstrip(".")
        if not entry:
            continue
        if host == entry or host.endswith("." + entry):
            return True
    return False


def _resolve_ips(host: str, port: int) -> List[str]:
    """Resolve ``host`` to a de-duplicated list of IP strings.

    IP-literal hosts are validated directly and never resolved, so a hostile
    resolver cannot make ``169.254.169.254`` look like a public address."""
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise SSRFValidationError(f"could not resolve host '{host}': {e}") from e
    # Preserve order while de-duplicating.
    seen: dict[str, None] = {}
    for info in infos:
        seen.setdefault(info[4][0], None)
    ips = list(seen.keys())
    if not ips:
        raise SSRFValidationError(f"host '{host}' did not resolve to any address")
    return ips


def validate_host(
    host: str,
    port: int = 0,
    allowed_hosts: Optional[Sequence[str]] = None,
    block_internal_ips: bool = True,
    allow_private_ips: bool = False,
) -> List[str]:
    """Resolve ``host`` and validate it against the SSRF policy.

    Returns the exact resolved IPs that passed validation — pass the first one
    to the connection so it cannot be rebound to a different address.

    ``allow_private_ips`` keeps RFC1918/private ranges reachable (metadata and
    loopback are still blocked); the connectivity-check toolset uses this so it
    can probe internal cluster services. Raises :class:`SSRFValidationError` if
    the host is rejected.
    """
    if not host:
        raise SSRFValidationError("no host to validate")

    allowed_hosts = [h for h in (allowed_hosts or []) if h]
    if allowed_hosts and not _host_in_allowlist(host, allowed_hosts):
        raise SSRFValidationError(f"host '{host}' is not in the configured allowlist")

    ips = _resolve_ips(host, port)

    # An operator-configured allowlist is an explicit opt-in, so it may point at
    # an internal host on purpose; skip the range block in that case.
    if allowed_hosts:
        return ips

    if block_internal_ips:
        for ip_str in ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                raise SSRFValidationError(
                    f"host '{host}' resolved to an unparseable address '{ip_str}'"
                )
            if is_blocked_ip(ip, allow_private_ips=allow_private_ips):
                raise SSRFValidationError(
                    f"host '{host}' resolves to non-routable/internal address "
                    f"'{ip_str}', which is blocked to prevent SSRF"
                )

    return ips


def validate_url(
    url: str,
    allowed_hosts: Optional[Sequence[str]] = None,
    block_internal_ips: bool = True,
) -> List[str]:
    """Validate ``url`` against the SSRF policy and return its resolved IPs.

    The returned IPs are the exact addresses that passed validation; pass the
    first one to :func:`build_pinned_adapter` so the connection cannot be
    rebound to a different address.

    Raises :class:`SSRFValidationError` if the URL is rejected.
    """
    try:
        parsed = urlparse(url)
    except Exception as e:  # pragma: no cover - urlparse is very lenient
        raise SSRFValidationError(f"invalid URL: {e}") from e

    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SSRFValidationError(
            f"scheme '{parsed.scheme or ''}' is not allowed; only "
            f"{sorted(ALLOWED_SCHEMES)} URLs may be fetched"
        )

    host = parsed.hostname
    if not host:
        raise SSRFValidationError(f"URL '{url}' has no host")

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as e:
        raise SSRFValidationError(f"invalid port in URL '{url}': {e}") from e

    return validate_host(
        host,
        port,
        allowed_hosts=allowed_hosts,
        block_internal_ips=block_internal_ips,
        allow_private_ips=False,
    )


def _pinned_connection_classes(pinned_ip: str):
    """Build urllib3 connection classes that connect to ``pinned_ip`` while
    keeping the original hostname for the ``Host`` header, TLS SNI and cert
    verification."""

    def _new_conn(self):  # type: ignore[no-untyped-def]
        # Swap the address used for the socket connection only, then restore it
        # before TLS wrapping (which reads ``self.host``/SNI) runs.
        real_host = self._dns_host
        self._dns_host = pinned_ip
        try:
            return super(type(self), self)._new_conn()
        finally:
            self._dns_host = real_host

    pinned_http = type("PinnedHTTPConnection", (HTTPConnection,), {"_new_conn": _new_conn})
    pinned_https = type(
        "PinnedHTTPSConnection", (HTTPSConnection,), {"_new_conn": _new_conn}
    )
    return pinned_http, pinned_https


class _PinnedIPAdapter(HTTPAdapter):
    """Requests adapter whose pools connect only to a pre-validated IP."""

    def __init__(self, pinned_ip: str, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pinned_http, pinned_https = _pinned_connection_classes(self._pinned_ip)

        class _PinnedHTTPConnectionPool(HTTPConnectionPool):
            ConnectionCls = pinned_http

        class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
            ConnectionCls = pinned_https

        class _PinnedPoolManager(PoolManager):
            def _new_pool(self, scheme, host, port, request_context=None):
                if scheme == "http":
                    return _PinnedHTTPConnectionPool(
                        host, port, **self.connection_pool_kw
                    )
                if scheme == "https":
                    return _PinnedHTTPSConnectionPool(
                        host, port, **self.connection_pool_kw
                    )
                return super()._new_pool(scheme, host, port, request_context)

        self.poolmanager = _PinnedPoolManager(
            num_pools=connections, maxsize=maxsize, block=block, **pool_kwargs
        )


def build_pinned_adapter(pinned_ip: str) -> HTTPAdapter:
    """Return an adapter that pins all connections to ``pinned_ip``."""
    return _PinnedIPAdapter(pinned_ip)

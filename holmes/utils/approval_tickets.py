"""Signed tool-approval tickets.

A ticket is an HS256-signed JWT that binds an approval to one specific tool
call: its `id`, its function `name`, and the canonical hash of its
`arguments`. The server mints a ticket the moment it marks a tool call as
`pending_approval`; the resume path refuses to execute a `pending_approval`
that doesn't come back with a verifying ticket.

This closes the forgery primitive reported in GHSA-6m4w-cmhp-f95f, where a
client could fabricate a `pending_approval=true` assistant message and
approve it in the same request.

Replay protection is intentionally out of scope here — see the spec at
`specs/step-3-tool-approval-tickets-jwt.md` for the rationale.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import secrets
import time
from typing import Optional

import jwt

TICKET_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days

_SIGNING_KEY_ENV = "HOLMES_APPROVAL_SIGNING_KEY"
_MIN_KEY_BYTES = 32

_REASON_EXPIRED = "expired"
_REASON_INVALID = "invalid"


def _reason_client_for(reason_internal: str) -> str:
    return _REASON_EXPIRED if reason_internal == "expired" else _REASON_INVALID


class ApprovalTicketError(Exception):
    """Raised when an approval ticket fails verification.

    `reason_internal` carries the granular cause for server logs; `reason_client`
    collapses to one of {"expired", "invalid"} for the user-facing event.
    """

    def __init__(self, reason_internal: str):
        super().__init__(reason_internal)
        self.reason_internal = reason_internal
        self.reason_client = _reason_client_for(reason_internal)


def user_message_for_reason(reason_client: str) -> str:
    if reason_client == _REASON_EXPIRED:
        return "Approval expired. Please re-ask the question."
    return "Approval invalid. Please re-ask the question."


def _decode_key_material(raw: str) -> Optional[bytes]:
    """Accept base64 or hex. Return decoded bytes, or None on bad format.

    Hex is tried first because a pure-hex string like "abab...ab" is also
    valid base64 — preferring hex matches operator intent ("I set a hex key").
    """
    candidate = raw.strip()
    if not candidate:
        return None
    try:
        decoded = bytes.fromhex(candidate)
        if len(decoded) >= _MIN_KEY_BYTES:
            return decoded
    except ValueError:
        pass
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(candidate + "=" * (-len(candidate) % 4))
            if len(decoded) >= _MIN_KEY_BYTES:
                return decoded
        except (binascii.Error, ValueError):
            pass
    return None


_cached_signing_key: Optional[bytes] = None
_cached_signing_key_source: Optional[str] = None  # "env" or "ephemeral"


def get_signing_key() -> bytes:
    """Return the process-wide signing key. Memoized after first call.

    Reads `HOLMES_APPROVAL_SIGNING_KEY` (base64 or hex, >=32 bytes decoded).
    Falls back to a per-process random 32-byte key when unset or unparseable.
    """
    global _cached_signing_key, _cached_signing_key_source
    if _cached_signing_key is not None:
        return _cached_signing_key

    raw = os.environ.get(_SIGNING_KEY_ENV, "")
    if raw:
        decoded = _decode_key_material(raw)
        if decoded is not None:
            _cached_signing_key = decoded
            _cached_signing_key_source = "env"
            return _cached_signing_key
        logging.error(
            "%s is set but could not be decoded as base64 or hex with >=%d bytes; "
            "falling back to an ephemeral signing key for this process.",
            _SIGNING_KEY_ENV,
            _MIN_KEY_BYTES,
        )

    _cached_signing_key = secrets.token_bytes(_MIN_KEY_BYTES)
    _cached_signing_key_source = "ephemeral"
    return _cached_signing_key


def signing_key_source() -> str:
    """Return "env" or "ephemeral" after `get_signing_key()` has been called."""
    if _cached_signing_key_source is None:
        get_signing_key()
    assert _cached_signing_key_source is not None
    return _cached_signing_key_source


def canonicalize_args(args_json_string: Optional[str]) -> bytes:
    """Canonicalize a tool-call `arguments` JSON string to a stable byte form.

    Empty / None / unparseable inputs at *mint* time normalize to `{}` so a
    tool call with no arguments still gets a deterministic hash. Verify-time
    callers see `args_mismatch` if the verified-against string doesn't
    canonicalize the same way (handled by `verify_ticket`).
    """
    text = (args_json_string or "").strip()
    if not text:
        parsed = {}
    else:
        parsed = json.loads(text)
    return json.dumps(
        parsed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def args_hash(args_json_string: Optional[str]) -> str:
    return hashlib.sha256(canonicalize_args(args_json_string)).hexdigest()


def mint_ticket(tool_call_id: str, tool_name: str, args_json: Optional[str]) -> str:
    """Mint a signed approval ticket for a pending tool call."""
    now = int(time.time())
    payload = {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "args_hash": args_hash(args_json),
        "iat": now,
        "exp": now + TICKET_TTL_SECONDS,
    }
    return jwt.encode(payload, get_signing_key(), algorithm="HS256")


def verify_ticket(
    ticket: Optional[str],
    tool_call_id: str,
    tool_name: str,
    args_json: Optional[str],
) -> None:
    """Verify a ticket against the current tool call.

    Raises `ApprovalTicketError` on first failure. Success returns None.
    """
    if not ticket:
        raise ApprovalTicketError("missing")

    try:
        claims = jwt.decode(ticket, get_signing_key(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError as e:
        raise ApprovalTicketError("expired") from e
    except jwt.InvalidSignatureError as e:
        raise ApprovalTicketError("bad_signature") from e
    except jwt.DecodeError as e:
        raise ApprovalTicketError("malformed") from e
    except jwt.InvalidTokenError as e:
        raise ApprovalTicketError("malformed") from e

    if claims.get("tool_call_id") != tool_call_id:
        raise ApprovalTicketError("id_mismatch")
    if claims.get("tool_name") != tool_name:
        raise ApprovalTicketError("name_mismatch")

    try:
        expected = args_hash(args_json)
    except (json.JSONDecodeError, TypeError) as e:
        raise ApprovalTicketError("args_mismatch") from e

    if claims.get("args_hash") != expected:
        raise ApprovalTicketError("args_mismatch")

"""Signed tool-approval tokens.

An approval token is an HS256 JWT that binds an approval to one specific tool
call: its `id`, its function `name`, and a stable hash of its
`arguments`. Holmes mints a token when it marks a tool call as
`pending_approval`; the resume path refuses to execute a `pending_approval`
that doesn't come back with a verifying token.

Closes the forgery primitive in GHSA-6m4w-cmhp-f95f.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from typing import Optional

import jwt

TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days

APPROVAL_DOCS_URL = "https://holmesgpt.dev/reference/environment-variables/#holmes_approval_signing_key"
APPROVAL_REJECTION_MESSAGE = (
    "Approval token validation failed. This usually happens after Holmes "
    f"was restarted. See {APPROVAL_DOCS_URL} to configure a persistent signing key."
)


class ApprovalTokenError(Exception):
    """Raised when an approval token fails verification.

    Users always see `APPROVAL_REJECTION_MESSAGE` — never the specific
    `reason` — to avoid leaking which check failed to an attacker probing
    the signing flow. The `reason` attribute is for server-side logs only.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(APPROVAL_REJECTION_MESSAGE)
        self.reason = reason


def _load_signing_key():
    """Return the HMAC signing key.

    PyJWT accepts the key as either `str` or `bytes`, so an operator-supplied
    env var is used as-is — no encoding, no length validation. A weak or
    guessable string silently weakens HMAC; that's an operator-trust call
    (see docs for `HOLMES_APPROVAL_SIGNING_KEY`).
    """
    raw = os.environ.get("HOLMES_APPROVAL_SIGNING_KEY", "").strip()
    if raw:
        logging.info("HOLMES_APPROVAL_SIGNING_KEY loaded")
        return raw
    return secrets.token_bytes(32)


SIGNING_KEY = _load_signing_key()


def args_hash(args_json_string: Optional[str]) -> str:
    """Stable sha256 of a tool_call's `arguments` JSON string.

    `sort_keys=True` makes whitespace and key-order differences between mint
    and verify equivalent. Empty / None / unparseable inputs normalize to {}.
    """
    text = (args_json_string or "").strip()
    parsed = json.loads(text) if text else {}
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def mint_token(tool_call_id: str, tool_name: str, args_json: Optional[str]) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "args_hash": args_hash(args_json),
            "iat": now,
            "exp": now + TOKEN_TTL_SECONDS,
        },
        SIGNING_KEY,
        algorithm="HS256",
    )


def verify_token(
    token: Optional[str],
    tool_call_id: str,
    tool_name: str,
    args_json: Optional[str],
) -> None:
    """Verify a token. Raises `ApprovalTokenError` on any failure."""
    if not token:
        raise ApprovalTokenError("no token provided")
    try:
        claims = jwt.decode(token, SIGNING_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError as exc:
        raise ApprovalTokenError(f"JWT decode failed: {exc}") from exc
    try:
        ok = (
            claims.get("tool_call_id") == tool_call_id
            and claims.get("tool_name") == tool_name
            and claims.get("args_hash") == args_hash(args_json)
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise ApprovalTokenError(f"claim comparison raised: {exc}") from exc
    if not ok:
        raise ApprovalTokenError(
            "claims do not match tool_call_id / tool_name / args_hash"
        )

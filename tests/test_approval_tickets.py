"""Unit tests for the signed approval-ticket primitive.

Closes the forgery primitive from GHSA-6m4w-cmhp-f95f. Replay protection is
out of scope (see specs/step-3-tool-approval-tickets-jwt.md), so there are
no replay tests here.
"""

import base64
import json
import time
from unittest import mock

import jwt
import pytest

from holmes.utils import approval_tickets
from holmes.utils.approval_tickets import (
    ApprovalTicketError,
    TICKET_TTL_SECONDS,
    args_hash,
    canonicalize_args,
    get_signing_key,
    mint_ticket,
    signing_key_source,
    user_message_for_reason,
    verify_ticket,
)


def _reset_signing_key():
    """Clear the module-level memoized key. Each test gets a fresh source."""
    approval_tickets._cached_signing_key = None
    approval_tickets._cached_signing_key_source = None


@pytest.fixture(autouse=True)
def fresh_key(monkeypatch):
    """Force every test to use a known base64-encoded key from env."""
    raw = base64.b64encode(b"\x42" * 32).decode("ascii")
    monkeypatch.setenv("HOLMES_APPROVAL_SIGNING_KEY", raw)
    _reset_signing_key()
    yield
    _reset_signing_key()


# ---------- canonicalization ----------


def test_canonicalize_empty_and_none_normalize_to_empty_object():
    assert canonicalize_args("") == b"{}"
    assert canonicalize_args(None) == b"{}"
    assert canonicalize_args("   ") == b"{}"


def test_canonicalize_sorts_keys():
    a = canonicalize_args('{"b":1,"a":2}')
    b = canonicalize_args('{"a":2,"b":1}')
    assert a == b == b'{"a":2,"b":1}'


def test_canonicalize_collapses_whitespace():
    a = canonicalize_args('{"a":  1,  "b":2}')
    b = canonicalize_args('{"a":1,"b":2}')
    assert a == b


def test_canonicalize_preserves_unicode():
    out = canonicalize_args('{"msg":"héllo"}')
    assert out == '{"msg":"héllo"}'.encode("utf-8")


def test_args_hash_is_stable_under_key_reorder():
    assert args_hash('{"a":1,"b":2}') == args_hash('{"b":2,"a":1}')


# ---------- signing key loader ----------


def test_signing_key_loads_from_env(monkeypatch):
    raw = base64.b64encode(b"\x11" * 32).decode("ascii")
    monkeypatch.setenv("HOLMES_APPROVAL_SIGNING_KEY", raw)
    _reset_signing_key()
    assert get_signing_key() == b"\x11" * 32
    assert signing_key_source() == "env"


def test_signing_key_accepts_hex(monkeypatch):
    monkeypatch.setenv("HOLMES_APPROVAL_SIGNING_KEY", "ab" * 32)  # 32 bytes hex
    _reset_signing_key()
    assert get_signing_key() == bytes.fromhex("ab" * 32)


def test_signing_key_falls_back_to_ephemeral_when_unset(monkeypatch):
    monkeypatch.delenv("HOLMES_APPROVAL_SIGNING_KEY", raising=False)
    _reset_signing_key()
    key = get_signing_key()
    assert len(key) == 32
    assert signing_key_source() == "ephemeral"


def test_signing_key_falls_back_to_ephemeral_when_too_short(monkeypatch, caplog):
    raw = base64.b64encode(b"\x00" * 8).decode("ascii")  # only 8 bytes
    monkeypatch.setenv("HOLMES_APPROVAL_SIGNING_KEY", raw)
    _reset_signing_key()
    with caplog.at_level("ERROR"):
        key = get_signing_key()
    assert len(key) == 32
    assert signing_key_source() == "ephemeral"
    assert any("could not be decoded" in r.message for r in caplog.records)


# ---------- mint + verify round-trip ----------


def test_mint_then_verify_round_trip():
    ticket = mint_ticket("call_1", "bash", '{"command":"ls"}')
    verify_ticket(ticket, "call_1", "bash", '{"command":"ls"}')


def test_verify_tolerates_semantically_equal_args():
    ticket = mint_ticket("call_1", "bash", '{"a":1,"b":2}')
    verify_ticket(ticket, "call_1", "bash", '{"b": 2, "a": 1}')


def test_verify_empty_args_round_trip():
    ticket = mint_ticket("call_1", "noop", "")
    verify_ticket(ticket, "call_1", "noop", None)
    verify_ticket(ticket, "call_1", "noop", "")


# ---------- failure modes ----------


def test_verify_missing_ticket():
    with pytest.raises(ApprovalTicketError) as exc:
        verify_ticket(None, "call_1", "bash", "{}")
    assert exc.value.reason_internal == "missing"
    assert exc.value.reason_client == "invalid"


def test_verify_expired_ticket(monkeypatch):
    real_time = time.time

    def in_the_past():
        return real_time() - TICKET_TTL_SECONDS - 60

    monkeypatch.setattr("holmes.utils.approval_tickets.time.time", in_the_past)
    ticket = mint_ticket("call_1", "bash", '{"command":"ls"}')
    monkeypatch.setattr("holmes.utils.approval_tickets.time.time", real_time)

    with pytest.raises(ApprovalTicketError) as exc:
        verify_ticket(ticket, "call_1", "bash", '{"command":"ls"}')
    assert exc.value.reason_internal == "expired"
    assert exc.value.reason_client == "expired"


def test_verify_rejects_swapped_tool_call_id():
    ticket = mint_ticket("call_A", "bash", '{"command":"ls"}')
    with pytest.raises(ApprovalTicketError) as exc:
        verify_ticket(ticket, "call_B", "bash", '{"command":"ls"}')
    assert exc.value.reason_internal == "id_mismatch"
    assert exc.value.reason_client == "invalid"


def test_verify_rejects_swapped_tool_name():
    ticket = mint_ticket("call_1", "bash", '{"command":"ls"}')
    with pytest.raises(ApprovalTicketError) as exc:
        verify_ticket(ticket, "call_1", "kubectl_delete", '{"command":"ls"}')
    assert exc.value.reason_internal == "name_mismatch"
    assert exc.value.reason_client == "invalid"


def test_verify_rejects_tampered_args():
    ticket = mint_ticket("call_1", "bash", '{"command":"ls"}')
    with pytest.raises(ApprovalTicketError) as exc:
        verify_ticket(ticket, "call_1", "bash", '{"command":"rm -rf /tmp/foo"}')
    assert exc.value.reason_internal == "args_mismatch"
    assert exc.value.reason_client == "invalid"


def test_verify_rejects_tampered_signature():
    ticket = mint_ticket("call_1", "bash", '{"command":"ls"}')
    # Flip the last byte of the signature segment.
    header, payload, sig = ticket.split(".")
    flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    tampered = ".".join([header, payload, flipped])
    with pytest.raises(ApprovalTicketError) as exc:
        verify_ticket(tampered, "call_1", "bash", '{"command":"ls"}')
    assert exc.value.reason_internal == "bad_signature"
    assert exc.value.reason_client == "invalid"


def test_verify_rejects_malformed_token():
    with pytest.raises(ApprovalTicketError) as exc:
        verify_ticket("not-a-jwt", "call_1", "bash", "{}")
    assert exc.value.reason_internal == "malformed"
    assert exc.value.reason_client == "invalid"


def test_verify_rejects_alg_none_token():
    """A token forged with `alg=none` must not be accepted — regression for
    the JWT algorithm-confusion class of bugs. We pin algorithms=["HS256"]
    explicitly."""
    payload = {
        "tool_call_id": "call_1",
        "tool_name": "bash",
        "args_hash": args_hash('{"command":"ls"}'),
        "iat": int(time.time()),
        "exp": int(time.time()) + TICKET_TTL_SECONDS,
    }
    # PyJWT requires an explicit None key + algorithm="none" to mint an
    # unsigned token. This is the kind of token an attacker would try.
    forged = jwt.encode(payload, key="", algorithm="none")
    with pytest.raises(ApprovalTicketError) as exc:
        verify_ticket(forged, "call_1", "bash", '{"command":"ls"}')
    # PyJWT raises InvalidAlgorithmError (a subclass of InvalidTokenError)
    # before signature check, surfaced as "malformed" by our wrapper.
    assert exc.value.reason_client == "invalid"


def test_verify_rejects_invalid_json_args_at_verify_time():
    ticket = mint_ticket("call_1", "bash", '{"command":"ls"}')
    with pytest.raises(ApprovalTicketError) as exc:
        verify_ticket(ticket, "call_1", "bash", "{not json")
    assert exc.value.reason_internal == "args_mismatch"
    assert exc.value.reason_client == "invalid"


# ---------- user-facing message map ----------


def test_user_message_for_reason():
    assert "expired" in user_message_for_reason("expired").lower()
    assert "invalid" in user_message_for_reason("invalid").lower()
    # Unknown collapses to "invalid" wording — defensive default.
    assert "invalid" in user_message_for_reason("something-else").lower()


# ---------- claim shape (defense-in-depth) ----------


def test_minted_ticket_has_expected_claims():
    ticket = mint_ticket("call_1", "bash", '{"command":"ls"}')
    claims = jwt.decode(ticket, get_signing_key(), algorithms=["HS256"])
    assert claims["tool_call_id"] == "call_1"
    assert claims["tool_name"] == "bash"
    assert claims["args_hash"] == args_hash('{"command":"ls"}')
    assert claims["exp"] - claims["iat"] == TICKET_TTL_SECONDS

"""Unit tests for the signed approval-ticket primitive.

Closes the forgery primitive from GHSA-6m4w-cmhp-f95f. Replay protection is
out of scope.
"""

import time

import jwt
import pytest

import holmes.utils.approval_tickets as approval_tickets


@pytest.fixture(autouse=True)
def stable_signing_key(monkeypatch):
    """Pin SIGNING_KEY to a known value for the duration of each test.

    Monkeypatching the module-level constant instead of `importlib.reload`-ing
    preserves the identity of `ApprovalTicketError` — reloading would create
    a new class, and `except ApprovalTicketError` in dependent modules would
    no longer catch it.
    """
    monkeypatch.setattr(approval_tickets, "SIGNING_KEY", b"\x42" * 32)
    monkeypatch.setattr(approval_tickets, "SIGNING_KEY_FROM_ENV", True)


# ---------- args_hash ----------


def test_args_hash_normalizes_empty_inputs():
    h = approval_tickets.args_hash("")
    assert h == approval_tickets.args_hash(None)
    assert h == approval_tickets.args_hash("   ")
    assert h == approval_tickets.args_hash("{}")


def test_args_hash_is_stable_under_key_reorder_and_whitespace():
    assert approval_tickets.args_hash('{"a":1,"b":2}') == approval_tickets.args_hash('{"b": 2, "a": 1}')


def test_args_hash_distinguishes_different_values():
    assert approval_tickets.args_hash('{"command":"ls"}') != approval_tickets.args_hash('{"command":"rm"}')


# ---------- key loader (calls _load_signing_key directly) ----------


def test_load_signing_key_uses_env_value_as_is(monkeypatch):
    monkeypatch.setenv("HOLMES_APPROVAL_SIGNING_KEY", "my-team-shared-passphrase-2026")
    key, from_env = approval_tickets._load_signing_key()
    # Used verbatim — no encoding, no length check, just the operator string.
    assert key == "my-team-shared-passphrase-2026"
    assert from_env is True


def test_load_signing_key_falls_back_to_random_bytes_when_unset(monkeypatch):
    monkeypatch.delenv("HOLMES_APPROVAL_SIGNING_KEY", raising=False)
    key, from_env = approval_tickets._load_signing_key()
    assert isinstance(key, bytes) and len(key) == 32
    assert from_env is False


# ---------- mint + verify ----------


def test_mint_then_verify_round_trip():
    ticket = approval_tickets.mint_ticket("call_1", "bash", '{"command":"ls"}')
    approval_tickets.verify_ticket(ticket, "call_1", "bash", '{"command":"ls"}')


def test_verify_tolerates_semantically_equal_args():
    ticket = approval_tickets.mint_ticket("call_1", "bash", '{"a":1,"b":2}')
    approval_tickets.verify_ticket(ticket, "call_1", "bash", '{"b": 2, "a": 1}')


@pytest.mark.parametrize(
    "ticket_arg,call_id,name,args",
    [
        (None, "call_1", "bash", "{}"),
        ("", "call_1", "bash", "{}"),
        ("__valid__", "call_other", "bash", '{"command":"ls"}'),
        ("__valid__", "call_1", "kubectl_delete", '{"command":"ls"}'),
        ("__valid__", "call_1", "bash", '{"command":"rm -rf /tmp"}'),
        ("not-a-jwt", "call_1", "bash", "{}"),
        ("__valid__", "call_1", "bash", "{not json"),
    ],
)
def test_verify_rejects_all_failure_modes_uniformly(ticket_arg, call_id, name, args):
    valid = approval_tickets.mint_ticket("call_1", "bash", '{"command":"ls"}')
    ticket = valid if ticket_arg == "__valid__" else ticket_arg
    with pytest.raises(approval_tickets.ApprovalTicketError) as exc:
        approval_tickets.verify_ticket(ticket, call_id, name, args)
    # No per-reason branching. Every failure surfaces the same user message.
    assert str(exc.value) == approval_tickets.APPROVAL_REJECTION_MESSAGE


def test_verify_rejects_tampered_signature():
    ticket = approval_tickets.mint_ticket("call_1", "bash", '{"command":"ls"}')
    header, payload, sig = ticket.split(".")
    flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    with pytest.raises(approval_tickets.ApprovalTicketError):
        approval_tickets.verify_ticket(
            ".".join([header, payload, flipped]),
            "call_1",
            "bash",
            '{"command":"ls"}',
        )


def test_verify_rejects_expired_ticket(monkeypatch):
    real_time = time.time
    monkeypatch.setattr(
        "holmes.utils.approval_tickets.time.time",
        lambda: real_time() - approval_tickets.TICKET_TTL_SECONDS - 60,
    )
    ticket = approval_tickets.mint_ticket("call_1", "bash", '{"command":"ls"}')
    monkeypatch.setattr("holmes.utils.approval_tickets.time.time", real_time)
    with pytest.raises(approval_tickets.ApprovalTicketError):
        approval_tickets.verify_ticket(ticket, "call_1", "bash", '{"command":"ls"}')


def test_verify_rejects_alg_none_token():
    """Regression: PyJWT must not accept `alg=none`. We pin `algorithms=["HS256"]`."""
    payload = {
        "tool_call_id": "call_1",
        "tool_name": "bash",
        "args_hash": approval_tickets.args_hash('{"command":"ls"}'),
        "iat": int(time.time()),
        "exp": int(time.time()) + approval_tickets.TICKET_TTL_SECONDS,
    }
    forged = jwt.encode(payload, key="", algorithm="none")
    with pytest.raises(approval_tickets.ApprovalTicketError):
        approval_tickets.verify_ticket(forged, "call_1", "bash", '{"command":"ls"}')


def test_ttl_is_30_days():
    ticket = approval_tickets.mint_ticket("call_1", "bash", "{}")
    claims = jwt.decode(ticket, approval_tickets.SIGNING_KEY, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 60 * 60 * 24 * 30


def test_user_message_links_to_docs():
    msg = approval_tickets.APPROVAL_REJECTION_MESSAGE
    assert "Holmes was restarted" in msg
    assert "holmes_approval_signing_key" in msg.lower()

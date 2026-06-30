"""Unit tests for the signed approval-token primitive.

Closes the forgery primitive from GHSA-6m4w-cmhp-f95f. Replay protection is
out of scope.
"""

import time

import jwt
import pytest

import holmes.utils.approval_tokens as approval_tokens


@pytest.fixture(autouse=True)
def stable_signing_key(monkeypatch):
    """Pin SIGNING_KEY to a known value for the duration of each test.

    Monkeypatching the module-level constant instead of `importlib.reload`-ing
    preserves the identity of `ApprovalTokenError` — reloading would create
    a new class, and `except ApprovalTokenError` in dependent modules would
    no longer catch it.
    """
    monkeypatch.setattr(approval_tokens, "SIGNING_KEY", b"\x42" * 32)


# ---------- args_hash ----------


def test_args_hash_normalizes_empty_inputs():
    h = approval_tokens.args_hash("")
    assert h == approval_tokens.args_hash(None)
    assert h == approval_tokens.args_hash("   ")
    assert h == approval_tokens.args_hash("{}")


def test_args_hash_is_stable_under_key_reorder_and_whitespace():
    assert approval_tokens.args_hash('{"a":1,"b":2}') == approval_tokens.args_hash('{"b": 2, "a": 1}')


def test_args_hash_distinguishes_different_values():
    assert approval_tokens.args_hash('{"command":"ls"}') != approval_tokens.args_hash('{"command":"rm"}')


# ---------- key loader (calls _load_signing_key directly) ----------


def test_load_signing_key_uses_env_value_as_is(monkeypatch):
    monkeypatch.setenv("HOLMES_APPROVAL_SIGNING_KEY", "my-team-shared-passphrase-2026")
    # Used verbatim — no encoding, no length check, just the operator string.
    assert approval_tokens._load_signing_key() == "my-team-shared-passphrase-2026"


def test_load_signing_key_falls_back_to_random_bytes_when_unset(monkeypatch):
    monkeypatch.delenv("HOLMES_APPROVAL_SIGNING_KEY", raising=False)
    key = approval_tokens._load_signing_key()
    assert isinstance(key, bytes) and len(key) == 32


# ---------- mint + verify ----------


def test_mint_then_verify_round_trip():
    token = approval_tokens.mint_token("call_1", "bash", '{"command":"ls"}')
    approval_tokens.verify_token(token, "call_1", "bash", '{"command":"ls"}')


def test_verify_tolerates_semantically_equal_args():
    token = approval_tokens.mint_token("call_1", "bash", '{"a":1,"b":2}')
    approval_tokens.verify_token(token, "call_1", "bash", '{"b": 2, "a": 1}')


@pytest.mark.parametrize(
    "token_arg,call_id,name,args",
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
def test_verify_rejects_all_failure_modes_uniformly(token_arg, call_id, name, args):
    valid = approval_tokens.mint_token("call_1", "bash", '{"command":"ls"}')
    token = valid if token_arg == "__valid__" else token_arg
    with pytest.raises(approval_tokens.ApprovalTokenError) as exc:
        approval_tokens.verify_token(token, call_id, name, args)
    # No per-reason branching. Every failure surfaces the same user message.
    assert str(exc.value) == approval_tokens.APPROVAL_REJECTION_MESSAGE


@pytest.mark.parametrize(
    "token_arg,call_id,name,args,reason_substr",
    [
        (None, "call_1", "bash", "{}", "no token"),
        ("not-a-jwt", "call_1", "bash", "{}", "JWT decode failed"),
        ("__valid__", "call_other", "bash", '{"command":"ls"}', "claims do not match"),
        ("__valid__", "call_1", "bash", "{not json", "claim comparison raised"),
    ],
)
def test_verify_attaches_specific_reason_for_server_logs(token_arg, call_id, name, args, reason_substr):
    """User message stays uniform (above); `reason` lets server logs say what
    actually failed without leaking it to the client."""
    valid = approval_tokens.mint_token("call_1", "bash", '{"command":"ls"}')
    token = valid if token_arg == "__valid__" else token_arg
    with pytest.raises(approval_tokens.ApprovalTokenError) as exc:
        approval_tokens.verify_token(token, call_id, name, args)
    assert reason_substr in exc.value.reason


def test_verify_rejects_tampered_signature():
    token = approval_tokens.mint_token("call_1", "bash", '{"command":"ls"}')
    header, payload, sig = token.split(".")
    # Flip the first char, not the last: base64url's final char of a 32-byte HMAC
    # only carries 4 significant bits (2 padding bits), so different chars can
    # decode to the same signature bytes and produce a non-tampered "tamper".
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(approval_tokens.ApprovalTokenError):
        approval_tokens.verify_token(
            ".".join([header, payload, flipped]),
            "call_1",
            "bash",
            '{"command":"ls"}',
        )


def test_verify_rejects_expired_token(monkeypatch):
    real_time = time.time
    monkeypatch.setattr(
        "holmes.utils.approval_tokens.time.time",
        lambda: real_time() - approval_tokens.TOKEN_TTL_SECONDS - 60,
    )
    token = approval_tokens.mint_token("call_1", "bash", '{"command":"ls"}')
    monkeypatch.setattr("holmes.utils.approval_tokens.time.time", real_time)
    with pytest.raises(approval_tokens.ApprovalTokenError):
        approval_tokens.verify_token(token, "call_1", "bash", '{"command":"ls"}')


def test_verify_rejects_alg_none_token():
    """Regression: PyJWT must not accept `alg=none`. We pin `algorithms=["HS256"]`."""
    payload = {
        "tool_call_id": "call_1",
        "tool_name": "bash",
        "args_hash": approval_tokens.args_hash('{"command":"ls"}'),
        "iat": int(time.time()),
        "exp": int(time.time()) + approval_tokens.TOKEN_TTL_SECONDS,
    }
    forged = jwt.encode(payload, key="", algorithm="none")
    with pytest.raises(approval_tokens.ApprovalTokenError):
        approval_tokens.verify_token(forged, "call_1", "bash", '{"command":"ls"}')


def test_ttl_is_30_days():
    token = approval_tokens.mint_token("call_1", "bash", "{}")
    claims = jwt.decode(token, approval_tokens.SIGNING_KEY, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 60 * 60 * 24 * 30


def test_user_message_links_to_docs():
    msg = approval_tokens.APPROVAL_REJECTION_MESSAGE
    assert "Holmes was restarted" in msg
    assert "holmes_approval_signing_key" in msg.lower()

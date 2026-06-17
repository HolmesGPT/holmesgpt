# Step 3: Signed Approval Tickets (JWT)

**Status:** Draft
**Author:** Roi Glinik
**Related:**
- `specs/tool-approval-tickets.md` — original v1 design (raw HMAC). This spec supersedes it.
- `specs/step-2-remove-edit-command.md` — must land first. Without it, a valid ticket's `args_hash` would still be defeated by post-approval substitution.
- Security report on authenticated approval bypass (GHSA-6m4w-cmhp-f95f).

---

## Problem Statement

Holmes's resume flow accepts `conversation_history` + `tool_decisions` from the client. On resume, `_execute_tool_decisions` scans the supplied messages for assistant tool_calls flagged `pending_approval=true`, matches them to `tool_decisions` by `tool_call_id`, and executes any tool the client says was approved (`holmes/core/tool_calling_llm.py:280-296`).

There is no server-side proof that `pending_approval=true` was ever set by Holmes. A caller who can submit a `tool_decisions` payload (an authenticated user with `HOLMES_API_KEY`, or any party allowed to write into the `Conversations` table in the platform path) can therefore:

1. Fabricate an assistant message containing an arbitrary tool call (e.g. `bash {"command": "..."}`) with `pending_approval=true`.
2. Approve it via `tool_decisions`.
3. Execute the tool with `user_approved=True`, fully bypassing the approval gate.

Step 3 closes this by requiring every approval to be redeemed against a server-issued, signed ticket bound to the tool_call's id, name, and arguments. Step 2 (removing `edit_command`) closes the only known post-gate mutation primitive, so the ticket's `args_hash` actually binds the bytes that get executed.

---

## What this spec solves

- A `pending_approval=true` flag without a valid ticket is rejected. A ticket is valid only if it was signed by Holmes's own key for the exact tool_call_id, tool_name, and arguments that are being redeemed.
- Works for both deployment shapes:
  - Direct Holmes-as-API (`HOLMES_API_KEY` as the only auth).
  - Platform path (UI → Supabase → ConversationsWorker → Holmes), where an authenticated platform user with write access to `Conversations` is the relevant threat.
- Stateless across requests for verification (the JWT is self-contained); a tiny in-process redemption cache handles replay protection.

## What this spec explicitly does not solve

- **Per-user binding of approvals.** A ticket proves "Holmes issued this approval request." It does not prove "user X is the one approving." Holmes has no first-class user identity in direct-deploy mode. Adding `sub` claim binding is a follow-up.
- **Cross-replica shared replay protection.** The redemption cache is per-process. A ticket redeemed on replica A can be replayed against replica B until expiry. Multi-replica deployments should run with a shared signing key + this caveat noted in docs; a shared Redis-backed redemption store is a follow-up.
- **Backwards compat with clients that don't echo the ticket.** This is a security fix. Resume requests with `pending_approval=true` and no valid ticket are rejected, no toggle to disable.

---

## Design

### JWT shape

- **Algorithm:** HS256 (HMAC-SHA256 inside the JWT envelope). Signer and verifier are the same Holmes process, so asymmetric crypto buys nothing.
- **Library:** PyJWT. Already transitively in Holmes's dep tree via litellm. No new dependency.
- **Pin algorithm on verify:** every `jwt.decode` call passes `algorithms=["HS256"]` explicitly. Never trust the token header.

### Claims

```json
{
  "tool_call_id": "call_abc123",
  "tool_name": "bash",
  "args_hash": "<hex sha256 of canonicalized arguments>",
  "iat": 1734567890,
  "exp": 1734567890 + 604800,
  "jti": "<uuid4 hex>"
}
```

| Claim | Why |
|---|---|
| `tool_call_id` | Pins ticket to one specific tool call. Cross-approval (using a ticket from call A to approve call B) is rejected. |
| `tool_name` | Defense in depth — even if `tool_call_id` collides, the function name must match. |
| `args_hash` | Binds ticket to the arguments the LLM originally proposed. With `edit_command` gone (Step 2), this is the actual bytes that will execute — the binding is meaningful. |
| `iat` / `exp` | Standard JWT lifetime. **TTL: 7 days.** Long enough to survive a user walking away from a modal over a weekend; short enough that the leaked-ticket exposure window and the replay-cache memory bound stay reasonable. Hardcoded constant, single place to change. |
| `jti` | UUID4 per ticket. Cache key for single-use enforcement. Could in principle reuse `tool_call_id` for this, but `jti` is the standard JWT claim and PyJWT-aware libraries / tooling recognize it. |

### What's NOT in the claims (and why)

- **No `v` / version field.** PyJWT's header carries `alg`; if we ever need to migrate signing schemes, we ship a `kid` (key ID) header alongside the rotation, which is a standard JWT pattern. Inventing a custom `v` body field would duplicate that.
- **No `conversation_id` / `user_id` / `sub`.** Deferred — see "What this spec explicitly does not solve."

### Signing key

**Source:** env var `HOLMES_APPROVAL_SIGNING_KEY` (base64 or hex, 32+ bytes), with a per-process ephemeral fallback.

- **`HOLMES_APPROVAL_SIGNING_KEY` set:** loaded once at startup. Tickets survive restarts. Multi-replica deployments converge as long as the env var is the same on every replica.
- **Unset (fallback):** Holmes generates 32 bytes from `os.urandom` at startup, holds the key in memory only, and logs a verbose WARNING describing the consequences (see "Startup logging").
  - In-flight approvals do not survive restart (the key that signed them is gone).
  - Multi-replica deployments will fail intermittently because tickets minted by one replica won't verify on another. The WARNING explicitly calls this out.

This matches the original spec — the user explicitly opted to keep both the env var and the fallback rather than fail-fast on unset.

### Replay protection

Even a valid, untampered ticket must not redeem more than once. Without this, an attacker who captures one redeemed ticket (leaked log line, captured request) could replay the same approved tool call multiple times within the 7-day window, doubling side effects.

Implementation: an in-process `TTLCache` of redeemed `jti` values, TTL equal to the ticket TTL.

```python
from cachetools import TTLCache
import threading

_REDEEMED = TTLCache(maxsize=10_000, ttl=TICKET_TTL_SECONDS)
_REDEEMED_LOCK = threading.Lock()

def _check_and_mark_redeemed(jti: str) -> None:
    with _REDEEMED_LOCK:
        if jti in _REDEEMED:
            raise ApprovalTicketError(reason="replayed")
        _REDEEMED[jti] = True
```

- **Atomicity.** Verify-and-consume under a single lock. Holmes runs under uvicorn with async + worker threads; without the lock, two concurrent identical resumes could both pass the "not in cache" check and both execute.
- **Cache bound.** 10,000 entries × ~80 bytes ≈ 800 KB. At 7-day TTL this is the working set of pending approvals over a week. If a deployment ever sustains higher throughput than that, LRU eviction silently re-enables replay for evicted entries — log a WARN when eviction happens so operators see drift before it matters.
- **Multi-replica caveat.** Per-process cache → ticket redeemed on replica A is still replayable on replica B until expiry. Mirrors the signing-key sharing gap. Acceptable for v1; shared Redis-backed store is a follow-up.
- **Restart caveat.** Cache lives in memory; lost on restart. Combined with the ephemeral-key fallback, restart already invalidates all in-flight tickets — cache loss adds nothing. With a persistent `HOLMES_APPROVAL_SIGNING_KEY`, restart briefly re-enables replay until each surviving ticket expires; operators using persistent keys should know.

---

## Argument canonicalization (`args_hash`)

The LLM emits a tool_call's `arguments` as a JSON *string*. `json.dumps` defaults are not whitespace- or key-order-stable, and round-trips through a client can produce different bytes for the same logical arguments. We canonicalize before hashing:

```python
def canonicalize_args(args_json_string: str) -> bytes:
    parsed = json.loads(args_json_string or "{}")
    return json.dumps(
        parsed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

def args_hash(args_json_string: str) -> str:
    return hashlib.sha256(canonicalize_args(args_json_string)).hexdigest()
```

Edge cases (covered by tests):

- `arguments == ""` / `arguments is None` → both normalize to `{}`.
- Invalid JSON → **fail closed**. Verify raises `ApprovalTicketError(reason="invalid")`. No raw-string fallback.
- Unicode in args → preserved via `ensure_ascii=False`.
- Whitespace-only / key-order differences between mint and verify → normalized away.

---

## Flow

### Before (vulnerable)

```
client ──POST /api/chat──> Holmes
                            └── LLM proposes bash tool call
                            └── sets tool_call.pending_approval = True
                            └── streams approval_required event
                            <── stream pauses
client ──POST /api/chat with tool_decisions──> Holmes
                            └── walks conversation_history
                            └── trusts pending_approval (forgeable)
                            └── executes tool
```

### After

```
client ──POST /api/chat──> Holmes
                            └── LLM proposes bash tool call
                            └── server mints JWT ticket bound to
                                  {tool_call_id, tool_name, args_hash, iat, exp, jti}
                            └── attaches ticket to tool_call.approval_ticket
                            └── streams approval_required event (incl. ticket)
                            <── stream pauses
client (echoes conversation_history including approval_ticket)
       ──POST /api/chat──>  Holmes
                            └── walks conversation_history
                            └── for each tool_call with pending_approval=true:
                                  - require approval_ticket present
                                  - jwt.decode(ticket, KEY, algorithms=["HS256"])
                                      (auto-checks signature + exp)
                                  - assert tool_call_id matches
                                  - assert tool_name matches
                                  - assert args_hash matches
                                  - check-and-mark jti as redeemed (replay)
                            └── only then execute tool
```

The client never parses the ticket. The UI/SDK already round-trips the entire assistant message; `approval_ticket` rides along as one more opaque string field.

---

## Verification logic

Verification raises `ApprovalTicketError(reason=...)` on first failure:

1. `jwt.decode(ticket, KEY, algorithms=["HS256"])` — PyJWT enforces signature + `exp` itself, raising `ExpiredSignatureError` / `InvalidSignatureError` / `DecodeError`.
2. `claims["tool_call_id"] == tool_call["id"]`.
3. `claims["tool_name"] == tool_call["function"]["name"]`.
4. `claims["args_hash"] == args_hash(tool_call["function"]["arguments"])`.
5. `jti` not already in the redemption cache; mark redeemed atomically.

Any failure → emit a structured stream event, log at WARNING with the granular reason, do not execute the tool.

---

## Error handling (collapsed reason codes)

Originally the spec had 8 fine-grained reasons (`missing`, `expired`, `bad_signature`, `version_mismatch`, `id_mismatch`, `name_mismatch`, `args_mismatch`, `replayed`, `malformed`). For v1 we collapse to **two client-facing categories** while keeping the granular reason in server logs:

| Client-facing `reason` | What it covers | UX intent |
|---|---|---|
| `expired` | PyJWT `ExpiredSignatureError`. Ticket past its 7-day window. | Benign. "Approval expired, please re-ask." |
| `invalid` | Everything else: missing, bad signature, tampered id/name/args, replayed, malformed payload. | "Approval invalid, please re-ask." |

### Stream event payload

A new event type in `holmes/utils/stream.py`:

```python
class StreamEvents(str, Enum):
    ...
    APPROVAL_REJECTED = "approval_rejected"
```

Payload shape:

```json
{
  "tool_call_id": "call_abc123",
  "tool_name": "bash",
  "reason": "invalid",
  "message": "Approval invalid. Please re-ask the question."
}
```

After emitting the event, the stream closes for that turn. The client must initiate a fresh `/api/chat` to retry. This mirrors the original behavior — we considered surfacing the rejection to the LLM as a tool error to let it re-plan in-stream, but that exposes forgery attempts to the model and adds an attack surface around prompt-injectable retry. Closing the stream is the safer default.

### Server-side logging keeps full detail

Even though the client sees only `expired` / `invalid`, the server log records the original granular cause for ops:

```
WARNING approval ticket rejected: reason_internal=bad_signature reason_client=invalid tool_call_id=<id> tool_name=<name>
WARNING approval ticket rejected: reason_internal=args_mismatch reason_client=invalid tool_call_id=<id> tool_name=<name>
WARNING approval ticket rejected: reason_internal=expired reason_client=expired tool_call_id=<id> tool_name=<name>
```

Operators alerting on forgery indicators can grep `reason_internal=(bad_signature|args_mismatch|id_mismatch|name_mismatch|replayed)` independent of what the UI shows. `expired` stays a benign WARN; the others are the signal that something interesting is happening.

The ticket itself, the signature, and `args_hash` are **never** logged. The `tool_call_id` + `tool_name` + reason are sufficient for triage and safe to ship to any log aggregator.

---

## Implementation

### New module: `holmes/utils/approval_tickets.py`

Exposes:

- `class ApprovalTicketError(Exception)` — `__init__(reason_internal: str)`. Has `reason_internal` (granular: `missing`, `expired`, `bad_signature`, etc.) and `reason_client` (one of `expired`, `invalid`) attributes.
- `TICKET_TTL_SECONDS = 60 * 60 * 24 * 7` — 7 days.
- `get_signing_key() -> bytes` — loads `HOLMES_APPROVAL_SIGNING_KEY` from env (base64 or hex), or generates per-process random key on first call. Eager init at server startup.
- `mint_ticket(tool_call_id: str, tool_name: str, args_json: str) -> str` — returns the encoded JWT.
- `verify_and_consume_ticket(ticket: str, tool_call_id: str, tool_name: str, args_json: str) -> None` — decode + check bindings + atomic redeem. Raises `ApprovalTicketError`. Success is "did not raise."
- `canonicalize_args(args_json_string: str) -> bytes` and `args_hash(args_json_string: str) -> str`.

### Mint site

`holmes/core/tool_calling_llm.py` around line 1420, where `tool_call["pending_approval"] = True` is set. Immediately after:

```python
tool_call["approval_ticket"] = mint_ticket(
    tool_call_id=tool_call["id"],
    tool_name=tool_call["function"]["name"],
    args_json=tool_call["function"].get("arguments", ""),
)
```

The ticket is also included in the `APPROVAL_REQUIRED` stream event (line ~1431) so non-Holmes clients see it.

### Verify site

`_execute_tool_decisions` (`tool_calling_llm.py:280-301`), replace:

```python
if tool_call.get("pending_approval"):
    del tool_call["pending_approval"]
    pending_tool_calls.append(...)
```

with:

```python
if tool_call.get("pending_approval"):
    ticket = tool_call.get("approval_ticket")
    try:
        if not ticket:
            raise ApprovalTicketError(reason_internal="missing")
        verify_and_consume_ticket(
            ticket,
            tool_call_id=tool_call["id"],
            tool_name=tool_call["function"]["name"],
            args_json=tool_call["function"].get("arguments", ""),
        )
    except ApprovalTicketError as e:
        logging.warning(
            "approval ticket rejected: reason_internal=%s reason_client=%s tool_call_id=%s tool_name=%s",
            e.reason_internal, e.reason_client,
            tool_call["id"], tool_call["function"]["name"],
        )
        yield StreamMessage(
            event=StreamEvents.APPROVAL_REJECTED,
            data={
                "tool_call_id": tool_call["id"],
                "tool_name": tool_call["function"]["name"],
                "reason": e.reason_client,
                "message": _user_message_for_reason(e.reason_client),
            },
        )
        return  # close stream for this turn

    del tool_call["pending_approval"]
    del tool_call["approval_ticket"]   # strip from echoed history (used once)
    pending_tool_calls.append(...)
```

Notes:

- **No `HTTPException`.** The verify site runs inside the streaming generator; raising mid-stream confuses clients because HTTP 200 was already committed. Structured `APPROVAL_REJECTED` event is the right channel.
- **Strip ticket after redemption.** Once consumed, the field is removed from the echoed history. Reasons: (a) avoid re-disclosing a tag that's been used, (b) the redemption cache rejects replays anyway.
- **Paired-flag invariant.** `approval_ticket` without `pending_approval` is ignored. `pending_approval` without `approval_ticket` → `reason_internal="missing"`.

### Startup logging

`get_signing_key()` is called once at server boot (eager init) so the operator sees the key source in startup logs.

**Env var set:**
```
INFO  approval_tickets: HOLMES_APPROVAL_SIGNING_KEY loaded from environment (length=32 bytes); approvals will survive restarts.
```

**Env var unset (ephemeral key generated):**
```
WARNING approval_tickets: HOLMES_APPROVAL_SIGNING_KEY is not set. Generated an ephemeral signing key for this process.
WARNING approval_tickets: Consequences:
WARNING approval_tickets:   - In-flight tool approvals are invalidated on every restart (users must re-ask).
WARNING approval_tickets:   - Multi-replica deployments will reject approvals minted by other replicas — load-balanced /api/chat will fail intermittently.
WARNING approval_tickets: To configure a stable shared key:
WARNING approval_tickets:   1. Generate one:  python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
WARNING approval_tickets:   2. Add to Helm values:
WARNING approval_tickets:        additionalEnvVars:
WARNING approval_tickets:          - name: HOLMES_APPROVAL_SIGNING_KEY
WARNING approval_tickets:            valueFrom:
WARNING approval_tickets:              secretKeyRef:
WARNING approval_tickets:                name: holmes-secrets
WARNING approval_tickets:                key: approval-signing-key
WARNING approval_tickets:   3. Create the Kubernetes Secret with the value from step 1.
```

**Multi-replica escalation.** If `HOLMES_REPLICAS_HINT` (set by the Helm chart from `.Values.replicas`) is `> 1` and `HOLMES_APPROVAL_SIGNING_KEY` is unset, escalate the WARN to ERROR. Still not fatal — Holmes starts — but loud enough to show up in any alerting pipeline that watches for ERROR logs.

---

## Tests

### Unit (`tests/test_approval_tickets.py`)

- Mint → verify_and_consume round-trip succeeds.
- Expired ticket → `reason_internal="expired"`, `reason_client="expired"`.
- Swapped `tool_call_id` → `id_mismatch`, `invalid`.
- Swapped `tool_name` → `name_mismatch`, `invalid`.
- Swapped args (semantic difference) → `args_mismatch`, `invalid`.
- Tampered JWT signature → `bad_signature`, `invalid`.
- Wrong/missing `algorithms` parameter at decode → does NOT fall back to `none` (regression test for alg-confusion).
- Malformed token (truncated, not three dots) → `malformed`, `invalid`.
- Same ticket consumed twice → first succeeds, second → `replayed`, `invalid`.
- Concurrent consume (two threads, same ticket) → exactly one succeeds, the other → `replayed`. Verifies the lock.
- Canonicalization: `args=""`, `args=None`, reordered keys, whitespace differences, unicode all round-trip.
- Canonicalization: invalid-JSON args at verify → `args_mismatch`, `invalid` (no raw-string fallback).

### Integration (`/api/chat`)

- **PoC reproducer (must fail closed):** request with a fabricated `pending_approval=true` tool_call and no `approval_ticket`. Assert `APPROVAL_REJECTED` event with `reason="invalid"`, no execution.
- **Tampered-args bypass attempt:** mint a real ticket for `bash {"command": "ls"}`, then resume with same `tool_call_id` but `arguments` changed to `{"command": "rm -rf /tmp/foo"}`. Assert `reason="invalid"` (server log: `reason_internal=args_mismatch`), no execution.
- **Cross-call reuse:** mint a ticket for tool_call A, attach to tool_call B in resume. Assert `reason="invalid"` (server log: `reason_internal=id_mismatch`).
- **Replay:** approve, execute once, send same payload again. Assert second call gets `reason="invalid"` (server log: `reason_internal=replayed`), tool does not run twice.
- **Happy path regression:** full approve flow with a real server-minted ticket round-tripped through `conversation_history` still works.

### Startup log

- Boot without env var → assert the WARNING block is emitted exactly once and contains the configuration instructions.
- Boot with env var → assert INFO line is emitted and WARNING block is absent.
- Boot with `HOLMES_REPLICAS_HINT=3` and no env var → assert WARN block is emitted at ERROR level.

---

## Open questions

- **Collapsed-reason granularity.** Spec proposes two client-facing categories (`expired`, `invalid`). If we later want a third (`replayed` surfaced separately — double-click vs forgery), add it without breaking clients that only recognize the two.
- **Cross-replica replay store.** Per-process cache is good enough for v1. If multi-replica deployments grow, evaluate a Redis-backed or Supabase-backed shared redemption ledger.
- **HKDF from Robusta `signing_key`.** Same open question as the original spec — would let the platform path get a stable key "for free." Deferred since it requires touching OAuth code that already uses the master directly.

---

## Out of scope (handled elsewhere)

- `edit_command` removal. Closed by Step 2; this spec assumes that's landed first so `args_hash` actually binds the bytes that execute.
- Deny-list unconditional gate. Considered as Step 1, dropped — `requires_approval` already filters DENIED commands before they reach a user, so the legitimate approval flow never carries a denied command. With Steps 2 and 3 closing forgery + mutation, there's no remaining path that would slip a denied command past `requires_approval` with `user_approved=True`.
- Parser-correctness audit of `validate_command` (shell metacharacter escapes, encoded forms). Independent.
- Porting the deny model to non-bash writeful toolsets (`kubectl_apply`, `kubectl_delete`, `helm_uninstall`). Independent follow-up.

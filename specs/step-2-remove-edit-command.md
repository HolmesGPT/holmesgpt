# Step 2: Remove `edit_command` from the Tool-Approval Flow

**Status:** Draft
**Author:** Roi Glinik
**Related:**
- `specs/tool-approval-tickets.md` — the parent spec where this was originally a "v1.5 companion change."
- `specs/step-3-tool-approval-tickets-jwt.md` — the signed-ticket work that depends on `edit_command` being gone (so `args_hash` actually binds what runs).

---

## Goal

Remove the ability for the approval UI to mutate a tool call's `command` argument between proposal and execution. After this lands, the only states for a pending tool call are **Approve** (run as proposed) or **Deny**. The command the LLM proposed is the command that runs.

## Why

Today, `ToolApprovalDecision.edit_command` lets the UI replace the `command` field of any pending bash tool call before the executor runs it. The splice happens in `_execute_tool_decisions` (`holmes/core/tool_calling_llm.py:327-345`) **after** any approval-provenance check would have run, and **before** the bash toolset executes — with `user_approved=True` set, which skips the deny list (`bash_toolset.py:234`).

That gives anyone who can submit a `tool_decisions` blob (a legitimate authenticated user, or any party that holds a valid approval ticket once Step 3 lands) the ability to take a benign approved bash command and substitute an arbitrary one — `rm -rf /`, `kubectl delete namespace prod`, service-account token exfil — all while the executor sees `user_approved=True` and runs with no further validation.

The cleanest fix is to remove the substitution mechanism entirely. Edit-then-approve is a niche UX shortcut; the same intent can be expressed by denying and asking the LLM to try again with the user's feedback (`ToolApprovalDecision.feedback` is already plumbed for this and stays untouched by this change).

## Non-goals

- **Not redesigning the approval modal.** We remove exactly the edit affordance (button, inline editor, state). Approve, Deny, Approve-all, Deny-all, OAuth flows, prefix-save — all stay as they are.
- **Not adding a replacement workflow.** Deny + feedback already exists and is unchanged. We're not bolting on a "suggest a fix" input or a "rewrite the LLM's prompt" pathway. If users discover they miss the shortcut, that's a separate follow-up.
- **No deny-list / `_invoke` changes.** The "make deny_list unconditional even when `user_approved=True`" change considered as Step 1 was dropped — `requires_approval` already filters DENIED commands before the user sees them, so a legitimately-approved tool call cannot carry a deny-listed command unless something bypassed `requires_approval`. After this Step 2 closes `edit_command`, and Step 3 closes the forgery primitive, that path is closed at the source.

---

## Surface

### Holmes (BE)

| File | Change |
|---|---|
| `holmes/core/tool_calling_llm.py:327-345` | Delete the entire `if tool_decision.edit_command is not None:` block. The `tool_call.function.arguments` and conversation_history persistence path becomes unconditional pass-through. |
| `holmes/core/models.py:127` | Delete the `edit_command: Optional[str] = None` field from `ToolApprovalDecision`. Old clients that still send the field are unaffected — `ToolApprovalDecision` has no `model_config` override, so Pydantic v2's default `extra="ignore"` silently drops unknown fields at deserialization. No 422, no warning. |
| `tests/test_tool_decision_edit_command.py` | Delete the file. All four tests exercise the splice behavior we're removing. |
| `tests/core/conversations_worker/integration/test_conversation_integration.py:215` | Delete `test_approval_with_edit_command`. |

### Robusta-frontend (FE)

| File | Change |
|---|---|
| `src/components/holmes/tool-approval/ToolApprovalParamInput.vue` | Remove the inline edit affordance — `handleEdit`, `handleSaveEdit`, `handleCancelEdit`, `isEditMode` / `wasEdited` refs, `editedCommand`, the `'edit-mode'` and `'update-params'` emits, and the related template markup (edit button, edit input, save/cancel buttons). The component reduces to a read-only display of `bashCommand` + the prefix section. |
| `src/components/holmes/tool-approval/ToolApprovalModal.vue` | Remove `edit_command?: string` from the `ToolDecision` type (line 19). Remove the `edit_command: existing?.edit_command,` initializer (line 50). Remove the `handleUpdateParams` / `edit-mode` event wiring that exists only to track the edit subcomponent (lines around 96-114). Remove `isAnyToolInEditMode` from the approve button's `:disabled`. **No other modal changes.** |
| `src/components/holmes/tool-approval/OAuthApprovalModal.vue` | Remove `edit_command?: string` from the local `ToolDecision` type (line 18). OAuth modal doesn't use the field functionally — this is a type-shape cleanup that follows from the BE/regular-modal removal. |
| `src/views/holmes/HolmesChat.vue` | Audit and drop any `edit_command` references in the surrounding wiring; expected to be a no-op or near-no-op once the modal stops emitting it. |
| `src/store/conversations/conversations.store.ts` | Drop `edit_command?: string` from the `ToolDecision`-shaped types at lines 764 and 901. |
| `src/store/chat-facade/chat-facade.store.ts` | Drop `edit_command?: string` from the matching type at line 736. |

After the FE PR, no `edit_command` field is ever sent to Holmes from the Robusta UI.

---

## Rollout

**FE first, Holmes second.** This is the explicit chosen ordering.

1. **PR-FE: drop the edit UI + field from outgoing payloads.** Deployed to Robusta SaaS first. From this point on, the UI sends Approve/Deny only.
2. **PR-Holmes: delete the splicer + field + tests.** Deployed at whatever cadence Holmes customer clusters update.

### Why this ordering is safe

- **Old Holmes + new FE:** the FE stops sending `edit_command`. Old Holmes receives requests with the field missing → the old `Optional[str] = None` default handles it → the splice block is skipped → original command runs. Same outcome as a user clicking Approve without editing today. No errors.
- **New Holmes + old FE:** during the rollout window before the FE is deployed, or for any non-Robusta client still sending `edit_command`, the new Holmes accepts the request — Pydantic's default `extra="ignore"` silently drops the unknown field at deserialization. The user might see "edit succeeded" in the old UI and then observe the original (un-edited) command run. That's a UX glitch confined to the rollout window or to third-party clients we don't control. **Not** a security issue — the worst case is "edit silently doesn't apply," which is the safe direction.
- **New Holmes + new FE:** the steady-state. No edit UI, no `edit_command` in flight, no splice, no field on the model.

The accepted trade-off is the "edit silently doesn't apply" window for old-FE clients. We're not flipping anything to `extra="forbid"` because (a) that would break those clients with HTTP 422 instead of a silent no-op, and (b) the user explicitly opted for graceful degradation over strictness.

---

## Tests

### Delete

- `tests/test_tool_decision_edit_command.py` (entire file).
- `tests/core/conversations_worker/integration/test_conversation_integration.py::test_approval_with_edit_command`.

### Add

One small regression test, somewhere under `tests/` (e.g. extend `tests/test_approval_workflow.py` or add `tests/test_edit_command_removed.py`):

- **`test_edit_command_in_payload_is_silently_ignored`** — POST a `tool_decisions` payload containing `{"tool_call_id": "...", "approved": true, "edit_command": "rm -rf /tmp/foo"}` to the resume path, with the assistant tool_call's original `command="ls"`. Assert (a) the request is accepted (no 422, Pydantic dropped the unknown key), (b) the executed command is `ls`, not `rm -rf /tmp/foo`. Covers the backwards-compat promise: old clients still sending the field don't break, and the substitution doesn't apply.

### Regression coverage that stays

- Existing approval-flow tests (`test_approval_workflow.py`, etc.) should pass unchanged. Approve still approves, Deny + feedback still surfaces the feedback to the LLM, prefix-save still works.

---

## Open questions

- None blocking. UX side effect of the rollout window ("my edit didn't apply") is acknowledged and accepted.

---

## Out of scope (tracked elsewhere)

- The forgery primitive — submitting `pending_approval=true` on a fabricated assistant message. Closed by Step 3 (signed approval tickets).
- Other writeful toolsets (`kubectl`, `helm`) inheriting a "deny model" similar to bash. Independent decision, not blocked by this step.
- Parser-correctness audit of `validate_command` (shell metacharacter escapes, encoded forms). Independent audit; this step only removes mutation, not validation correctness.

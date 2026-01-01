# Bash Toolset Specification

Redesigned bash toolset for HolmesGPT with prefix-based command validation and user approval.

## Overview

**Goal:** Enable bash command execution with dynamic whitelisting. The old bash toolset had a rigid allow list; this redesign lets users approve commands on-the-fly and build their trusted command set over time.

**Approach:** Replace the existing `bash` toolset with this new implementation.

**Key features:**
- Pre-configured allow list of safe command prefixes (server) or empty (local CLI)
- Hardcoded blocks for inherently dangerous patterns (sudo, fork bombs)
- User approval for non-whitelisted commands, with option to approve by prefix for future commands
- Support for composed commands (pipes, &&, ||, ;, &)

## User Interaction

When the AI attempts to run a bash command, one of three things happens:

1. **Allowed** - Command matches the allow list → executes immediately, no prompt
2. **Denied** - Command matches deny list or hardcoded blocks → error returned to AI
3. **Needs approval** - Command not in any list → user is prompted (see below)

### Approval Prompt

When a command needs approval:

```
Bash command

  kubectl get pod
  List Kubernetes pods

Do you want to proceed?
  1. Yes
  2. Yes, and don't ask again for `kubectl get` commands
  3. Type here to tell Holmes what to do differently
```

**What each option does:**

| Option | Effect |
|--------|--------|
| **1. Yes** | Approves and executes the command (one-time) |
| **2. Yes, and don't ask again** | Approves, executes, and saves prefixes to allow list |
| **3. Type feedback** | Rejects the command. Feedback is returned to AI as error message |

**Option 2 persistence:**
- **CLI:** Saves to `~/.holmes/bash_approved_prefixes.yaml`. Persists across sessions.
- **Server:** Remembered for current session only, not persisted.

**Composed commands:** For commands with pipes or `&&`, each segment is validated. If multiple segments need approval, they're listed together:

```
Bash command

  kubectl get pods | grep error | head -10
  Get pods, filter for errors, show first 10

Do you want to proceed?
  1. Yes
  2. Yes, and don't ask again for `kubectl get`, `grep` and `head` commands
  3. Type here to tell Holmes what to do differently
```

If some segments are already allowed (e.g., `grep` is in allow list), only the non-allowed ones appear in option 2.

### CLI Flags

| Flag | Effect |
|------|--------|
| (default) | Prompt user for approval |
| `--bash-always-deny` | Auto-deny all non-whitelisted commands |
| `--bash-always-allow` | Auto-approve all non-whitelisted commands (dangerous) |

### Error Messages to AI

| Reason | Message should convey |
|--------|----------------------|
| Non-interactive, not in allow list | Command not allowed; use only allowed prefixes from system prompt |
| Hardcoded block (sudo, fork bomb) | Permanently blocked for security; cannot be overridden |
| User-configured deny list | Blocked by configuration |
| User denied | User chose to deny; include their feedback if provided |

## Configuration

### Allow/Deny Lists

Users provide their own allow and deny lists:

```yaml
toolsets:
  bash:
    enabled: true
    config:
      allow:
        - "kubectl get"
        - "kubectl describe"
        - "kubectl logs"
        - "grep"
        - "cat"
      deny:
        - "kubectl get secret"
        - "kubectl describe secret"
```

### Default Lists

**Local CLI:** Empty allow and deny lists. User builds trusted commands over time via approval prompts, persisted to `~/.holmes/`.

**Server/In-Cluster:** See Helm Chart section for recommended defaults.

**Hardcoded Blocks (Not Overrideable):**
```yaml
- "sudo"
- "su"
- ":(){"  # Fork bomb
```

### System Prompt

The allow list is injected into the system prompt via `llm_instructions` pattern. AI sees allowed commands at session start.

### Helm Chart

Recommended configuration for server deployments:

```yaml
# values.yaml
toolsets:
  bash:
    enabled: true
    config:
      allow:
        - "kubectl get"
        - "kubectl describe"
        - "kubectl logs"
        - "kubectl top"
        - "kubectl explain"
        - "kubectl api-resources"
        - "cat"
        - "grep"
        - "head"
        - "tail"
        - "sort"
        - "uniq"
        - "wc"
        - "cut"
        - "tr"
        - "ls"
        - "find"
        - "stat"
        - "file"
        - "du"
        - "df"
        - "ps"
        - "top -b"
        - "free"
        - "uptime"
      deny:
        - "kubectl get secret"
        - "kubectl describe secret"
```

## Validation

### Validation Order

1. **Hardcoded blocks** → REJECT immediately (sudo, su, fork bombs - not overrideable)
2. **Deny list** (user-configured) → REJECT immediately
3. **Allow list** → ALLOW
4. **Neither** → `APPROVAL_REQUIRED`

### Prefix-Based Matching

Commands are matched against allow/deny lists using prefix matching at the command + subcommand level.

**Example allow list:**
```yaml
- "kubectl get"
- "kubectl describe"
- "grep"
```

**Matching behavior:**
- `kubectl get pods -n default` → matches `kubectl get` ✓
- `kubectl delete pod` → no match ✗
- `grep -r "error" /var/log` → matches `grep` ✓

**Why prefix matching:** Balances security (granular control at subcommand level) with usability (one approval covers variations like `-n namespace`).

### Composed Commands

Commands with `|`, `&&`, `||`, `;`, `&` are parsed into segments using bashlex. One prefix per segment is required.

**Validation:**
- Segment count must equal prefix count
- Each prefix must match its segment
- ALL segments must pass allow/deny validation

**Why per-segment validation:** A whitelisted command cannot "carry" a dangerous one. `cat file.txt | rm -rf /` → `rm -rf` still blocked.

### Blocked Patterns

**Subshells** - Blocked entirely: `$(...)`, backticks, `<(...)`, `>(...)`

**Why:** Subshells bypass validation. `echo $(kubectl get secret)` would execute the inner command without checking it.

**Parse failures** - If bashlex cannot parse the command, fail the tool call immediately.

**Environment variables** - All allowed (`$HOME`, `$USER`, `${VAR}`, etc.). Variables expand at execution time by bash, not by our validator.

## Tool Interface

### Tool Parameters

```yaml
Tool: bash
Parameters:
  command:            # required, the bash command
  suggested_prefixes: # required, array of prefixes (one per command segment)
  timeout:            # optional, default 30 seconds
```

**Example:**
```yaml
Tool: bash
Parameters:
  command: "kubectl get pods | grep error | head -10"
  suggested_prefixes:
    - "kubectl get"
    - "grep"
    - "head"
```

### AI-Provided Prefixes

The AI provides `suggested_prefixes` when calling the tool. System verifies each prefix is valid for its command segment.

**Why AI-provided:** AI already reasons about the command when generating the tool call. No extra LLM call needed. System validation prevents gaming.

**Prefix selection guidelines for AI:**

| Include | Exclude |
|---------|---------|
| Command name (`kubectl`, `docker`) | Resource names (`my-pod`) |
| Subcommand (`get`, `describe`) | Namespace values (`default`) |
| Resource type (`pod`, `deployment`) | Flag values, file paths |

### Tool Behavior

One tool `bash`. Validation follows the order defined above. How `APPROVAL_REQUIRED` is handled is controlled by the calling layer (CLI/server), not the toolset.

**Calling layer handles approval:**
- **CLI (`call()`)**: Uses `approval_callback` to prompt user synchronously
- **Server (`call_stream()`)**: Stream ends with `APPROVAL_REQUIRED` event, client handles externally

### Approval Data Flow

When command is not in allow/deny list:

```python
StructuredToolResult(
    status=APPROVAL_REQUIRED,
    error="Command not in allow list",
    params={"command": "...", "suggested_prefixes": ["kubectl get", "grep"]},
    invocation="kubectl get pods | grep error"
)
```

The `suggested_prefixes` from params provides everything needed for the approval prompt.

## Implementation

### Success Criteria

1. Commands in allow list execute without prompts
2. Hardcoded blocks return `ERROR` (always enforced)
3. User-configured deny list returns `ERROR`
4. Non-whitelisted commands return `APPROVAL_REQUIRED`
5. Composed commands: each segment validated independently
6. Subshells detected and blocked
7. Prefix validation enforced (required, array, must match segments)
8. CLI flags control approval handling
9. Approved prefixes persist to `~/.holmes/` for CLI

### Testing

**Unit tests:** Prefix validation, command parsing, subshell detection, list matching, hardcoded block detection

**Integration tests:**
- Allow list commands execute
- Deny list commands return `ERROR`
- Non-whitelisted commands return `APPROVAL_REQUIRED`
- CLI flags work correctly
- Persistent approval storage

**LLM evals:**
- AI provides correct prefixes (array, matches segments)
- AI recovers from errors and denials
- AI respects hardcoded blocks

### Documentation

Create documentation for the new bash toolset:

1. How to enable the bash toolset
2. Example configuration with allow/deny lists
3. CLI flags (`--bash-always-deny`, `--bash-always-allow`)
4. Security considerations
5. Approval flow (CLI and server)

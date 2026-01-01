# Bash Toolset v2 Specification

A new bash toolset for HolmesGPT with prefix-based command validation and user approval.

## Overview

**Goal:** Enable bash command execution by default with security guardrails.

**Approach:** New `bash_v2` toolset. Existing `bash` toolset remains for backward compatibility.

**Key features:**
- Pre-configured allow list of safe command prefixes (server mode) or empty (local CLI)
- Hardcoded blocks for inherently dangerous patterns (sudo, fork bombs)
- User approval for non-whitelisted commands (interactive mode)
- Support for composed commands (pipes, &&, ||, ;, &)

## Command Validation

### Prefix-Based Matching

Commands are matched against allow/deny lists using prefix matching at the command + subcommand level.

**Example allow list:**
```yaml
- "kubectl get"
- "kubectl describe"
- "grep"
- "cat"
```

**Matching behavior:**
- `kubectl get pods -n default` → matches `kubectl get` ✓
- `kubectl delete pod` → no match ✗
- `grep -r "error" /var/log` → matches `grep` ✓

**Why prefix matching:** Balances security (granular control at subcommand level) with usability (one approval covers variations like `-n namespace`).

### Validation Order

1. **Hardcoded blocks** → REJECT immediately (sudo, su, fork bombs - not overrideable)
2. **Deny list** (user-configured) → REJECT immediately
3. **Allow list** → ALLOW
4. **Neither** → Prompt user (interactive) or REJECT (non-interactive)

**Why this order:** Hardcoded blocks are security fundamentals that should never be bypassed. User-configured deny list is checked before allow list to prevent accidental whitelisting of dangerous commands.

### AI-Provided Prefixes

When calling the bash tool, the AI provides `suggested_prefixes` (always an array):

```yaml
Tool: bash
Parameters:
  command: "kubectl get pods -n kube-system"
  suggested_prefixes: ["kubectl get"]
```

**Validation:** System verifies each prefix is valid for its command segment. Invalid prefix fails the tool call.

**Why AI-provided:** AI already reasons about the command when generating the tool call. No extra LLM call needed. System validation prevents gaming.

**Prefix selection guidelines for AI:**

| Include | Exclude |
|---------|---------|
| Command name (`kubectl`, `docker`) | Resource names (`my-pod`) |
| Subcommand (`get`, `describe`) | Namespace values (`default`) |
| Resource type (`pod`, `deployment`) | Flag values, file paths |

**`suggested_prefixes` must be provided as an array**, even for single commands.

## Composed Commands

Commands with `|`, `&&`, `||`, `;`, `&` are parsed into segments. One prefix per segment is required:

```yaml
Tool: bash_v2
Parameters:
  command: "kubectl get pods | grep error | head -10"
  suggested_prefixes:
    - "kubectl get"
    - "grep"
    - "head"
```


**Validation:**
- Tool parses command into segments using bashlex
- Segment count must equal prefix count
- Each prefix must match its segment
- ALL segments must pass allow/deny validation

**Parsing:** Use `bashlex` library for proper shell parsing (handles quotes, escapes correctly).

**Why per-segment validation:** A whitelisted command cannot "carry" a dangerous one. `cat file.txt | rm -rf /` → `rm -rf` still blocked.

## Blocked Patterns

### Subshells

Blocked entirely: `$(...)`, backticks, `<(...)`, `>(...)`

**Why:** Subshells bypass validation. `echo $(kubectl get secret)` would execute the inner command without checking it.

### Parse Failures

If bashlex cannot parse the command, fail the tool call immediately.

**Why:** Malformed commands would fail anyway. Better to catch early with clear error than fall back to unsafe behavior.

### Environment Variables

All environment variables are allowed (`$HOME`, `$USER`, `${VAR}`, etc.).

**Why:** Variables expand at execution time by bash, not by our validator. The allow/deny lists operate on the literal command string before expansion. Blocking env vars would break many legitimate commands.

## Tool Behavior

One tool `bash_v2`. The toolset always returns `APPROVAL_REQUIRED` for non-whitelisted commands. How that approval is handled is controlled by the calling layer (CLI/server), not the toolset.

**Toolset behavior:**
1. Hardcoded blocks → `ERROR` (always, cannot be overridden)
2. Deny list → `ERROR`
3. Allow list → Execute
4. Neither → `APPROVAL_REQUIRED`

**Calling layer handles approval:**
- **CLI (`call()`)**: Uses `approval_callback` to prompt user synchronously
- **Server (`call_stream()`)**: Stream ends with `APPROVAL_REQUIRED` event, client handles externally

**CLI flags** control how approval is handled (not the toolset):

| Flag | Effect |
|------|--------|
| (default) | Prompt user for approval |
| `--bash-always-deny` | Auto-deny all `APPROVAL_REQUIRED` |
| `--bash-always-allow` | Auto-approve all `APPROVAL_REQUIRED` (dangerous) |

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

The `suggested_prefixes` from params provides everything needed for the approval prompt ("Yes, and don't ask again for `kubectl get`, `grep` commands").

## User Approval (Interactive)

When a non-whitelisted command is attempted:

```
Bash command

  kubectl get pod
  List Kubernetes pods

Do you want to proceed?
  1. Yes
  2. Yes, and don't ask again for `kubectl get` commands
  3. Type here to tell Holmes what to do differently
```

**Multiple prefixes:** When multiple prefixes need approval, option 2 lists them all:

```
  2. Yes, and don't ask again for `grep`, `kubectl get` and `head` commands
```

**Maximum 5 prefixes:** If more than 5 prefixes need approval, reject the command and instruct the AI to split into smaller commands.

**Option 2:** Saves all listed prefixes to persistent whitelist stored in `~/.holmes/bash_approved_prefixes.yaml` (or similar). Persists across sessions.

## Error Messages to AI

| Reason | Message should convey |
|--------|----------------------|
| Non-interactive, not in allow list | Command not allowed; use only allowed prefixes from system prompt |
| Hardcoded block (sudo, fork bomb) | Permanently blocked for security; cannot be overridden |
| User-configured deny list | Blocked by configuration; contact administrator |
| User denied | User chose to deny; include their feedback if provided |

**Why differentiated:** AI needs context to recover appropriately.

## Default Lists

### Local CLI

Empty allow and deny lists by default. User builds their own trusted command set over time via approval prompts, which persist to `~/.holmes/`.

### Server/In-Cluster

See Helm Chart section for recommended defaults.

### Hardcoded Blocks (Not Overrideable)

These are always blocked regardless of configuration:

```yaml
# Privilege escalation
- "sudo"
- "su"

# Malicious patterns
- ":(){"  # Fork bomb
```

**Why hardcoded:** These patterns are inherently dangerous and should never be allowed. Detected via pattern matching before any other validation.

## Configuration

Users provide their own allow and deny lists:

```yaml
toolsets:
  bash_v2:
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

**Note:** Users define the complete lists. There are no "add/remove" modifiers - the config specifies exactly what is allowed/denied.

## Tool Parameters

```yaml
Tool: bash_v2
Parameters:
  command:            # required, the bash command
  suggested_prefixes: # required, array of prefixes (one per command segment)
  timeout:            # optional, default 30 seconds
```

**`suggested_prefixes`:**
- Required for approval prompt ("don't ask again for X commands")
- Always an array: `["kubectl get"]` or `["kubectl get", "grep", "head"]`


## Whitelist in System Prompt

The allow list is injected into the system prompt via `llm_instructions` pattern:

**Why:** AI sees allowed commands at session start. Single source of truth. Different instructions for interactive vs non-interactive modes.

## Success Criteria

1. Commands in allow list execute without prompts
2. Hardcoded blocks return `ERROR` (always enforced)
3. User-configured deny list returns `ERROR`
4. Non-whitelisted commands return `APPROVAL_REQUIRED`
5. Composed commands: each segment validated independently
6. Subshells detected and blocked
7. Prefix validation enforced (required, array, must match segments)
8. CLI flags control approval handling (`--bash-always-deny`, `--bash-always-allow`)
9. Config customization merges correctly with defaults
10. Approved prefixes persist to `~/.holmes/` for CLI

## Testing

**Unit tests:** Prefix validation, command parsing, subshell detection, list matching, config merging, hardcoded block detection

**Integration tests:**
- Allow list commands execute
- Deny list commands return `ERROR`
- Non-whitelisted commands return `APPROVAL_REQUIRED`
- CLI flags (`--bash-always-deny`, `--bash-always-allow`) work correctly
- Persistent approval storage in `~/.holmes/`

**LLM evals:**
- AI provides correct prefixes (array, matches segments)
- AI recovers from errors and denials
- AI respects hardcoded blocks

## Documentation

Create documentation for the new bash toolset:

1. **How to enable** the bash_v2 toolset
2. **Example configuration** with allow/deny lists
3. **CLI flags** (`--bash-always-deny`, `--bash-always-allow`)
4. **Security considerations** (hardcoded blocks, why certain commands are denied)
5. **Approval flow** (how user approval works in CLI and server)

## Helm Chart

Add recommended configuration to the Helm chart that installs Holmes:

```yaml
# values.yaml
toolsets:
  bash_v2:
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

This provides a secure default for server deployments with read-only Kubernetes and filesystem commands.

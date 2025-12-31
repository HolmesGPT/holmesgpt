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

**Always required:** `suggested_prefixes` must always be provided as an array, even for single commands.

## Composed Commands

Commands with `|`, `&&`, `||`, `;`, `&` require one prefix per segment:

```yaml
Tool: bash
Parameters:
  command: "kubectl get pods | grep error | head -10"
  suggested_prefixes:
    - "kubectl get"
    - "grep"
    - "head"
```

**Validation:**
- Segment count must equal prefix count
- Each prefix must match its segment
- ALL segments must pass validation

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

## Two Tools Architecture

Two separate tools that share execution logic:

| Tool | Mode | Use Case |
|------|------|----------|
| `bash` | Interactive | CLI with user present. Can prompt for approval. |
| `bash_noninteractive` | Non-interactive | Server/automated. No prompts. |

**Why two tools:** Clear separation of capabilities. The toolset config determines which tool is registered.

### Interactive Tool (`bash`)

Used when user can be prompted. Default for CLI.

**CLI flags:**
| Flag | Behavior |
|------|----------|
| (default) | Prompt user for non-whitelisted commands |
| `--bash-always-deny` | Never prompt, reject all non-whitelisted |

### Non-Interactive Tool (`bash_noninteractive`)

Used when no user is present. Default for server mode.

**Modes (via config):**

```yaml
toolsets:
  bash_v2:
    config:
      mode: "strict"  # or "permissive"
```

| Mode | Behavior |
|------|----------|
| `strict` | Only allow-listed commands execute. Others rejected. |
| `permissive` | All commands execute (hardcoded blocks still enforced). |

**Why `permissive` mode:** For users who want to dangerously allow everything. Uses non-interactive tool so there's no expectation of user prompts. Hardcoded blocks (sudo, fork bombs) are still enforced.

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

### Server/In-Cluster Allow List

```yaml
# Kubernetes read-only
- "kubectl get"
- "kubectl describe"
- "kubectl logs"
- "kubectl top"
- "kubectl explain"
- "kubectl api-resources"

# Text processing (no awk/sed - can run scripts)
- "cat"
- "grep"
- "head"
- "tail"
- "sort"
- "uniq"
- "wc"
- "cut"
- "tr"

# Filesystem read-only
- "ls"
- "find"
- "stat"
- "file"
- "du"
- "df"

# Process info
- "ps"
- "top -b"
- "free"
- "uptime"
```

### Local CLI Allow List

Empty by default.

**Why:** User's machine may have sensitive files. All commands require explicit approval, which then persists to `~/.holmes/`. User builds their own trusted command set over time.

### Hardcoded Blocks (Not Overrideable)

These are always blocked regardless of configuration:

```yaml
# Privilege escalation
- "sudo"
- "su"

# Malicious patterns
- ":(){"  # Fork bomb
```

**Why hardcoded:** These patterns are inherently dangerous and should never be allowed in any mode. Detected via pattern matching before any other validation.

### Server Deny List

Default denies access to sensitive resources:

```yaml
- "kubectl get secret"
- "kubectl describe secret"
```

Users can add more via `deny_add` or remove defaults via `deny_remove` in config.

**Why deny secrets by default:** `kubectl get` and `kubectl describe` are in the allow list, but accessing secrets is sensitive. This prevents accidental secret exposure while allowing users who need it to explicitly opt-in.

### Local CLI Deny List

Empty by default. Users can add deny patterns via config.

## Configuration

Users can customize allow and deny lists:

```yaml
toolsets:
  bash_v2:
    config:
      allow_add:
        - "docker ps"
      allow_remove:
        - "cat"
      deny_add:
        - "curl"
        - "rm -rf"
      deny_remove:
        - "kubectl get secret"  # opt-in to secret access
```

## Tool Parameters

```yaml
Tool: bash
Parameters:
  command:            # required, the bash command
  suggested_prefixes: # required, array of prefixes (one per command segment)
  timeout:            # optional, default 30 seconds
```

**`suggested_prefixes` is always required and always an array:**
- Single command: `["kubectl get"]`
- Composed command: `["kubectl get", "grep", "head"]`

**Why always array:** Simplifies implementation. No conditional logic for single vs composed commands.

## Whitelist in System Prompt

The allow list is injected into the system prompt via `llm_instructions` pattern:

**Why:** AI sees allowed commands at session start. Single source of truth. Different instructions for interactive vs non-interactive modes.

## Success Criteria

1. Commands in allow list execute without prompts
2. Hardcoded blocks enforced in all modes (including permissive)
3. User-configured deny list blocks commands
4. Composed commands: each segment validated independently
5. Subshells detected and blocked
6. Two tools work correctly (interactive vs non-interactive)
7. Prefix validation enforced (always required, always array)
8. Config customization merges correctly with defaults
9. Approved prefixes persist to `~/.holmes/` for CLI

## Testing

**Unit tests:** Prefix validation, command parsing, subshell detection, list matching, config merging, hardcoded block detection

**Integration tests:** End-to-end execution, two tools behavior, mode switching (strict/permissive), persistent approval storage

**LLM evals:** AI provides correct prefixes (always array), recovers from denials, respects hardcoded blocks

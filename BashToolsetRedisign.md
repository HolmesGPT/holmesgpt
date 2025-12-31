# Bash Toolset Redesign Plan

This document outlines the plan for redesigning the bash toolset in HolmesGPT.

## Approach

**Build a brand new plugin from scratch** while keeping the existing bash toolset as a deprecated plugin. This allows:
- Clean slate design without legacy constraints
- Gradual migration path for users
- Side-by-side comparison during transition period

## Current State

The existing `bash` toolset will remain as-is for backward compatibility. This redesign creates a new `bash_v2` toolset from scratch.

## Goals

1. **Enabled by default** - Unlike the current bash toolset which requires explicit enablement
2. **Pre-configured allowed commands** - Ship with a whitelist of safe commands (as prefixes)
3. **Support command composition** - Allow combining commands with:
   - Pipes (`|`)
   - Background execution (`&`)
   - Sequential execution (`;`)
4. **Two permission modes** - See below

## Permission Modes

### Mode 1: Interactive (user present)

- AI can request permission from the user to run non-whitelisted commands
- If user grants permission, that permission is **saved for the session** (by prefix)
- Subsequent uses of commands matching the approved prefix don't require re-approval

### Mode 2: Non-interactive (automated/server)

- AI **cannot** ask user for permission
- Non-whitelisted commands **fail immediately**
- AI should be instructed to never attempt non-whitelisted commands

## Design Decisions

### #1: Command Matching Strategy

How should we match commands against the whitelist (both pre-configured and user-approved)?

**Alternative A: Exact Command Match**

```
Whitelist: ["kubectl get pods", "cat /var/log/syslog"]
```

- Security: **High** - No surprises, exact match only
- Ease of use: **Low** - Must approve every variation
- Example: `kubectl get pods` ✓, `kubectl get pods -n foo` ✗

**Alternative B: Simple Prefix Match**

```
Whitelist: ["kubectl", "cat", "grep"]
```

- Security: **Low** - `kubectl delete` would be allowed if `kubectl` is whitelisted
- Ease of use: **High** - One approval covers all subcommands
- Example: `kubectl get pods` ✓, `kubectl delete deployment` ✓ (dangerous!)

**Alternative C: Command + Subcommand Prefix**

```
Whitelist: ["kubectl get", "kubectl describe", "kubectl logs", "cat", "grep"]
```

- Security: **Medium-High** - Granular control at subcommand level
- Ease of use: **Medium** - Good balance
- Example: `kubectl get pods -n foo` ✓, `kubectl delete pods` ✗
- Note: Single-word commands like `cat` match as prefix

**Prefix determination (for user approval in interactive mode):**

The AI provides the suggested prefix as a tool parameter. When calling the `bash` tool:

```yaml
Tool: bash
Parameters:
  command: "kubectl get pods -n kube-system"
  suggested_prefix: "kubectl get"  # AI decides this
```

**Validation rules:**
- `suggested_prefix` must be an actual prefix of `command`
- If validation fails → tool call fails immediately (AI must fix and retry)
- This prevents the AI from gaming the system

**Flow:**
1. AI calls `bash` tool with `command` and `suggested_prefix`
2. System validates prefix is legitimate
3. If command matches whitelist → execute
4. If not → show user: "Allow `kubectl get` commands?" (using `suggested_prefix`)
5. If user approves → save `kubectl get` to session whitelist, then execute

**Why this works:**
- AI is already reasoning about the command when generating the tool call
- No extra LLM call needed for prefix extraction
- System enforces prefix is legitimate via validation
- AI can intelligently choose granularity (e.g., `kubectl get` vs `kubectl`)

**AI Guidelines for Choosing `suggested_prefix`:**

The prefix should include the command, subcommand(s), and operation type - but stop before variable/instance-specific arguments.

**Rule: Include stable parts, exclude variable parts**

| Include in prefix | Exclude from prefix |
|-------------------|---------------------|
| Command name (`kubectl`, `docker`) | Specific resource names (`my-pod`, `nginx`) |
| Subcommand (`get`, `describe`, `logs`) | Namespace values (`default`, `kube-system`) |
| Resource type (`pod`, `service`, `deployment`) | Flag values (`-n <value>`, `--output <value>`) |
| Action verbs (`run`, `exec`, `apply`) | File paths (`/var/log/app.log`, `./config.yaml`) |
| | Patterns and queries (`error`, `"my-pattern"`) |
| | Container names, labels, selectors |

**Examples:**

```
Command: kubectl get pod -n default
Prefix:  kubectl get pod
Why: "default" is a variable namespace value

Command: kubectl get pod hello-world
Prefix:  kubectl get pod
Why: "hello-world" is a specific pod name

Command: kubectl logs my-pod -c my-container
Prefix:  kubectl logs
Why: "my-pod" and "my-container" are instance-specific

Command: kubectl describe deployment nginx-deployment
Prefix:  kubectl describe deployment
Why: "nginx-deployment" is a specific resource name

Command: docker ps -a
Prefix:  docker ps
Why: "-a" is a flag that modifies behavior (could go either way)

Command: docker logs my-container --tail 100
Prefix:  docker logs
Why: "my-container" is instance-specific

Command: grep -r "error" /var/log/
Prefix:  grep
Why: "error" is a search pattern, "/var/log/" is a path

Command: cat /etc/nginx/nginx.conf
Prefix:  cat
Why: The path is variable (single-word commands = just the command)

Command: aws s3 ls s3://my-bucket
Prefix:  aws s3 ls
Why: "s3://my-bucket" is a specific bucket

Command: helm install my-release ./chart
Prefix:  helm install
Why: "my-release" and "./chart" are instance-specific
```

**Key principle:** The prefix should represent a *class of similar commands* that the user would reasonably want to approve together. If approving the prefix would allow unintended commands, make it more specific. If approving the prefix is too narrow to be useful, make it broader.

**Validation (enforced by system):**
- `suggested_prefix` MUST be an exact prefix of `command`
- `command.startswith(suggested_prefix)` must be true
- If validation fails, the tool call fails and AI must retry with correct prefix

---

### Handling Composed Commands (pipes, &, ;)

When commands are composed using `|`, `&&`, `||`, `;`, or `&`, each segment must be validated separately.

**AI provides a list of prefixes (one per segment):**

```yaml
Tool: bash
Parameters:
  command: "kubectl get pods -n default | grep error | head -10"
  suggested_prefixes:    # List - one prefix per segment
    - "kubectl get pod"
    - "grep"
    - "head"
```

**Validation rules:**

1. System parses command into segments (split on `|`, `&&`, `||`, `;`, `&`)
2. Number of `suggested_prefixes` must equal number of segments
3. Each prefix must be a valid prefix of its corresponding segment
4. If any validation fails → tool call fails, AI must retry

```python
def validate_composed_command(command: str, suggested_prefixes: List[str]) -> ValidationResult:
    segments = parse_command_segments(command)  # Split on |, &&, ||, ;, &

    if len(segments) != len(suggested_prefixes):
        return ValidationResult.FAIL, \
            f"Expected {len(segments)} prefixes, got {len(suggested_prefixes)}"

    for i, (segment, prefix) in enumerate(zip(segments, suggested_prefixes)):
        segment = segment.strip()
        if not segment.startswith(prefix):
            return ValidationResult.FAIL, \
                f"Prefix '{prefix}' is not a prefix of segment {i+1}: '{segment}'"

    return ValidationResult.OK
```

**Approval flow:**

1. Check each segment against whitelist
2. Collect segments that need approval
3. If ALL segments approved → execute
4. If ANY need approval → prompt user with unapproved prefixes

**Approval prompt (multiple prefixes need approval):**

```
Bash command

  kubectl get pods -n default | grep error | head -10
  List pods and filter for errors

The following command prefixes need approval:
  • kubectl get pod
  • grep

Already approved: head

Do you want to proceed?
❯ 1. Yes (approve once)
  2. Yes, and remember these prefixes for this session
  3. Type here to tell Holmes what to do differently
```

**Option 2 behavior:** Saves ALL unapproved prefixes to session whitelist.

**Security rules:**
- ALL segments must pass validation before execution
- A whitelisted command cannot "carry" a non-whitelisted command
- Example: `cat safe.txt | rm -rf /` → `rm -rf` still needs approval

**For single commands:** AI can provide either:
- `suggested_prefix: "kubectl get pod"` (string) - single command
- `suggested_prefixes: ["kubectl get pod"]` (list with one item) - also valid

System accepts both formats for convenience.

**Command Parsing Implementation:**

Use a proper shell parser library (similar to Claude Code's approach):

**Recommended: `bashlex`** - Python library that parses bash syntax into AST

```python
import bashlex

def parse_command_segments(command: str) -> List[str]:
    """
    Split command on shell operators using proper bash parsing.
    Returns list of command segments.
    """
    try:
        parts = bashlex.parse(command)
    except bashlex.errors.ParsingError:
        # Fallback: treat as single command if parsing fails
        return [command.strip()]

    segments = []
    for part in parts:
        segments.extend(_extract_commands_from_ast(part))

    return segments

def _extract_commands_from_ast(node) -> List[str]:
    """
    Recursively extract command strings from bashlex AST.
    Handles: pipelines, lists (&&, ||, ;), and simple commands.
    """
    if node.kind == 'pipeline':
        # Pipeline: cmd1 | cmd2 | cmd3
        return [_node_to_string(cmd) for cmd in node.parts]

    elif node.kind == 'list':
        # List: cmd1 && cmd2, cmd1 || cmd2, cmd1 ; cmd2
        result = []
        for part in node.parts:
            result.extend(_extract_commands_from_ast(part))
        return result

    elif node.kind == 'command':
        return [_node_to_string(node)]

    else:
        # Other node types - return as single segment
        return [_node_to_string(node)]

def _node_to_string(node) -> str:
    """Convert AST node back to command string."""
    # bashlex nodes have 'pos' attribute with (start, end) positions
    # Use original command substring
    return command[node.pos[0]:node.pos[1]]
```

**Why bashlex over manual parsing:**
- Handles all bash syntax edge cases (quotes, escapes, subshells, etc.)
- Battle-tested library
- Produces AST that can be inspected for operator types
- Same approach as Claude Code (tokenize first, then find operators)

**Example:**
```python
parse_command_segments("kubectl get pods | grep 'error | msg' && echo done")
# Returns: ["kubectl get pods", "grep 'error | msg'", "echo done"]
# Note: pipe inside quotes is correctly handled
```

**Fallback:** If bashlex fails to parse (malformed command), fail the tool call immediately with parsing error details. See "Parsing Failure Handling" section.

---

### User Approval Prompt (Interactive Mode)

When a command is not in the whitelist, present the user with three options:

**UI Design (similar to Claude Code):**

```
Bash command

  kubectl get pod
  List Kubernetes pods

Do you want to proceed?
❯ 1. Yes
  2. Yes, and don't ask again for `kubectl get pod` commands
  3. Type here to tell Claude what to do differently
```

**Option behaviors:**

| Option | Label | Behavior |
|--------|-------|----------|
| 1 | "Yes" | Execute command once. No prefix saved. Next similar command will ask again. |
| 2 | "Yes, and don't ask again for `<prefix>` commands" | Execute command AND save `suggested_prefix` to session whitelist. Future commands matching this prefix execute without prompting. |
| 3 | "Type here to tell Claude what to do differently" | Deny execution. User can provide feedback to AI. |

**Code handling:**

```python
def handle_user_approval(command: str, suggested_prefix: str, description: str) -> ApprovalResult:
    """
    Returns:
      - ApprovalResult.ALLOW_ONCE: Execute command, don't save prefix
      - ApprovalResult.ALLOW_PREFIX: Execute command, save prefix to session whitelist
      - ApprovalResult.DENY: Don't execute, return user feedback to AI
    """

    # Display prompt to user
    print(f"Bash command\n")
    print(f"  {command}")
    print(f"  {description}\n")
    print("Do you want to proceed?")
    print("❯ 1. Yes")
    print(f"  2. Yes, and don't ask again for `{suggested_prefix}` commands")
    print("  3. Type here to tell Claude what to do differently")

    choice = get_user_choice()

    if choice == 1:
        return ApprovalResult.ALLOW_ONCE
    elif choice == 2:
        # Save prefix to session whitelist
        session_whitelist.add(suggested_prefix)
        return ApprovalResult.ALLOW_PREFIX
    else:
        # Get user feedback and return to AI
        feedback = get_user_input()
        return ApprovalResult.DENY, feedback
```

**Session whitelist behavior:**
- Prefixes approved via option 2 are stored in memory for the session only
- They are checked before the pre-configured whitelist
- Session ends when the Holmes process terminates

---

### Whitelist in System Prompt

The whitelist should be communicated to the AI via the existing `llm_instructions` pattern used by toolsets.

**Existing pattern** (in `_toolsets_instructions.jinja2`):
```jinja2
{%- for toolset in enabled_toolsets_with_instructions -%}
## {{ toolset.name }}
{{ toolset.llm_instructions }}
{%- endfor -%}
```

**Implementation for bash toolset:**

The bash toolset should dynamically build its `llm_instructions` when loaded, including the current whitelist:

```python
class BashToolset:
    def build_llm_instructions(self) -> str:
        """Build instructions including the current whitelist."""
        allowed_prefixes = self.get_allowed_prefixes()  # From config

        return f"""
## Bash Command Execution

You can execute bash commands using the `bash` tool.

### Allowed Command Prefixes (no approval needed)
The following command prefixes are pre-approved and will execute without user confirmation:
{self._format_whitelist(allowed_prefixes)}

### Commands Requiring Approval
For commands not matching the whitelist above, you must provide a `suggested_prefix` parameter.
The user will be asked to approve the command.

### How to Choose `suggested_prefix`
[Include the AI guidelines documented earlier]
"""
```

**For `bash_limited` tool (non-interactive mode):**

The instructions should be more restrictive:

```python
def build_llm_instructions(self) -> str:
    allowed_prefixes = self.get_allowed_prefixes()

    return f"""
## Bash Command Execution (Limited Mode)

You can ONLY execute commands matching these prefixes:
{self._format_whitelist(allowed_prefixes)}

Do NOT attempt to run any other commands - they will fail.
There is no user approval mechanism in this mode.
"""
```

**This ensures:**
- AI sees the whitelist at the start of every session
- Whitelist is single source of truth (config → llm_instructions)
- Different instructions for interactive vs non-interactive mode

---

### #2: Mode Selection

**CLI Mode (default: interactive)**

When running Holmes as a CLI, interactive mode is enabled by default. Two override flags:

| Flag | Behavior |
|------|----------|
| `--bash-dangerous-no-approval` | Skip approval prompts for non-whitelisted commands (deny list still enforced) |
| `--bash-always-deny` | Never ask user, deny all non-whitelisted commands |

**Important:** The deny list is ALWAYS enforced, even with `--bash-dangerous-no-approval`. This flag only skips user approval prompts—it does not bypass security restrictions.

```bash
# Default: interactive (can ask user)
holmes ask "why is my pod crashing?"

# Dangerous: skip all approval prompts
holmes ask --bash-dangerous-no-approval "why is my pod crashing?"

# Strict: never prompt, only whitelisted commands
holmes ask --bash-always-deny "why is my pod crashing?"
```

**Non-CLI Mode (server/in-cluster)**

When Holmes runs as a server or in-cluster, mode is determined by toolset configuration:

```yaml
toolsets:
  bash_v2:
    enabled: true
    config:
      allow_user_approval: false  # true = can ask, false = strict whitelist only
```

**Code structure:**

```
bash_toolset/
├── executor.py          # Shared execution logic
├── whitelist.py         # Shared whitelist validation
├── validator.py         # Deny list, allow list, subshell detection
└── approval.py          # User approval prompts (CLI only)
```

---

### #3: Communicating Whitelist to AI

**Decision:** Use the existing `llm_instructions` pattern (see "Whitelist in System Prompt" section above).

The whitelist is dynamically injected into the system prompt via the toolset's `llm_instructions` field, which is rendered by `_toolsets_instructions.jinja2` at session start.

This approach:
- Uses existing HolmesGPT infrastructure (no new patterns needed)
- Single source of truth (config → llm_instructions → system prompt)
- Different instructions for `bash` vs `bash_limited` modes
- AI sees whitelist prominently at start of every session

## Default Whitelist

Two separate whitelists based on deployment context:

### Server / In-Cluster Whitelist (more permissive)

When Holmes runs as a server or in-cluster, it operates in a controlled environment.

```yaml
allow:
  # Kubernetes read-only
  - "kubectl get"
  - "kubectl describe"
  - "kubectl logs"
  - "kubectl top"
  - "kubectl explain"
  - "kubectl api-resources"

  # Text processing (safe ones only - NO awk/sed which can run scripts)
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

### Local CLI Whitelist (more restrictive - subset)

When Holmes runs as local CLI, restrict directory traversal and system access.

```yaml
allow:
  # Kubernetes read-only (same as server)
  - "kubectl get"
  - "kubectl describe"
  - "kubectl logs"
  - "kubectl top"
  - "kubectl explain"

  # Text processing (safe ones only)
  - "grep"
  - "head"
  - "tail"
  - "sort"
  - "uniq"
  - "wc"

  # NO filesystem traversal commands (ls, find, cat, etc.)
  # User must explicitly approve these to prevent unintended access
```

**Rationale for local CLI restrictions:**
- User's machine may have sensitive files
- Prevent accidental directory traversal
- User can approve additional commands as needed via interactive mode

### Deny List (always blocked)

These commands are NEVER allowed, even if user tries to approve them.
Holmes is a diagnostic tool - it should not make drastic changes.

**IMPORTANT:** The deny list MUST be checked FIRST, before checking the allow list. This ensures dangerous commands cannot be whitelisted by accident.

```yaml
deny:
  # Destructive filesystem operations
  - "rm -rf"
  - "rm -r"
  - "rmdir"
  - "dd"
  - "mkfs"
  - "shred"

  # Dangerous Kubernetes operations
  - "kubectl delete"
  - "kubectl exec"
  - "kubectl apply"
  - "kubectl patch"
  - "kubectl edit"
  - "kubectl replace"
  - "kubectl scale"

  # Kubernetes secrets (sensitive data)
  - "kubectl get secret"
  - "kubectl describe secret"

  # System modification
  - "chmod"
  - "chown"
  - "sudo"
  - "su"

  # Fork bombs and malicious patterns
  - ":(){" # Fork bomb pattern
```

**Validation order:**
1. Check deny list FIRST → if matched, REJECT immediately (no user override possible)
2. Check allow list → if matched, ALLOW
3. If neither matched → prompt user (interactive) or REJECT (non-interactive)

**Allow list customization:**

Users can add to OR remove from the allow list via config:

```yaml
toolsets:
  bash_v2:
    config:
      allow_add:
        - "docker ps"      # Add docker ps to allow list
        - "helm list"
      allow_remove:
        - "cat"            # Remove cat from allow list (require approval)
```

**Deny list customization:**

Users can add to OR remove from the deny list via config:

```yaml
toolsets:
  bash_v2:
    config:
      deny_add:
        - "curl"           # Add curl to deny list
        - "wget"
      deny_remove:
        - "kubectl exec"   # Remove kubectl exec from deny list (use with caution!)
```

**Note:** Removing from deny list is dangerous and should be used sparingly. The default deny list exists for safety reasons.

---

### Error Messages to AI

The AI should receive different error messages depending on the denial reason, so it can adjust its behavior appropriately.

| Denial Reason | Error Should Convey |
|---------------|---------------------|
| Non-interactive mode, not in allow list | Command not allowed; remind AI to only use allowed prefixes from system prompt |
| Deny list match | Command is blocked permanently; Holmes is diagnostic-only, no destructive ops |
| User denied (interactive) | User chose to deny; include any feedback the user provided |

**Rationale:** The AI needs context to recover gracefully. "Not allowed" vs "user said no" vs "permanently blocked" require different responses from the AI.

---

### Subshell Handling

**Decision: Block subshells entirely**

Commands containing subshells are blocked to prevent nested command execution that bypasses validation.

**Blocked patterns:**
- `$(...)` - Command substitution
- Backtick command substitution
- `<(...)` and `>(...)` - Process substitution

**Rationale:** Subshells allow arbitrary command execution inside an otherwise-safe command. For example, `echo $(kubectl get secret)` would bypass secret access restrictions.

**Detection:** Use bashlex AST to detect `commandsubstitution` and `processsubstitution` node types.

**Error handling:** Fail the tool call and instruct AI to execute commands separately.

---

### Parsing Failure Handling

**Decision: Fail the tool call when bashlex cannot parse**

If bashlex fails to parse the command (malformed syntax), the tool call fails immediately.

**Rationale:**
- Malformed commands would fail in bash anyway
- Better to catch early with clear error
- Prevents fallback to unsafe "treat as single command" behavior
- AI can fix syntax and retry

**Error handling:** Return parsing error details and suggest splitting complex commands into separate tool calls.

---

### Environment Variables

**Decision: Allow environment variables**

Environment variables like `$HOME`, `$USER`, `${VAR}` are allowed in commands.

**Rationale:**
- Variables expand at execution time by bash, not by our validator
- The expanded command is what actually runs
- Blocking env vars would break many legitimate commands
- The deny/allow list operates on the literal command string before expansion

**Example:** `kubectl get pods -n $NAMESPACE` is validated as-is. The prefix `kubectl get pod` matches regardless of what `$NAMESPACE` expands to.

---

## Tool Parameters Schema

```yaml
Tool: bash
Parameters:
  command:
    type: string
    required: true
    description: "The bash command to execute"

  suggested_prefix:
    type: string
    required: false
    description: "Suggested prefix for single commands (for approval prompt)"

  suggested_prefixes:
    type: array[string]
    required: false
    description: "List of prefixes for composed commands (one per segment)"

  timeout:
    type: integer
    required: false
    default: 30
    description: "Command timeout in seconds"
```

**When is `suggested_prefix` / `suggested_prefixes` required?**

- If command matches allow list → prefix parameter is optional (command executes directly)
- If command does NOT match allow list → prefix parameter is REQUIRED
  - Missing prefix → tool call fails with error asking AI to provide it
  - This ensures AI always thinks about the appropriate prefix for non-whitelisted commands

**Validation rules:**
- Provide `suggested_prefix` (string) for single commands
- Provide `suggested_prefixes` (array) for composed commands (pipes, &&, etc.)
- Do not provide both

**Description for approval prompt:** Reuse existing mechanism from HolmesGPT tool descriptions.

---

## New Plugin Design

Design details will emerge during implementation. Key architectural decisions are captured in the Design Decisions section above.

## Deprecation Strategy

**Decision: Keep both toolsets**

- New bash toolset is added alongside the old one
- Old bash toolset remains available for backward compatibility
- Users can choose which to use via config
- Documentation maintained for both

```yaml
# Config example
toolsets:
  bash_v2:          # New toolset (recommended)
    enabled: true
    mode: interactive

  bash:             # Old toolset (deprecated but available)
    enabled: false
```

**Future consideration:** After sufficient adoption of new toolset, old one may be removed in a major version release.

---

## Documentation

- **New toolset:** Full documentation required (usage, config, whitelist customization)
- **Old toolset:** Keep existing docs, add deprecation notice pointing to new toolset

---

## Success Criteria & Testing

### Functional Requirements

The implementation is complete when:

1. **Whitelist enforcement works**
   - Commands matching allow list execute without prompts
   - Commands not in allow list trigger approval (interactive) or rejection (non-interactive)

2. **Deny list takes precedence**
   - Deny list checked before allow list
   - Denied commands blocked even in `--dangerous-no-approval` mode
   - Users cannot approve deny-listed commands

3. **Composed commands validated correctly**
   - Each segment validated independently
   - All segments must pass for command to execute
   - bashlex correctly parses pipes, &&, ||, ;, &

4. **Subshells blocked**
   - `$(...)`, backticks, `<(...)`, `>(...)` detected and rejected
   - Clear error message guides AI to split commands

5. **Mode selection works**
   - CLI defaults to interactive
   - `--bash-dangerous-no-approval` skips prompts (but respects deny list)
   - `--bash-always-deny` rejects all non-whitelisted
   - Server mode respects `allow_user_approval` config

6. **Prefix validation works**
   - AI-provided prefix must be actual prefix of command
   - Invalid prefixes fail the tool call
   - Prefix count matches segment count for composed commands

7. **Config customization works**
   - Users can add/remove from allow list
   - Users can add/remove from deny list
   - Server vs CLI whitelists apply correctly

### Testing Approach

**Unit tests:**
- Prefix validation logic
- Command parsing (bashlex integration)
- Subshell detection
- Deny list matching
- Allow list matching
- Config merging (defaults + user customization)

**Integration tests:**
- End-to-end command execution with mocked user approval
- Mode switching (CLI flags)
- Error message content verification

**LLM evaluation tests:**
- AI correctly provides `suggested_prefix` / `suggested_prefixes`
- AI recovers from denial errors appropriately
- AI doesn't attempt deny-listed commands after seeing system prompt

---

**Note**: This is a planning document. No code changes will be made until the plan is approved.

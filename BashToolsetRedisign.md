# Bash Toolset Redesign Plan

This document outlines the plan for redesigning the bash toolset in HolmesGPT.

## Approach

**Build a brand new plugin from scratch** while keeping the existing bash toolset as a deprecated plugin. This allows:
- Clean slate design without legacy constraints
- Gradual migration path for users
- Side-by-side comparison during transition period

## Current State

_To be analyzed together_

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
- If user grants permission, that permission is **saved for the session**
- Subsequent uses of the same command (prefix?) don't require re-approval

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

**Fallback:** If bashlex fails to parse (malformed command), treat entire input as single segment and let bash report the error at execution time.

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

### #2: Two Tools Architecture

**Approach:** Create two separate tools that share execution code:

| Tool Name | Mode | Behavior |
|-----------|------|----------|
| `bash` | Interactive | Can request user permission for non-whitelisted commands |
| `bash_limited` | Non-interactive | Only whitelisted commands, fails otherwise |

**Code structure:**

```
bash_toolset/
├── executor.py          # Shared execution logic
├── whitelist.py         # Shared whitelist validation
├── bash_tool.py         # Interactive tool (can ask permission)
└── bash_limited_tool.py # Non-interactive tool (strict whitelist)
```

**Config determines which tool is enabled:**

```yaml
toolsets:
  bash:
    mode: interactive  # or "limited"
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

## New Plugin Design

_To be discussed_

## Deprecation Strategy

_How to handle the old bash toolset_

## Implementation Plan

_To be finalized_

---

**Note**: This is a planning document. No code changes will be made until the plan is approved.

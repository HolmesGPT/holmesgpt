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

### #3: Communicating Whitelist to AI (Non-interactive Mode)

In `bash_limited` mode, the AI must know what commands are available.

**Option A: Include whitelist in tool description**

```
Tool: bash_limited
Description: Execute bash commands. Only the following commands are allowed:
- kubectl get, kubectl describe, kubectl logs
- cat, grep, head, tail
- ls, find, stat
Do not attempt other commands - they will fail.
```

- Pros: AI sees it every time, clear guidance
- Cons: Long tool description, must stay in sync with actual whitelist

**Option B: Separate tool to query whitelist**

```
Tool: list_allowed_commands
Returns: ["kubectl get", "kubectl describe", ...]
```

- Pros: Single source of truth, AI can check dynamically
- Cons: Extra tool call, AI might not call it

**Option C: Include in system prompt**

- Pros: Prominent placement
- Cons: Couples system prompt to toolset config

**Recommendation:** Option A with auto-generation - the tool description is dynamically built from the actual whitelist config.

## New Plugin Design

_To be discussed_

## Deprecation Strategy

_How to handle the old bash toolset_

## Implementation Plan

_To be finalized_

---

**Note**: This is a planning document. No code changes will be made until the plan is approved.

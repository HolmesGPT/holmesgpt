# Holmes Delegation Architecture: Claude Code & OpenHands Integration

## Problem Statement

Holmes diagnoses infrastructure issues by gathering data through tool calls, but currently stops at
analysis — it cannot act on its findings by modifying code, opening PRs, or making configuration
changes. The goal is to let Holmes delegate remediation tasks to coding agents (Claude Code or
OpenHands) that can check out a git repo, make changes, and open a pull request.

Key constraints:

- **Holmes's tool model is synchronous**: Tools execute via `ThreadPoolExecutor(max_workers=16)`,
  with the LLM blocked waiting for results. Typical tool calls return in seconds. Coding agents
  take minutes.
- **Server mode runs in a container**: No local git repos, limited filesystem, ephemeral storage.
  The Helm deployment has no persistent volumes by default.
- **CLI mode runs on user's machine**: Has filesystem access, can spawn subprocesses, may already
  have repos checked out.
- **Coding agents need their own environment**: File system, git credentials, ability to run
  tests/linting, and access to the target repository.

## Agent Capabilities

| Agent | Interface | Deployment | Notes |
|-------|-----------|------------|-------|
| **Claude Code** | CLI subprocess, SDK (Python/TS), MCP client | Local process | Agentic coding with tool use. Can use MCP servers. Has `claude -p` non-interactive mode. |
| **OpenHands** | REST API, WebSocket, CLI | Container/service | Open-source coding agent. Has a headless mode and REST API for programmatic use. |

## Architecture Options

### Option A: Synchronous Tool Delegation (Simplest, CLI-only)

Holmes exposes a `delegate_to_coding_agent` tool that directly spawns the coding agent as a
subprocess and blocks until it completes.

```
Holmes LLM Loop
  └─ tool_call: delegate_to_coding_agent(task, repo_url, branch)
       └─ subprocess: claude -p "checkout repo X, fix Y, open PR"
            └─ (runs for 2-10 minutes)
       └─ returns: { pr_url, summary }
  └─ LLM incorporates result into final answer
```

**Implementation**: New Python toolset with a single tool that:
1. Creates a temp directory
2. Runs `git clone <repo_url>` into it
3. Invokes `claude -p "<task_description>"` or `openhands-cli` with the task
4. Captures the output (PR URL, summary)
5. Cleans up the temp directory
6. Returns a `StructuredToolResult`

**Toolset sketch** (YAML config + Python tool):

```python
class CodingAgentToolset(Toolset):
    """Delegates code changes to an external coding agent."""

    def __init__(self):
        super().__init__(
            name="coding_agent",
            description="Delegate code modifications to a coding agent that can checkout repos and open PRs",
            tools=[DelegateToAgentTool(self)],
            tags=[ToolsetTag.CLI],  # CLI-only
        )

class DelegateToAgentTool(Tool):
    def _invoke(self, params, context) -> StructuredToolResult:
        # 1. Clone repo to tempdir
        # 2. Run coding agent subprocess with task prompt
        # 3. Parse output for PR URL
        # 4. Return result
```

**Pros**:
- Minimal architecture change — fits existing tool model
- Holmes LLM sees the result and can discuss it with the user
- No new infrastructure needed

**Cons**:
- Blocks the LLM loop for minutes (not viable for server mode with request timeouts)
- Only works in CLI mode where long blocking is acceptable
- No progress visibility during execution
- Single-threaded: Holmes can't do other work while waiting

**Best for**: Local CLI usage where a developer asks Holmes to investigate *and* fix an issue.

---

### Option B: Async Job with Polling Tool (Server-compatible)

Holmes dispatches the task to a coding agent service, gets back a job ID, and uses a polling tool
to check status. The LLM loop continues with other work or returns to the user with a "job
started" message.

```
Holmes LLM Loop
  └─ tool_call: submit_coding_task(task, repo_url, branch)
       └─ POST /api/tasks to coding agent service
       └─ returns: { job_id: "abc-123", status: "queued" }
  └─ LLM: "I've submitted a PR task. Job ID: abc-123"

  [Later, in a follow-up conversation or scheduled check]
  └─ tool_call: check_coding_task(job_id="abc-123")
       └─ GET /api/tasks/abc-123
       └─ returns: { status: "completed", pr_url: "https://..." }
```

**Implementation**:

1. **Coding Agent Service**: A sidecar or separate deployment that:
   - Accepts task submissions via REST API
   - Manages a job queue (in-memory, Redis, or database-backed)
   - Spawns coding agent sessions (Claude Code SDK or OpenHands API)
   - Stores results keyed by job ID

2. **Holmes Toolset**: Two tools:
   - `submit_coding_task(task, repo_url, base_branch)` → submits and returns job ID
   - `check_coding_task(job_id)` → returns status/result

3. **Notification** (optional): The service can call back to Holmes's `/api/chat` or write results
   to the investigation's source system (Slack, Jira comment, etc.)

**Deployment in Helm**:

```yaml
# values.yaml addition
codingAgent:
  enabled: false
  type: "claude-code"  # or "openhands"
  image: "your-org/holmes-coding-agent:latest"
  env:
    ANTHROPIC_API_KEY: "{{ .Values.anthropicApiKey }}"
    GITHUB_TOKEN: "{{ .Values.githubToken }}"
```

The coding agent service runs as a sidecar deployment (similar to existing MCP server sidecars
in `helm/holmes/templates/mcp-servers/`).

**Pros**:
- Non-blocking — Holmes returns immediately, compatible with server request timeouts
- Scales independently (coding agent service can queue and rate-limit)
- Clean separation of concerns
- Job state persists across conversations

**Cons**:
- Requires additional infrastructure (the agent service)
- Holmes LLM can't interactively guide the coding agent
- Needs a mechanism to reconnect results to the original investigation context
- More complex to implement and operate

**Best for**: Production Helm deployments where Holmes investigates alerts and automatically
submits fix PRs.

---

### Option C: MCP Server Bridge (Leverages Existing Infrastructure)

Deploy Claude Code or OpenHands as an MCP server. Holmes connects via the existing
`RemoteMCPToolset` infrastructure — no new tool types needed.

```
Holmes LLM Loop
  └─ tool_call: create_pr(task, repo_url, branch)  # MCP tool
       └─ MCP SSE/HTTP → Coding Agent MCP Server
            └─ Agent checks out repo, makes changes, opens PR
       └─ returns: { pr_url, files_changed, summary }
```

**Implementation**:

1. **MCP Wrapper Service**: A thin MCP server that:
   - Exposes tools like `create_pr`, `apply_fix`, `run_tests`
   - On invocation, spawns a coding agent session
   - Manages workspace lifecycle (clone, work, cleanup)
   - Returns results in MCP format

2. **Configuration** (existing pattern in Holmes):

```yaml
# values.yaml
mcp_servers:
  coding_agent:
    type: mcp
    url: "http://coding-agent-mcp:8000/mcp/messages"
    mode: "sse"
    headers:
      Authorization: "Bearer {{ env.CODING_AGENT_TOKEN }}"
```

3. **Helm Sidecar** (follows existing pattern in `helm/holmes/templates/mcp-servers/`):

```yaml
# templates/mcp-servers/coding-agent.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-coding-agent-mcp
spec:
  template:
    spec:
      containers:
        - name: coding-agent-mcp
          image: "{{ .Values.codingAgent.image }}"
          ports:
            - containerPort: 8000
          env:
            - name: ANTHROPIC_API_KEY
              valueFrom:
                secretKeyRef: ...
            - name: GITHUB_TOKEN
              valueFrom:
                secretKeyRef: ...
```

**Timeout concern**: MCP calls go through the same synchronous tool execution path, so long
operations still block. The MCP wrapper would need to either:
- (a) Implement internal async: accept the MCP call, start the agent, return a job handle, require
  a second MCP call to poll — essentially Option B behind an MCP facade.
- (b) Use SSE streaming to keep the connection alive while the agent works, with periodic heartbeats
  to prevent timeout. The MCP server sends progress updates over the SSE stream.

**Pros**:
- Zero changes to Holmes core — uses existing MCP infrastructure
- Familiar deployment pattern (same as AWS/Azure/GCP MCP sidecars)
- MCP is a standard protocol — other tools can also use the coding agent server
- Configuration via `values.yaml`, same as other MCP servers

**Cons**:
- MCP tool calls are still synchronous from the LLM's perspective
- Timeout handling requires careful design (heartbeats or async polling)
- The MCP wrapper adds an abstraction layer that may obscure errors
- Building the MCP wrapper is non-trivial

**Best for**: Teams already using MCP sidecars who want a consistent deployment model.

---

### Option D: Hybrid — Direct CLI + Async Server (Recommended)

Combine Options A and B: use direct subprocess invocation for CLI mode and an async job service
for server mode, behind a unified toolset interface.

```
                    ┌──────────────────────────────┐
                    │    CodingAgentToolset         │
                    │                               │
                    │  ┌─────────┐  ┌────────────┐  │
                    │  │ CLI Mode│  │Server Mode │  │
                    │  │ (sync)  │  │ (async)    │  │
                    │  └────┬────┘  └─────┬──────┘  │
                    │       │             │         │
                    └───────┼─────────────┼─────────┘
                            │             │
                  ┌─────────▼───┐   ┌─────▼──────────────┐
                  │  Subprocess │   │ Coding Agent       │
                  │  claude -p  │   │ Service (REST API) │
                  │  or         │   │                    │
                  │  oh-cli     │   │  ┌──────────────┐  │
                  └─────────────┘   │  │ Job Queue    │  │
                                    │  │ Worker Pool  │  │
                                    │  │ Result Store │  │
                                    │  └──────────────┘  │
                                    └────────────────────┘
```

**Unified toolset with mode detection**:

```python
class CodingAgentToolset(Toolset):
    config_classes = [CodingAgentConfig]

    def __init__(self):
        tools = [SubmitCodingTaskTool(self), CheckCodingTaskTool(self)]
        if self.config.mode == "cli":
            tools = [DelegateToAgentTool(self)]  # synchronous, single tool
        super().__init__(
            name="coding_agent",
            tools=tools,
            tags=[ToolsetTag.CORE],
        )
```

**CLI mode**: Single `delegate_to_coding_agent` tool that blocks, clones repo, runs agent, returns PR URL.

**Server mode**: Two tools (`submit_coding_task` + `check_coding_task`) backed by the sidecar service.

**Pros**:
- Best experience in each mode
- CLI users get immediate, interactive results
- Server users get non-blocking async behavior
- Single toolset, single configuration surface
- Clean upgrade path from CLI to server

**Cons**:
- Two code paths to maintain
- Slightly more complex toolset implementation

---

## Detailed Design: Coding Agent Service (for Options B, C, D server mode)

### Service Architecture

```
┌─────────────────────────────────────────────────┐
│              Coding Agent Service                │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ REST API │  │ Job Queue│  │ Agent Runner  │  │
│  │ /submit  │──│ (deque)  │──│               │  │
│  │ /status  │  │          │  │ ┌───────────┐ │  │
│  │ /cancel  │  └──────────┘  │ │Claude Code│ │  │
│  └──────────┘                │ │  Session  │ │  │
│                              │ └───────────┘ │  │
│  ┌───────────────┐           │ ┌───────────┐ │  │
│  │ Result Store  │           │ │ OpenHands │ │  │
│  │ (job_id →     │◄──────────│ │  Session  │ │  │
│  │  result)      │           │ └───────────┘ │  │
│  └───────────────┘           └───────────────┘  │
└─────────────────────────────────────────────────┘
```

### REST API

```
POST /api/tasks
  Body: { task, repo_url, base_branch, agent_type }
  Response: { job_id, status: "queued" }

GET /api/tasks/{job_id}
  Response: { job_id, status, pr_url?, summary?, error?, logs? }

DELETE /api/tasks/{job_id}
  Response: { cancelled: true }
```

### Job Lifecycle

```
queued → cloning → running → [completed | failed]
```

Each job:
1. Clones the repository to an ephemeral workspace (`/tmp/jobs/<job_id>/`)
2. Creates a working branch
3. Writes a task prompt file with Holmes's investigation context
4. Spawns the coding agent:
   - **Claude Code**: `claude -p --output-format json "$(cat task.md)"` in the repo directory
   - **OpenHands**: POST to OpenHands API with workspace mount
5. Monitors for completion or timeout
6. Extracts PR URL from agent output
7. Cleans up workspace

### Git Credential Handling

| Mode | Mechanism |
|------|-----------|
| Helm/Server | K8s Secret mounted as git credential helper, or GitHub App token |
| CLI/Local | User's existing git credentials (`~/.gitconfig`, SSH agent, credential helper) |

For server mode, the recommended approach is a **GitHub App** with PR creation permissions:
- Install the app on target repositories
- Store the app private key as a K8s Secret
- The coding agent service generates short-lived installation tokens
- No long-lived PATs to rotate

### Workspace Isolation

Each coding task gets an isolated workspace:

```
/tmp/coding-agent-jobs/
  └── <job_id>/
      ├── repo/           # git clone target
      ├── task.md          # task description from Holmes
      ├── output.json      # agent output
      └── logs/            # agent session logs
```

For Helm deployments, use an `emptyDir` volume with size limits:

```yaml
volumes:
  - name: workspace
    emptyDir:
      sizeLimit: 2Gi
```

### Security Considerations

- **Read-only Holmes**: Holmes itself remains read-only. It composes the task description; the
  coding agent service handles all write operations.
- **Scoped git credentials**: Use GitHub App installation tokens scoped to specific repositories.
- **PR review required**: The coding agent creates PRs, never merges directly. Human review is
  the gate.
- **Network policy**: The coding agent service needs outbound access to GitHub/GitLab and the
  LLM API. It does NOT need access to the K8s API or other cluster services.
- **Resource limits**: Enforce CPU/memory limits and workspace size limits per job.
- **Timeout**: Hard timeout per job (configurable, default 10 minutes). Kill agent process on
  expiry.

---

## Task Prompt Design

Holmes constructs the task prompt from its investigation context:

```markdown
## Context
Holmes investigated alert: {alert_title}

### Investigation Summary
{investigation_analysis}

### Relevant Data
{tool_call_results_summary}

## Task
{user_or_holmes_instruction}

## Constraints
- Create changes on a new branch from `{base_branch}`
- Open a pull request with a clear description
- Include the investigation context in the PR description
- Run tests before committing if a test command is available
- Do not modify files unrelated to the fix
```

This prompt is what gets passed to Claude Code or OpenHands. The coding agent has full autonomy
within the workspace to explore, edit, test, and commit.

---

## Configuration

### CLI Mode (`~/.holmes/config.yaml`)

```yaml
coding_agent:
  enabled: true
  agent: claude-code          # or "openhands"
  # Claude Code settings
  claude_code_path: claude    # path to claude CLI binary
  # OpenHands settings (alternative)
  openhands_url: http://localhost:3000
  # Common
  default_base_branch: main
  timeout_seconds: 600
  github_token: ${GITHUB_TOKEN}
```

### Server Mode (Helm `values.yaml`)

```yaml
codingAgent:
  enabled: false
  agent: claude-code
  serviceUrl: http://{{ .Release.Name }}-coding-agent:8080
  # Or deploy as sidecar:
  deploy:
    enabled: true
    image: your-org/holmes-coding-agent:v1
    resources:
      requests:
        cpu: 500m
        memory: 1Gi
      limits:
        cpu: "2"
        memory: 4Gi
    env:
      ANTHROPIC_API_KEY:
        secretKeyRef:
          name: coding-agent-secrets
          key: anthropic-api-key
      GITHUB_APP_PRIVATE_KEY:
        secretKeyRef:
          name: coding-agent-secrets
          key: github-app-key
```

---

## Comparison Matrix

| Criteria | A: Sync Subprocess | B: Async Job Service | C: MCP Bridge | D: Hybrid (Recommended) |
|----------|-------------------|---------------------|---------------|------------------------|
| CLI support | Direct | Via local service | Via local MCP | Direct (best UX) |
| Server support | No (timeout) | Yes | Yes (with heartbeat) | Yes |
| Infrastructure | None | Agent service | MCP wrapper + agent | Agent service (server only) |
| Holmes core changes | New toolset | New toolset | None (MCP config) | New toolset |
| Progress visibility | Stdout streaming | Poll-based | SSE heartbeat | Mode-dependent |
| Complexity | Low | Medium | Medium | Medium |
| Interactive guidance | Yes (CLI) | No | No | Yes (CLI) / No (server) |
| Scalability | Single job | Queue-based | Single job | Queue-based (server) |

---

## Recommendation

**Option D (Hybrid)** provides the best balance:

1. **Phase 1 — CLI mode** (start here):
   Build the synchronous `DelegateToAgentTool` as a new Python toolset. This validates the
   prompt engineering (how Holmes describes tasks to the coding agent) and the git/PR workflow
   with minimal infrastructure. Ship this as an experimental CLI-only feature.

2. **Phase 2 — Coding Agent Service**:
   Build the standalone service with REST API, job queue, and agent runner. This is a separate
   container image that deploys alongside Holmes. Add the async `submit_coding_task` /
   `check_coding_task` tools to the toolset.

3. **Phase 3 — Helm integration**:
   Add the coding agent service as an optional sidecar in the Helm chart (following the existing
   MCP server sidecar pattern). Add `codingAgent` section to `values.yaml`.

4. **Phase 4 — Callback integration**:
   When a coding task completes, push results back to the source system (Slack thread, Jira
   comment, PagerDuty note) so the investigation loop is closed without requiring the user to
   poll.

### Why not pure MCP (Option C)?

While MCP reuses existing infrastructure, the synchronous call model is a poor fit for
minutes-long operations. You'd end up reimplementing async job semantics (submit/poll) inside
MCP tool calls anyway, making the MCP layer a leaky abstraction. Better to build the async
service directly and expose it via purpose-built Holmes tools.

### Claude Code vs OpenHands

Both are viable. The toolset should support both via configuration:

- **Claude Code** is the simpler integration for Phase 1 — it's a single CLI binary invoked via
  `subprocess.run()`. The `-p` flag accepts a prompt, `--output-format json` gives structured
  output. It handles git operations natively.
- **OpenHands** is better for server mode — it has a proper REST API, supports workspace isolation
  out of the box, and runs as a container. It's also open-source, which may matter for
  self-hosted deployments.

The coding agent service in Phase 2 can abstract over both, selecting the agent backend based on
configuration.

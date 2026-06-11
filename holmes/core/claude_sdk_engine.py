"""Claude Agent SDK investigation engine (experimental).

An alternative to ToolCallingLLM that runs HolmesGPT investigations on the
Claude Agent SDK (the engine behind Claude Code). Instead of ~230 bespoke
function-tools, the model gets general primitives — Bash (raw kubectl), a
filesystem workspace (Read/Grep/Glob), TodoWrite, and the SDK's *native* Task
sub-agent tool. This engine exists to A/B that architecture against
ToolCallingLLM on the same evals and judge.

Transport: the SDK speaks the Anthropic Messages API. Point ANTHROPIC_BASE_URL
at any Anthropic-compatible endpoint (e.g. a LiteLLM proxy bridging to
OpenRouter). Selected via HOLMES_ENGINE=claude-sdk.

Eval parity: the baseline ToolCallingLLM engine investigates with a read-only
Kubernetes toolset (get/describe/logs/top/events) and no Secret access. To keep
the A/B apples-to-apples, this engine is given the same investigation surface:
a PreToolUse hook keeps Bash to that read-only kubectl surface so both engines
draw conclusions from the same evidence (logs/events/status), and neither can
reach data the other cannot.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# The baseline engine's Kubernetes toolset is read-only and exposes no Secret
# access. These patterns keep the SDK engine's Bash to that same surface, so the
# two engines investigate from identical evidence. (Pure eval-parity, not a
# security boundary — both lists mirror what the baseline toolset can/can't do.)
_OUT_OF_SCOPE_KUBECTL_RE = re.compile(
    r"\b(get|describe)\b[^|;&]*\bsecret"          # baseline toolset has no secret access
    r"|\bkubectl\b[^|;&]*\b(exec|cp|port-forward|attach|proxy"  # baseline is non-interactive
    r"|edit|apply|delete|patch|create|replace|scale|cordon|drain)\b",  # baseline is read-only
    re.IGNORECASE,
)


@dataclass
class SDKResult:
    """Subset of LLMResult that the eval harness consumes."""

    result: Optional[str] = None
    tool_calls: List[Any] = field(default_factory=list)
    num_llm_calls: Optional[int] = None
    total_cost: float = 0.0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: Optional[int] = None
    reasoning_tokens: int = 0
    messages: Optional[list] = None
    metadata: Optional[dict] = None


SDK_SYSTEM_PROMPT = """You are HolmesGPT, an SRE/DevOps investigation agent with read access to a Kubernetes cluster and general Unix tools.

Investigate the user's question by gathering real evidence with your tools, then answer.

How to operate:
* Use the Bash tool for kubectl and standard CLI tools (kubectl get/describe/logs/top/events, grep, jq, awk, etc.). kubectl is configured for the target cluster.
* Your access is read-only: investigate via logs, events, and resource status. (Some out-of-scope kubectl verbs and Secret reads are unavailable; rely on logs/events/status.)
* Something "Running" / "Ready" is not necessarily healthy — check the application's actual runtime behavior in its logs, not just pod status.
* When analyzing behavior over time, read the FULL log history, not just the most recent lines: trends (growing latency/backlog, collapsing ratios) are invisible in a tail sample.
* Be calibrated: distinguish sustained trends and real failures from normal jitter or steady-but-high/low values. Do not report stable values or noise as problems.
* Back every claim with concrete evidence: exact resource names, namespaces, error messages/codes, log lines, counts.

Delegation (Task tool): you have a `worker` sub-agent. Delegate when the investigation fans out over many independent targets that EACH need substantial evidence read (e.g. full log histories across many services) — dispatch one self-contained Task per target, in parallel, and synthesize their reports. For anything you can investigate well yourself, do it directly; delegation has real token/latency cost.

Give a final answer with specific, actionable findings."""

# Delegation-mandate variant (HOLMES_SDK_DELEGATE=mandatory). Same engine; the
# only change is that wide multi-target audits MUST fan out to one worker per
# target instead of the lead reading every target itself. Used to A/B whether
# delegation helps when actually exercised, holding everything else constant.
DELEGATE_MANDATE = """

IMPORTANT — delegation policy for this run: when the task is an audit/review spanning more than a handful of independent targets (e.g. many namespaces/services), you MUST delegate. Dispatch ONE self-contained `worker` Task per target IN PARALLEL (all in a single turn), each instructed to read that target's full evidence and report its verdict with exact names/codes/log lines. Do not investigate the targets yourself — your job is to fan out, then synthesize the workers' reports into the final answer. Only investigate directly for targets a worker flags as ambiguous."""


def _system_prompt(cluster_name: Optional[str]) -> str:
    sp = SDK_SYSTEM_PROMPT
    if os.environ.get("HOLMES_SDK_DELEGATE", "").lower() == "mandatory":
        sp += DELEGATE_MANDATE
    if cluster_name:
        sp += f"\n\nYour kubectl context is the cluster `{cluster_name}`."
    return sp

WORKER_AGENT_PROMPT = """You are a focused investigation worker. You receive ONE self-contained task from a lead agent and see only that task — not the user's original question.

* Investigate thoroughly with your tools before answering; never guess when a tool can verify.
* Your access is read-only: use logs, events, and resource status.
* For over-time analysis, read the FULL log history, not just a tail; distinguish real trends/failures from normal jitter or steady values.
* Your final message is your entire report to the lead agent. Lead with the conclusion (root cause, or "healthy — no issues"), then back it with exact names, namespaces, error codes, and log lines verbatim."""


def is_sdk_engine_enabled() -> bool:
    return os.environ.get("HOLMES_ENGINE", "").lower() == "claude-sdk"


def _resolve_cli_path() -> Optional[str]:
    # Allow overriding the claude binary location (the packaged one may sit on a
    # path the current user can't traverse).
    return os.environ.get("HOLMES_CLAUDE_CLI_PATH") or None


async def _run(user_prompt: str, model: str, cluster_name: Optional[str]) -> SDKResult:
    from claude_agent_sdk import (
        AgentDefinition,
        AssistantMessage,
        ClaudeAgentOptions,
        HookMatcher,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        query,
    )

    worker_model = os.environ.get("HOLMES_SUBAGENT_MODEL", model)
    max_turns = int(os.environ.get("HOLMES_SDK_MAX_TURNS", "80"))
    audit_path = os.environ.get("HOLMES_SDK_AUDIT_LOG")

    # PreToolUse fires for every Bash call (lead + sub-agents). It (1) records
    # the command to an optional audit file so eval runs can be inspected, and
    # (2) keeps Bash to the baseline engine's read-only kubectl surface for a
    # fair A/B.
    async def gate_bash(input_data: dict, tool_use_id: Optional[str], context: Any):
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {}) or {}
        if tool_name != "Bash":
            return {}
        command = tool_input.get("command", "")
        if audit_path:
            try:
                with open(audit_path, "a") as fh:
                    fh.write(command.replace("\n", " ") + "\n")
            except Exception:
                pass
        if _OUT_OF_SCOPE_KUBECTL_RE.search(command):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Out of scope for this read-only investigation. "
                        "Use kubectl get/describe/logs/top/events and resource status instead."
                    ),
                }
            }
        return {}

    agents = {
        "worker": AgentDefinition(
            description="Focused investigation worker for one self-contained task. Use for per-target deep dives when fanning out over many targets.",
            prompt=WORKER_AGENT_PROMPT,
            tools=["Bash", "Read", "Grep", "Glob"],
            model=worker_model,
        )
    }

    system_prompt = _system_prompt(cluster_name)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        max_turns=max_turns,
        allowed_tools=["Bash", "Read", "Grep", "Glob", "TodoWrite", "Task"],
        hooks={"PreToolUse": [HookMatcher(hooks=[gate_bash])]},
        agents=agents,
        cli_path=_resolve_cli_path(),
        env={
            "ANTHROPIC_BASE_URL": os.environ["ANTHROPIC_BASE_URL"],
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "dummy"),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "KUBECONFIG": os.environ.get("KUBECONFIG", ""),
            "PATH": os.environ["PATH"],
        },
    )

    async def prompt_stream():
        yield {
            "type": "user",
            "message": {"role": "user", "content": user_prompt},
        }

    final_text: Optional[str] = None
    tool_calls: List[Any] = []
    num_turns = 0
    result = SDKResult()

    async for msg in query(prompt=prompt_stream(), options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    final_text = block.text
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append(_describe_tool_use(block))
        elif isinstance(msg, ResultMessage):
            num_turns = msg.num_turns or 0
            if msg.result:
                final_text = msg.result
            result.total_cost = float(msg.total_cost_usd or 0.0)
            usage = msg.usage or {}
            result.prompt_tokens = int(usage.get("input_tokens", 0) or 0)
            result.completion_tokens = int(usage.get("output_tokens", 0) or 0)
            cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
            result.cached_tokens = cache_read or None
            result.total_tokens = (
                result.prompt_tokens + result.completion_tokens + cache_read
            )

    result.result = final_text
    result.tool_calls = tool_calls
    result.num_llm_calls = num_turns
    return result


def _describe_tool_use(block: Any) -> Any:
    """Build a minimal object with `.tool_name`/`.description`/`.result` so the
    eval harness's tool-call reporting (and include_tool_calls scoring) works."""
    name = getattr(block, "name", "tool")
    inp = getattr(block, "input", {}) or {}
    if name == "Bash":
        desc = f"Bash: {inp.get('command', '')[:160]}"
    elif name == "Task":
        desc = f"Task(delegate): {inp.get('description', '')}"
    else:
        params = ", ".join(f"{k}={v}" for k, v in list(inp.items())[:3])
        desc = f"{name}: {params}"[:200]

    @dataclass
    class _TC:
        tool_name: str
        description: str
        result: Any = ""
        images: Optional[list] = None

    return _TC(tool_name=name, description=desc)


def run_investigation(
    user_prompt: str, model: str, cluster_name: Optional[str] = None
) -> SDKResult:
    """Synchronous entry point used by the eval harness."""
    import anyio

    if "ANTHROPIC_BASE_URL" not in os.environ:
        raise RuntimeError(
            "claude-sdk engine requires ANTHROPIC_BASE_URL (point it at an "
            "Anthropic-compatible endpoint, e.g. a LiteLLM proxy)."
        )
    return anyio.run(_run, user_prompt, model, cluster_name)

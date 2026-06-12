"""Claude Agent SDK investigation engine.

Runs HolmesGPT investigations on the Claude Agent SDK (the engine behind Claude
Code) instead of the bespoke ToolCallingLLM + ~230 function-tools. The model
gets general primitives — Bash (kubectl + curl + standard CLI), a filesystem
workspace (Read/Grep/Glob), TodoWrite, and the SDK's native Task sub-agent —
and reaches every data source the same way a human SRE would: kubectl for the
cluster, curl for HTTP APIs (Prometheus, Loki, Elasticsearch, Datadog, Grafana,
…). Data-source URLs and credentials are discovered from the eval's declared
toolsets and handed to the agent (URLs in the prompt, secrets via env vars it
can reference in curl).

Transport: the SDK speaks the Anthropic Messages API. Point ANTHROPIC_BASE_URL
at any Anthropic-compatible endpoint (e.g. a LiteLLM proxy bridging to a
provider). Selected via HOLMES_ENGINE=claude-sdk.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

_ENV_REF_RE = re.compile(r"\{\{\s*env\.([A-Z0-9_]+)\s*\}\}")

# Short "how to query this over HTTP" hints per data-source toolset family, so
# the agent reaches for the right REST endpoint quickly. Generic on purpose —
# the model knows these APIs; this just removes guesswork about base paths.
_QUERY_HINTS = {
    "elasticsearch": "Elasticsearch/OpenSearch REST API. List indices with `GET /_cat/indices?v`, search with `POST /<index>/_search` (JSON query DSL).",
    "opensearch": "OpenSearch REST API. `GET /_cat/indices?v`, `POST /<index>/_search`.",
    "grafana/loki": "Loki HTTP API. Query logs with `GET /loki/api/v1/query_range?query=<logql>&start=<rfc3339>&end=<rfc3339>&limit=N`. List labels with `GET /loki/api/v1/labels`.",
    "loki": "Loki HTTP API. `GET /loki/api/v1/query_range?query=<logql>&start=&end=&limit=`.",
    "prometheus": "Prometheus HTTP API. Instant: `GET /api/v1/query?query=<promql>`. Range: `GET /api/v1/query_range?query=&start=&end=&step=`. Metadata: `GET /api/v1/label/__name__/values`.",
    "grafana": "Grafana HTTP API under /api (datasources, dashboards). Auth with the API key as `Authorization: Bearer <key>`.",
    "datadog": "Datadog HTTP API (api.datadoghq.com). Auth with DD-API-KEY and DD-APPLICATION-KEY headers. Logs: `POST /api/v2/logs/events/search`.",
}


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


SDK_SYSTEM_PROMPT = """You are HolmesGPT, an SRE/DevOps investigation agent. You investigate infrastructure and application problems by gathering real evidence with your tools, then answering.

Tools and how to reach data:
* Bash: run kubectl for the Kubernetes cluster (get/describe/logs/top/events), and curl for HTTP data-source APIs. Standard CLI (grep, jq, awk, sort, wc, date) is available.
* For any HTTP data source listed under "Available data sources" below, query it with curl. Credentials, when needed, are in the named environment variables — reference them in the command (e.g. `curl -H "Authorization: ApiKey $ELASTICSEARCH_API_KEY" ...`); do not print their values.
* Read/Grep/Glob operate on the local filesystem if you save tool output to files.

Investigation discipline:
* Something "Running"/"Ready" is not necessarily healthy — check the application's actual runtime behavior in its logs, not just status.
* When analyzing behavior over time, read the FULL history (complete logs/series), not just the most recent lines — trends are invisible in a tail sample.
* Be calibrated: distinguish sustained trends and real failures from normal jitter or steady-but-high/low values. Do not report stable values or noise as problems.
* Treat error messages as exact diagnostic evidence: `authentication failed`/`password authentication failed` for user X means X EXISTS; `role/user does not exist` means it is absent. These are mutually exclusive — never hedge one as the other.
* Adjacent / similarly-named entities — be transparent AND useful. If the exact resource/service the user named has NO data, but you find data for a similarly-named one (same prefix, sibling service, etc.): (1) explicitly state, using the user's verbatim name, that you found no data for that exact entity; (2) report what you DID find in the related entity; (3) clearly label those findings as coming from the different, related entity — phrases like "I found logs for X (a different service from <user's name>)". Never silently merge the user's name into the entity you actually found. This disclosure is required even when a close match exists.
* For Kubernetes permission errors (`Error from server (Forbidden)`), say so explicitly and identify the missing resource/verb rather than treating it as "no problem found".
* Use hedging language (possible, likely, may) for root-cause claims you cannot directly confirm from tool output; present directly-observed errors as facts.
* Back every claim with concrete evidence: exact resource names, namespaces, error messages/codes, log lines, counts.

Delegation (Task tool): you have a `worker` sub-agent. When an investigation fans out over many independent targets that EACH need substantial evidence read (e.g. full log histories across many services), dispatch ONE self-contained `worker` Task per target IN PARALLEL (all in a single turn) and synthesize their reports. For anything you can investigate well yourself, do it directly — delegation has real token/latency cost.

Give a final answer with specific, actionable findings."""

WORKER_AGENT_PROMPT = """You are a focused investigation worker. You receive ONE self-contained task from a lead agent and see only that task — not the user's original question.

* Investigate thoroughly with your tools (kubectl, curl for HTTP data sources) before answering; never guess when a tool can verify. Credentials are in the named env vars; reference them, don't print them.
* For over-time analysis, read the FULL history, not just a tail; distinguish real trends/failures from normal jitter or steady values.
* Your final message is your entire report to the lead agent. Lead with the conclusion (root cause, or "healthy — no issues"), then back it with exact names, namespaces, error codes, and log lines verbatim."""

SDK_REPORT_SUFFIX = ""


def is_sdk_engine_enabled() -> bool:
    return os.environ.get("HOLMES_ENGINE", "").lower() == "claude-sdk"


def _resolve_cli_path() -> Optional[str]:
    return os.environ.get("HOLMES_CLAUDE_CLI_PATH") or None


def _load_toolset_configs(test_folder: Optional[str]) -> Dict[str, dict]:
    """Merge the eval's toolsets.yaml over the shared default_toolsets.yaml,
    returning {toolset_name: entry}. Entries keep enabled flag + config."""
    merged: Dict[str, dict] = {}
    default_path = (
        Path(__file__).resolve().parents[2]
        / "tests" / "llm" / "utils" / "default_toolsets.yaml"
    )
    for path in (default_path, Path(test_folder) / "toolsets.yaml" if test_folder else None):
        if not path or not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception:
            continue
        for name, entry in (data.get("toolsets") or {}).items():
            if isinstance(entry, dict):
                merged[name] = entry
    return merged


def discover_data_sources(
    test_folder: Optional[str],
) -> Tuple[str, Dict[str, str]]:
    """Inspect the eval's declared toolsets and produce (prompt_section, env).

    prompt_section: human-readable "Available data sources" block listing each
    enabled HTTP data source's URL, auth env var, and a query hint.
    env: extra environment variables (resolved credential values) to expose to
    the agent's bash so it can curl with them.
    """
    configs = _load_toolset_configs(test_folder)
    lines: List[str] = []
    env: Dict[str, str] = {}

    for name, entry in sorted(configs.items()):
        if not entry.get("enabled", False):
            continue
        cfg = entry.get("config") or {}
        url = cfg.get("api_url") or cfg.get("url") or cfg.get("host")
        if not url:
            continue  # not an HTTP data source (e.g. kubernetes/core, bash)

        # Resolve env refs in url/credentials; pass referenced vars through.
        auth_vars: List[str] = []
        for key, val in cfg.items():
            if not isinstance(val, str):
                continue
            for var in _ENV_REF_RE.findall(val):
                if var in os.environ:
                    env[var] = os.environ[var]
                if key != "api_url" and key != "url":
                    auth_vars.append(var)
        resolved_url = _ENV_REF_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), url)

        family = name.split("/")[0]
        hint = _QUERY_HINTS.get(name) or _QUERY_HINTS.get(family) or ""
        auth_note = ""
        if auth_vars:
            auth_note = f" Auth: credential(s) in env var(s) {', '.join('$' + v for v in dict.fromkeys(auth_vars))}."
        line = f"- **{name}** at `{resolved_url}`.{auth_note}"
        if hint:
            line += f" {hint}"
        lines.append(line)

    if not lines:
        return "", env
    section = (
        "\n\n# Available data sources\n"
        "These HTTP data sources are reachable from your Bash environment via curl. "
        "Use them to gather evidence:\n" + "\n".join(lines)
    )
    return section, env


async def _run(
    user_prompt: str,
    model: str,
    cluster_name: Optional[str],
    test_folder: Optional[str],
    mocked_date: Optional[str] = None,
) -> SDKResult:
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
    max_turns = int(os.environ.get("HOLMES_SDK_MAX_TURNS", "120"))
    audit_path = os.environ.get("HOLMES_SDK_AUDIT_LOG")

    data_source_section, extra_env = discover_data_sources(test_folder)

    async def trace_bash(input_data: dict, tool_use_id: Optional[str], context: Any):
        # Optional debug trace of bash commands run (lead + workers).
        if audit_path and input_data.get("tool_name") == "Bash":
            try:
                cmd = (input_data.get("tool_input") or {}).get("command", "")
                with open(audit_path, "a") as fh:
                    fh.write(cmd.replace("\n", " ") + "\n")
            except Exception:
                pass
        return {}

    agents = {
        "worker": AgentDefinition(
            description="Focused investigation worker for one self-contained task. Use for per-target deep dives when fanning out over many targets.",
            prompt=WORKER_AGENT_PROMPT + data_source_section,
            tools=["Bash", "Read", "Grep", "Glob"],
            model=worker_model,
        )
    }

    system_prompt = SDK_SYSTEM_PROMPT
    if mocked_date:
        # The baseline harness patches the prompt builder to inject "now"; mirror
        # that so time-relative evals resolve against the same reference time.
        system_prompt += f"\n\nThe current date and time is {mocked_date}."
    if cluster_name:
        system_prompt += f"\n\nYour kubectl context is the cluster `{cluster_name}`."
    system_prompt += data_source_section

    sub_env = {
        "ANTHROPIC_BASE_URL": os.environ["ANTHROPIC_BASE_URL"],
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "dummy"),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    # Only forward KUBECONFIG if set & non-empty; otherwise let kubectl fall back
    # to ~/.kube/config (passing KUBECONFIG="" can break cluster access).
    if os.environ.get("KUBECONFIG"):
        sub_env["KUBECONFIG"] = os.environ["KUBECONFIG"]
    sub_env.update(extra_env)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        max_turns=max_turns,
        allowed_tools=["Bash", "Read", "Grep", "Glob", "TodoWrite", "Task"],
        hooks={"PreToolUse": [HookMatcher(hooks=[trace_bash])]} if audit_path else None,
        agents=agents,
        cli_path=_resolve_cli_path(),
        env=sub_env,
    )

    async def prompt_stream():
        yield {"type": "user", "message": {"role": "user", "content": user_prompt}}

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
            result.total_tokens = result.prompt_tokens + result.completion_tokens + cache_read

    result.result = final_text
    result.tool_calls = tool_calls
    result.num_llm_calls = num_turns
    return result


def _describe_tool_use(block: Any) -> Any:
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
    user_prompt: str,
    model: str,
    cluster_name: Optional[str] = None,
    test_folder: Optional[str] = None,
    mocked_date: Optional[str] = None,
) -> SDKResult:
    """Synchronous entry point used by the eval harness."""
    import anyio

    if "ANTHROPIC_BASE_URL" not in os.environ:
        raise RuntimeError(
            "claude-sdk engine requires ANTHROPIC_BASE_URL (point it at an "
            "Anthropic-compatible endpoint, e.g. a LiteLLM proxy)."
        )
    return anyio.run(
        _run, user_prompt, model, cluster_name, test_folder, mocked_date
    )

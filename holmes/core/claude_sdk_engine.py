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
import shutil
import tempfile
import time
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
    "grafana": "Grafana HTTP API under /api (datasources, dashboards). Auth with the API key as `Authorization: Bearer <key>`. To render a dashboard/panel as an image: `GET /render/d-solo/<uid>/<slug>?panelId=<n>&width=1000&height=500` (PNG; save to a file and Read it).",
    "rabbitmq": "RabbitMQ management HTTP API. Health/overview: `GET /api/overview` with basic auth (`curl -u $USER:$PASS`).",
    "datadog": "Datadog HTTP API (api.datadoghq.com). Auth with DD-API-KEY and DD-APPLICATION-KEY headers. Logs: `POST /api/v2/logs/events/search`.",
}


@dataclass
class SDKResult:
    """Subset of LLMResult that the eval harness consumes."""

    result: Optional[str] = None
    tool_calls: List[Any] = field(default_factory=list)
    num_llm_calls: Optional[int] = None
    # Investigation wall-clock, measured directly from session-ready (the CLI's
    # init handshake) to completion — CLI process spawn is not investigation
    # time (a real deployment holds a persistent client). None if the session
    # never became ready (caller should fall back to its own wall measurement).
    investigation_seconds: Optional[float] = None
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
* Batch independent read-only commands into ONE Bash invocation (chain with `;`) instead of one command per turn — every extra turn costs a full model round trip.
* For any HTTP data source listed under "Available data sources" below, query it with curl. Credentials, when needed, are in the named environment variables — reference them in the command (e.g. `curl -H "Authorization: ApiKey $ELASTICSEARCH_API_KEY" ...`); do not print their values.
* Read/Grep/Glob operate on the local filesystem if you save tool output to files.

Investigation discipline:
* Something "Running"/"Ready" is not necessarily healthy — check the application's actual runtime behavior in its logs, not just status.
* When analyzing behavior over time, read the FULL history (complete logs/series), not just the most recent lines — trends are invisible in a tail sample.
* Be calibrated: distinguish sustained trends and real failures from normal jitter or steady-but-high/low values. Do not report stable values or noise as problems.
* Treat error messages as exact diagnostic evidence: `authentication failed`/`password authentication failed` for user X means X EXISTS; `role/user does not exist` means it is absent. These are mutually exclusive — never hedge one as the other.
* Adjacent / similarly-named entities — be transparent AND useful. If the user's name AS WRITTEN matches no data exactly (names differing in case, separators, or punctuation are DIFFERENT names, e.g. `foowebjob` vs `Foo.WebJob`), but you find data for a similarly-named one: (1) explicitly state, using the user's verbatim name, that you found no data for that exact entity; (2) report what you DID find in the related entity; (3) clearly label those findings as coming from the different, related entity — phrases like "I found logs for X (a different service from <user's name>)". Never silently merge the user's name into the entity you actually found. This disclosure is required even when a close match exists, and must LEAD your final answer.
* For Kubernetes permission errors (`Error from server (Forbidden)`), say so explicitly and identify the missing resource/verb rather than treating it as "no problem found".
* Use hedging language (possible, likely, may) for root-cause claims you cannot directly confirm from tool output; present directly-observed errors as facts.
* Back every claim with concrete evidence: exact resource names, namespaces, error messages/codes, log lines, counts.

Delegation (Task tool): you have a `worker` sub-agent. When an investigation fans out over many independent targets that EACH need substantial evidence read (e.g. full log histories across many services), dispatch ONE self-contained `worker` Task per target IN PARALLEL (all in a single turn) and synthesize their reports. For anything you can investigate well yourself, do it directly — delegation has real token/latency cost.

You are part of the HolmesGPT product: when users ask how to connect/enable an integration or give you access to a new data source, point them to its setup docs at https://holmesgpt.dev/data-sources/builtin-toolsets/<integration-name>/.

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
) -> Tuple[str, Dict[str, str], Dict[str, dict]]:
    """Inspect the eval's declared toolsets and produce (prompt_section, env, mcp_servers).

    prompt_section: human-readable "Available data sources" block listing each
    enabled HTTP data source's URL, auth env var, and a query hint.
    env: extra environment variables (resolved credential values) to expose to
    the agent's bash so it can curl with them.
    mcp_servers: SDK-native MCP server definitions for toolsets of type `mcp`.
    """
    configs = _load_toolset_configs(test_folder)
    lines: List[str] = []
    env: Dict[str, str] = {}
    mcp_servers: Dict[str, dict] = {}

    for name, entry in sorted(configs.items()):
        # Presence in an eval's toolsets.yaml means enabled unless explicitly
        # disabled — same semantics as the baseline harness (cfg.get("enabled", True)).
        if not entry.get("enabled", True):
            continue
        cfg = entry.get("config") or {}

        if entry.get("type") == "mcp":
            # Wire MCP toolsets natively: the Claude Agent SDK speaks MCP itself.
            if cfg.get("mode", "stdio") == "stdio" and cfg.get("command"):
                # Absolutize relative arg paths now: the CLI runs in a scratch
                # workspace, not the repo, so cwd-relative script paths would break.
                args = [
                    str(Path(a).resolve()) if Path(a).exists() else a
                    for a in (cfg.get("args") or [])
                ]
                mcp_servers[name] = {
                    "type": "stdio",
                    "command": cfg["command"],
                    "args": args,
                    "env": cfg.get("env") or {},
                }
            continue

        for url, creds in _walk_endpoints(cfg):
            # Resolve {{ env.X }} refs; pass referenced vars through. Literal
            # credential values get exported under generated env var names so
            # the agent can use them without secrets appearing in the prompt.
            auth_vars: List[str] = []
            for key, val in creds.items():
                refs = _ENV_REF_RE.findall(val)
                if refs:
                    for var in refs:
                        if var in os.environ:
                            env[var] = os.environ[var]
                        auth_vars.append(var)
                else:
                    var = re.sub(r"[^A-Z0-9]+", "_", f"{name}_{key}".upper()).strip("_")
                    env[var] = val
                    auth_vars.append(var)
            resolved_url = _ENV_REF_RE.sub(
                lambda m: os.environ.get(m.group(1), m.group(0)), url
            )

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
        return "", env, mcp_servers
    section = (
        "\n\n# Available data sources\n"
        "These HTTP data sources are reachable from your Bash environment via curl. "
        "Use them to gather evidence:\n" + "\n".join(lines)
    )
    return section, env, mcp_servers


_CRED_KEYS = ("username", "password", "api_key", "token", "user", "headers")


def _walk_endpoints(cfg: Any, _depth: int = 0):
    """Recursively yield (url, {cred_key: raw_value}) for every dict in the
    toolset config that carries a URL-ish key (api_url, url, host, *_url) —
    handles both flat configs and nested shapes like rabbitmq's clusters list."""
    if _depth > 4:
        return
    if isinstance(cfg, list):
        for item in cfg:
            yield from _walk_endpoints(item, _depth + 1)
        return
    if not isinstance(cfg, dict):
        return
    url = None
    for key, val in cfg.items():
        if isinstance(val, str) and val and (
            key in ("api_url", "url", "host") or key.endswith("_url")
        ):
            url = val
            break
    if url:
        creds = {
            k: v for k, v in cfg.items()
            if isinstance(v, str) and v and k in _CRED_KEYS
        }
        yield url, creds
    else:
        for val in cfg.values():
            yield from _walk_endpoints(val, _depth + 1)


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
        SystemMessage,
        TextBlock,
        ToolUseBlock,
        query,
    )

    worker_model = os.environ.get("HOLMES_SUBAGENT_MODEL", model)
    max_turns = int(os.environ.get("HOLMES_SDK_MAX_TURNS", "120"))
    audit_path = os.environ.get("HOLMES_SDK_AUDIT_LOG")

    data_source_section, extra_env, mcp_servers = discover_data_sources(test_folder)

    # Capture the CLI's stderr so an opaque is_error result is debuggable.
    # --debug is verbose, so keep a generous tail in memory and mirror the full
    # stream to a file for post-mortem.
    stderr_lines: List[str] = []
    stderr_file = os.environ.get("HOLMES_SDK_CLI_STDERR_LOG", "/tmp/holmes_sdk_cli_stderr.log")
    try:
        open(stderr_file, "w").close()  # truncate per run
    except Exception:
        stderr_file = None

    def _capture_stderr(line: str) -> None:
        if line and line.strip():
            stderr_lines.append(line.rstrip())
            del stderr_lines[:-250]  # keep last 250 lines
            if stderr_file:
                try:
                    with open(stderr_file, "a") as fh:
                        fh.write(line.rstrip() + "\n")
                except Exception:
                    pass

    # Always-on, command-level audit trail of every tool invocation — lead agent
    # AND sub-agent workers (PreToolUse hooks fire for both, unlike the lead-only
    # ToolUseBlocks in the message stream). Attached to the result and logged to
    # Braintrust so every eval run is auditable at full-command level.
    tool_invocations: List[dict] = []

    async def trace_tools(input_data: dict, tool_use_id: Optional[str], context: Any):
        try:
            name = input_data.get("tool_name", "?")
            raw = input_data.get("tool_input") or {}
            if len(tool_invocations) < 400:
                tool_invocations.append({"tool": name, "input": str(raw)[:1500]})
            if audit_path:
                with open(audit_path, "a") as fh:
                    fh.write(f"{name}\t{str(raw)[:1500]}".replace("\n", " ") + "\n")
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
        system_prompt += (
            f"\n\nYour kubectl context is the cluster `{cluster_name}`."
            f" If the user asks about a DIFFERENT cluster/environment, state up front that this agent is connected to `{cluster_name}` and not the cluster they named, label any findings as coming from `{cluster_name}`, and suggest they may need the agent/data source for that other cluster."
        )
    system_prompt += data_source_section

    # Experimental (env-gated): parallel hypothesis racing for ambiguous root causes.
    if os.environ.get("HOLMES_HYPOTHESIS_RACING", "").lower() in ("1", "true"):
        system_prompt += (
            "\n\nHypothesis racing: if after initial triage the root cause is ambiguous "
            "between 2-3 plausible hypotheses, dispatch one `worker` Task per hypothesis "
            "IN PARALLEL (all in one turn), each tasked to confirm or refute ONE hypothesis "
            "with direct evidence, then adjudicate on their findings."
        )

    # Experimental (env-gated): persistent per-cluster memory across investigations.
    memory_file = os.environ.get("HOLMES_MEMORY_FILE")
    if memory_file:
        try:
            mem = Path(memory_file).read_text()[:8000]
        except Exception:
            mem = ""
        if mem.strip():
            system_prompt += (
                "\n\n# Cluster memory (facts learned in previous investigations — "
                "trust but verify anything load-bearing)\n" + mem
            )
        system_prompt += (
            f"\n\nAfter giving your final answer, append concise NEW durable facts you "
            f"learned about this cluster (topology, namespaces, services, known issues) "
            f"to {memory_file} via Bash — one bullet per fact, no duplicates of what is "
            f"already there; skip entirely if nothing new."
        )

    sub_env = {
        "ANTHROPIC_BASE_URL": os.environ["ANTHROPIC_BASE_URL"],
        # The CLI requires a NON-EMPTY api key or it reports "Not logged in" and
        # every turn errors. We always talk to the local proxy (which ignores the
        # key and uses the model_list creds), so a placeholder is correct; never
        # inherit an empty ANTHROPIC_API_KEY (CI sets it empty when using OpenRouter).
        "ANTHROPIC_API_KEY": (os.environ.get("ANTHROPIC_API_KEY") or "").strip() or "sk-ant-proxy-placeholder",
        # The CLI uses a separate small/"haiku" model for background essentials
        # (e.g. summarisation). Its default name (claude-3-5-haiku-*) is NOT in
        # our proxy model_list, so such a call 404s and can fail the whole run.
        # Pin every model alias the CLI might pick to the main model, which the
        # proxy always resolves. (Explicit ANTHROPIC_AUTH_TOKEN from ambient env
        # is cleared so it can't shadow our placeholder key on the proxy hop.)
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_AUTH_TOKEN": "",
        # Disable Claude Code's automatic API-side context management ("microcompact":
        # it sends a top-level `context_management` field once the context grows large
        # enough). LiteLLM 1.83.7's /v1/messages translation for non-Anthropic backends
        # (we route opus-4.6 through OpenRouter) rejects that field with HTTP 400
        # "context_management: Extra inputs are not permitted", which crashed every
        # multi-turn eval (large kubectl/log output trips the threshold). The CLI's own
        # local compaction still works, and opus-4.6 has a 1M context window.
        "DISABLE_MICROCOMPACT": "1",
        # The CLI caps MCP tool output at 25k tokens by default; eval MCP servers
        # intentionally return oversized payloads with trailing image attachments
        # (e.g. 236_image_spill_to_disk), and the cap silently drops the image.
        "MAX_MCP_OUTPUT_TOKENS": "100000",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    # Only forward KUBECONFIG if set & non-empty; otherwise let kubectl fall back
    # to ~/.kube/config (passing KUBECONFIG="" can break cluster access).
    if os.environ.get("KUBECONFIG"):
        sub_env["KUBECONFIG"] = os.environ["KUBECONFIG"]
    sub_env.update(extra_env)

    # MCP toolsets from the eval are wired natively (the SDK speaks MCP);
    # `mcp__<server>` in allowed_tools whitelists every tool the server exposes.
    allowed = ["Bash", "Read", "Grep", "Glob", "TodoWrite", "Task"]
    allowed += [f"mcp__{n}" for n in mcp_servers]

    # Run each investigation in a fresh scratch workspace. The CLI auto-loads
    # project context (CLAUDE.md, settings, directory listing) from its cwd —
    # inheriting the harness cwd injected ~34k tokens of repo content into
    # EVERY investigation (measured: 35,300 vs 1,309 first-call prompt tokens)
    # and exposed the repo (incl. eval fixtures) to the agent. The scratch dir
    # is also where the agent saves large tool output for Read/Grep analysis.
    workspace = tempfile.mkdtemp(prefix="holmes-sdk-ws-")

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        max_turns=max_turns,
        allowed_tools=allowed,
        mcp_servers=mcp_servers or {},
        hooks={"PreToolUse": [HookMatcher(hooks=[trace_tools])]},
        agents=agents,
        cwd=workspace,
        cli_path=_resolve_cli_path(),
        env=sub_env,
        stderr=_capture_stderr,
        extra_args={"debug": None},
    )

    async def prompt_stream():
        yield {"type": "user", "message": {"role": "user", "content": user_prompt}}

    final_text: Optional[str] = None
    tool_calls: List[Any] = []
    num_turns = 0
    result = SDKResult()
    err_detail: Optional[str] = None
    init_info: Optional[str] = None
    t_ready: Optional[float] = None

    try:
        async for msg in query(prompt=prompt_stream(), options=options):
            if isinstance(msg, SystemMessage):
                # The CLI's `init` system message reports the model/tools/session
                # it actually negotiated — invaluable when a run errors opaquely.
                if t_ready is None:
                    t_ready = time.monotonic()  # session ready: clock starts here
                data = getattr(msg, "data", {}) or {}
                if getattr(msg, "subtype", "") == "init" or "model" in data:
                    init_info = (
                        f"subtype={getattr(msg, 'subtype', '?')} "
                        f"model={data.get('model')} "
                        f"tools={data.get('tools')} "
                        f"mcp_servers={data.get('mcp_servers')} "
                        f"apiKeySource={data.get('apiKeySource')}"
                    )
            elif isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        final_text = block.text
                    elif isinstance(block, ToolUseBlock):
                        tool_calls.append(_describe_tool_use(block))
            elif isinstance(msg, ResultMessage):
                num_turns = msg.num_turns or 0
                if msg.result:
                    final_text = msg.result
                if getattr(msg, "is_error", False):
                    err_detail = (
                        "; ".join(getattr(msg, "errors", None) or [])
                        or f"is_error subtype={getattr(msg, 'subtype', '?')}"
                    )
                result.total_cost = float(msg.total_cost_usd or 0.0)
                usage = msg.usage or {}
                # Match litellm/baseline semantics so report columns compare
                # apples-to-apples: prompt_tokens INCLUDES cached and
                # cache-creation tokens (Anthropic reports them separately;
                # litellm folds them into prompt_tokens).
                in_tok = int(usage.get("input_tokens", 0) or 0)
                cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
                cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
                result.prompt_tokens = in_tok + cache_read + cache_creation
                result.completion_tokens = int(usage.get("output_tokens", 0) or 0)
                result.cached_tokens = cache_read or None
                result.total_tokens = result.prompt_tokens + result.completion_tokens
    except Exception as e:  # surface the real failure instead of blanking the row
        err_detail = f"{type(e).__name__}: {str(e)[:600]}"
        logger.error("SDK engine query failed", exc_info=True)

    if err_detail:
        # Put the real error where the eval report shows it (the "Actual" column),
        # so a transport/CLI failure is debuggable rather than a blank row.
        # ALWAYS append (even when a partial assistant turn produced text): an
        # is_error/exception means the run did not complete normally, and the
        # CLI --debug stderr is the only window into why.
        stderr_tail = "\n".join(stderr_lines[-120:]) or "(no stderr)"
        banner = (
            f"\n\n[claude-sdk engine error] {err_detail}\n"
            f"CLI init: {init_info or '(no init message received)'}\n"
            f"CLI stderr tail:\n{stderr_tail}\n"
            f"proxy log tail:\n{_proxy_log_tail()}"
        )
        final_text = (final_text + banner) if final_text else banner.lstrip()
        logger.error("claude-sdk CLI stderr tail:\n%s", stderr_tail)

    result.result = final_text
    result.tool_calls = tool_calls
    result.num_llm_calls = num_turns
    result.metadata = {"tool_invocations": tool_invocations}
    if t_ready is not None:
        result.investigation_seconds = time.monotonic() - t_ready
    if not os.environ.get("HOLMES_SDK_KEEP_WORKSPACE"):
        shutil.rmtree(workspace, ignore_errors=True)
    return result


def _proxy_log_tail(n: int = 25) -> str:
    for path in ("/tmp/holmes_sdk_litellm_proxy.log", "/tmp/litellm_proxy.log"):
        try:
            lines = Path(path).read_text().splitlines()
            if lines:
                return "\n".join(lines[-n:])
        except Exception:
            continue
    return "(no proxy log)"


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

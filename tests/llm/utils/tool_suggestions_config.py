"""Tool suggestions wiring for LLM eval runs.

Always injects the SUGGEST_RUNBOOKS frontend noop tool and its system-prompt
block so every eval has access to the "capture an env-specific tool-call
correction" skill. Memory emission is recorded on the run for the GitHub
report and (for evals that opt in via ``rerun_with_memory``) replayed
through the SkillsToolset.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from holmes.core.tools_utils.frontend_tools import build_frontend_noop_tool

SUGGEST_RUNBOOKS_TOOL_NAME = "suggest_runbooks"


# Description shown to the LLM as the tool's description.
#
# Purpose — narrow: capture ONLY tool-call access-pattern corrections.
#
# The LLM already knows generic debugging methodology, the standard kubectl
# verbs, "check pod status first", etc. Suggesting that back to itself is
# noise. The durable, model-doesn't-know-this thing is: in THIS environment,
# the LLM tried a tool call with parameters that were wrong (empty result,
# error, irrelevant data), and only succeeded after adjusting params in a
# way that an LLM with no exposure to this environment would not have
# guessed. That correction — the environment-specific call shape — is the
# only thing worth saving so the next investigation skips the failed
# attempt and goes straight to the working call.
#
# If the investigation succeeded on the first try, or if the correction was
# a generic mistake any LLM would self-correct (e.g. a typo, forgetting a
# flag documented in --help), there is nothing to save: emit ZERO suggestions.
SUGGEST_RUNBOOKS_TOOL_DESCRIPTION = (
    "Call this tool ONLY if, during this investigation, you called a tool "
    "with the wrong parameters (empty result, error, or irrelevant data), "
    "and then succeeded by calling the same tool (or a sibling tool) with "
    "different parameters that required environment-specific knowledge — "
    "knowledge a fresh LLM would NOT have guessed without trying the wrong "
    "way first.\n\n"
    "Each suggestion captures one such correction so the NEXT investigation "
    "skips the failed attempt and goes straight to the call shape that "
    "works in this environment.\n\n"
    "Do NOT call this tool — emit zero suggestions — when:\n"
    "  - All your tool calls succeeded on the first try (nothing to learn).\n"
    "  - The correction was a generic mistake any LLM would self-correct "
    "(typo, missing `-n <namespace>`, forgetting `--previous` for a crashed "
    "container — these are in the model's training data already).\n"
    "  - You want to record the ROOT CAUSE you found (that's transient — "
    "once fixed it's gone; we are not saving conclusions).\n"
    "  - You want to record generic methodology like \"first check pods, "
    "then logs\" — the model already knows this.\n"
    "  - You want to record an alert/symptom→cause mapping.\n\n"
    "DO call this tool when the correction was something like:\n"
    "  - The label/selector used to identify this team's apps is non-standard "
    "(e.g. `service.team/component=checkout` instead of `app=checkout`) — "
    "first PromQL/log query returned empty, second with the right label "
    "worked.\n"
    "  - The metric/log/trace this service emits is on a non-default path, "
    "port, index, or stream — first query hit the wrong location.\n"
    "  - A custom annotation, CRD field, or dashboard UID is the only way "
    "to find a piece of data in this environment.\n"
    "  - A specific tool needs a specific filter to return relevant data here "
    "(unfiltered call returned huge/irrelevant data; filtered call worked).\n\n"
    "Before suggesting, review the skills already fetched from the catalog "
    "this turn. Do NOT propose a skill that duplicates one already captured.\n\n"
    "CRITICAL: This tool is silent. The user sees suggestions as UI chips they "
    "can accept or ignore. Never say \"I'll remember\", \"noted\", \"saved\", "
    "or acknowledge this tool in any way — even if the user explicitly asks "
    "you to remember something. Just call the tool silently and continue your "
    "response as if it doesn't exist. Never refer to these as \"runbooks\" — "
    "they are \"skills\"."
)


SUGGEST_RUNBOOKS_NOOP_RESPONSE = (
    "Tool returned silently — no data, no acknowledgement to make. "
    "The investigation is not over yet: the user has NOT seen your "
    "answer. Your next message must contain your final answer text "
    "for the user. Do not say you saved, remembered, or will remember "
    "anything — write the answer as if this tool was never called."
)


SUGGEST_RUNBOOKS_TOOL_PARAMETERS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "description": (
                "One entry per tool-call correction discovered this turn. "
                "Empty array if no correction occurred."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": (
                            "Short name for the access-pattern correction. "
                            "Name it by the tool/data being queried and the "
                            "env-specific quirk, e.g. "
                            '"Querying checkout-service metrics — uses '
                            '\'service.team/component\' label, not \'app\'", '
                            '"Fetching logs for payments — index is '
                            '\'payments-prod-*\', not \'k8s-*\'". '
                            "NOT a root-cause title."
                        ),
                    },
                    "when_to_use": {
                        "type": "string",
                        "description": (
                            "Which tool / data source this correction applies "
                            "to, and the shape of the request that triggers "
                            'it (e.g. "Any PromQL query for the checkout '
                            'service in this cluster", "Loki queries for any '
                            'app in the payments namespace"). Should let a '
                            "future investigation recognize \"this skill is "
                            "relevant\" before issuing the first wrong call."
                        ),
                    },
                    "failed_call": {
                        "type": "string",
                        "description": (
                            "Concrete shape of the call you tried that did "
                            "NOT work, with the exact parameter that was "
                            'wrong (e.g. "PromQL: sum(rate(http_requests_'
                            'total{app=\\"checkout\\"}[5m])) — returned empty '
                            'because the label is service.team/component, '
                            'not app"). Omit incident-specific values; keep '
                            "the call-shape and the wrong parameter name."
                        ),
                    },
                    "working_call": {
                        "type": "string",
                        "description": (
                            "Concrete shape of the call that DID work, with "
                            "the env-specific parameter that made it work "
                            "(e.g. \"PromQL: sum(rate(http_requests_total"
                            "{service.team/component=\\\"checkout\\\"}[5m]))"
                            " — use service.team/component label\"). This "
                            "is the durable lesson: the call shape a future "
                            "investigation should reach for first."
                        ),
                    },
                    "why_env_specific": {
                        "type": "string",
                        "description": (
                            "One sentence on why a fresh LLM would NOT have "
                            "guessed the working call without trying the "
                            "wrong one first (e.g. \"This team overrides "
                            "the default app= label with their own taxonomy; "
                            "not documented anywhere a model would know\"). "
                            "If you can't articulate this, the correction is "
                            "probably generic — do NOT include this suggestion."
                        ),
                    },
                    "importance": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": (
                            "high = this exact wrong-call/right-call pair will "
                            "recur often in this environment and saves real "
                            "tokens/turns; medium = likely useful; low = nice "
                            "to have. Default to medium unless you're sure."
                        ),
                    },
                },
                "required": [
                    "title",
                    "when_to_use",
                    "failed_call",
                    "working_call",
                    "why_env_specific",
                    "importance",
                ],
            },
        }
    },
    "required": ["suggestions"],
}


# System prompt addition appended whenever the SUGGEST_RUNBOOKS tool is injected.
#
# This carries the GOAL and the concrete pattern examples; the tool's own
# description states the action. Together they push the model to fire
# specifically on env-specific tool-call corrections it just learned, and
# to skip generic methodology the model already knows from training.
SUGGEST_RUNBOOKS_SYSTEM_PROMPT = (
    f"GOAL of the {SUGGEST_RUNBOOKS_TOOL_NAME} tool — speed up FUTURE "
    f"investigations in THIS environment. Future-you (or another LLM) will "
    f"face the same kind of question; if during this investigation you "
    f"called a tool the way a fresh LLM would default to, got the wrong "
    f"answer or an empty result, and only succeeded after discovering an "
    f"env-specific call shape, capture that correction so the next run "
    f"skips the failed attempt.\n\n"
    f"CAPTURE the correction when you encounter patterns like:\n"
    f"- Non-standard label / selector schemes — e.g. apps identified by "
    f"`service.team/component=X` or a custom team prefix rather than the "
    f"conventional `app=X`. You tried `app=X`, got empty, listed labels, "
    f"found the right one.\n"
    f"- Non-standard metric names — the exporter's default name was "
    f"renamed via Prometheus `metric_relabel_configs`, or a team prefix "
    f"like `acmecorp_<metric>` replaces the upstream `kafka_<metric>`, "
    f"`mysql_<metric>`, etc. Your PromQL with the standard name returned "
    f"no series; `label_values` or `__name__=~...` revealed the real one.\n"
    f"- Non-standard log fields / log shape — the index/stream uses "
    f"`severity` not `level`, `msg` not `message`, or a custom JSON "
    f"schema. Your default filter returned zero; reading the mapping or "
    f"sampling a doc showed the right field.\n"
    f"- Non-standard data location / addressing — production data only "
    f"reachable via an alias, not the obvious index name; config in a "
    f"versioned ConfigMap (`cfg-X-v3`) not `X-config`; logs mounted at a "
    f"non-default path; secrets in a non-default namespace.\n"
    f"- Custom CRDs instead of standard resources — `kubectl get "
    f"deployment X` returns not-found because apps are deployed via "
    f"`apps.platform.io/App` and you only find them via `kubectl get apps`.\n"
    f"- Tool routing quirks specific to this stack — API base URL, "
    f"required filters/routing keys, version of the wire format, etc.\n\n"
    f"For each capture, the `failed_call` and `working_call` fields should "
    f"contain the CONCRETE call shape (tool name + the parameter that was "
    f"wrong vs the parameter that worked). Things you did NOT know before "
    f"this investigation began — write them down so you don't have to "
    f"rediscover them.\n\n"
    f"Do NOT call {SUGGEST_RUNBOOKS_TOOL_NAME} for:\n"
    f"- Generic METHODOLOGY a fresh LLM already knows "
    f"(\"check pod status first\", \"use --previous for crashed pods\", "
    f"\"filter by namespace\"). Note: the act of *inspecting the mapping* "
    f"or *listing labels* to recover from an empty query is generic; the "
    f"FACT you discover from it (e.g. \"this index uses `severity` not "
    f"`level`\", \"this team labels services with `acme_service` not "
    f"`service`\") is env-specific and IS worth capturing.\n"
    f"- Root-cause conclusions from THIS incident — they're transient; "
    f"once fixed they don't recur. We capture how to FIND things, not "
    f"what was wrong this time.\n"
    f"- Investigations that succeeded on the first try with default "
    f"parameters — there was no correction to teach.\n\n"
    f"WORKFLOW — when you have gathered enough information to answer "
    f"the user, follow this order:\n"
    f"  STEP 1. Scan your tool-call history this turn. Did any call "
    f"return empty / wrong / irrelevant data, followed by a successful "
    f"call with DIFFERENT parameters (different label name, different "
    f"field name, different metric prefix, different index/alias, "
    f"different path, etc.)? AND was the difference env-specific (a "
    f"custom convention, prefix, schema, or routing this team uses "
    f"that a fresh LLM would not have known)?\n"
    f"  STEP 2. If yes — invoke {SUGGEST_RUNBOOKS_TOOL_NAME} NOW, BEFORE "
    f"writing your final answer. The call must include the failed_call "
    f"shape, the working_call shape, and a one-line why_env_specific. "
    f"The tool returns silently — no data, no acknowledgement.\n"
    f"  STEP 3. THEN, in your next assistant message, write your final "
    f"answer text to the user. The tool call from STEP 2 is invisible "
    f"to the user; only your STEP 3 answer text is what they see.\n"
    f"Skipping STEP 2 when a real correction happened is a defect. "
    f"Mentioning the correction in your STEP 3 prose is NOT a "
    f"substitute for emitting the tool call — the prose is read by the "
    f"current user, the tool call surfaces a save-able skill chip for "
    f"future investigations.\n\n"
    f"CRITICAL — silent operation: NEVER acknowledge calling "
    f"{SUGGEST_RUNBOOKS_TOOL_NAME} in your response. Do not say "
    f"\"Got it\", \"I'll remember\", \"going forward\", \"from now on\", "
    f"\"noted\", or anything similar. The user sees suggestions as UI "
    f"chips and decides whether to save them; your response must read as "
    f"if no memory or learning took place. Even if the user says "
    f"\"remember this\" or \"save this\", do not confirm — just silently "
    f"call the tool and respond normally."
)


def inject_suggest_runbooks_tool(ai: Any) -> Any:
    """Return a clone of ``ai`` with the SUGGEST_RUNBOOKS frontend noop tool
    injected. Always injects — callers that don't want the tool (e.g. the
    closed-loop replay pass) should simply skip this call.
    """
    tool = build_frontend_noop_tool(
        name=SUGGEST_RUNBOOKS_TOOL_NAME,
        description=SUGGEST_RUNBOOKS_TOOL_DESCRIPTION,
        parameters=SUGGEST_RUNBOOKS_TOOL_PARAMETERS,
        canned_response=SUGGEST_RUNBOOKS_NOOP_RESPONSE,
    )
    cloned_executor = ai.tool_executor.clone_with_extra_tools([tool])
    return ai.with_executor(cloned_executor)


def append_suggest_runbooks_system_prompt(
    additional_system_prompt: Optional[str],
) -> str:
    """Append the SUGGEST_RUNBOOKS system prompt block to the caller's
    existing system prompt (or return it standalone if there isn't one).
    """
    if additional_system_prompt:
        return f"{additional_system_prompt}\n\n{SUGGEST_RUNBOOKS_SYSTEM_PROMPT}"
    return SUGGEST_RUNBOOKS_SYSTEM_PROMPT


def _slugify(text: str) -> str:
    """Normalize a free-form title to a filesystem-safe slug."""
    import re

    text = (text or "skill").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text[:60] or "skill"


def write_memories_as_skill_files(
    memories: List[Dict[str, Any]], target_dir: str
) -> List[str]:
    """Render each captured memory as a SKILL.md file under ``target_dir``,
    one per memory, so the SkillsToolset can pick them up via its standard
    file-scanning path. Returns the list of skill directories written.

    Used by the rerun_with_memory replay flow: after the first eval pass
    captures a memory, we write the memory to disk, point the
    SkillsToolset at the tempdir, and run the same prompt a second time.
    The agent — having read the skill description in the system prompt —
    is expected to call fetch_skill to load the body, see the
    failed_call/working_call pair, and go straight to the working call
    shape, skipping the wrong call that the first pass needed to recover
    from.
    """
    import os

    written: List[str] = []
    for idx, mem in enumerate(memories):
        title = str(mem.get("title") or f"runbook-{idx + 1}")
        when_to_use = str(mem.get("when_to_use") or "").strip()
        slug = _slugify(title)
        skill_dir = os.path.join(target_dir, f"{idx + 1:02d}-{slug}")
        os.makedirs(skill_dir, exist_ok=True)

        # The agent on replay only sees `name | description` in the system
        # prompt when deciding whether to load a skill. The captured
        # `title` describes the env-specific quirk ("uses 'severity', not
        # 'level'") — true but it doesn't match the user's symptom-shape
        # phrasing. The captured `when_to_use` describes the query shape
        # the skill applies to ("Any Loki query targeting the checkout
        # service in app-263") — exactly what the user will ask about.
        # Lead the description with when_to_use so the agent can recognize
        # relevance from symptoms, then append the title as the durable
        # lesson. Fall back to title alone if no when_to_use.
        if when_to_use:
            # Cap each part so the combined description stays short enough
            # for the system-prompt listing to remain scannable.
            when_short = when_to_use.split(".")[0][:120].rstrip()
            title_short = title[:120]
            description = f"{when_short} — {title_short}"
        else:
            description = title

        body_parts: List[str] = ["", "## When to use", ""]
        body_parts.append(when_to_use)
        body_parts += ["", "## Failed call shape (avoid)", ""]
        body_parts.append(str(mem.get("failed_call") or ""))
        body_parts += ["", "## Working call shape", ""]
        body_parts.append(str(mem.get("working_call") or ""))
        body_parts += ["", "## Why this is env-specific", ""]
        body_parts.append(str(mem.get("why_env_specific") or ""))

        # YAML frontmatter must escape embedded single quotes — the agent
        # may emit quotes inside its title or when_to_use. Pre-escape any
        # CR/LF too in case the model produced multi-line strings; YAML
        # block scalars handle that but a plain single-quoted scalar
        # cannot.
        safe_description = description.replace("'", "''").replace("\n", " ")
        frontmatter = (
            "---\n"
            f"name: {slug}\n"
            f"description: '{safe_description}'\n"
            "---\n"
        )

        skill_md = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md, "w", encoding="utf-8") as f:
            f.write(frontmatter)
            f.write("\n".join(body_parts).strip() + "\n")
        written.append(skill_dir)

    return written


def extract_suggested_memories(tool_calls: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """Pull the parsed ``suggestions`` arrays out of any SUGGEST_RUNBOOKS calls
    found in the LLM tool-call history. Each dict is one suggestion; multiple
    calls are flattened in the order they occurred.
    """
    if not tool_calls:
        return []

    memories: List[Dict[str, Any]] = []
    for tc in tool_calls:
        if getattr(tc, "tool_name", None) != SUGGEST_RUNBOOKS_TOOL_NAME:
            continue
        params = _extract_tool_call_params(tc)
        if not params:
            continue
        suggestions = params.get("suggestions") or []
        if not isinstance(suggestions, list):
            continue
        for suggestion in suggestions:
            if isinstance(suggestion, dict):
                memories.append(suggestion)

    return memories


def _extract_tool_call_params(tool_call: Any) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of the tool-call arguments dict.

    The runtime stores arguments on ``tool_call.result.params`` (set by the
    ``FrontendNoopTool._invoke``). When a different code path is exercised
    we fall back to ``tool_call.params`` and to the raw JSON description.
    """
    result = getattr(tool_call, "result", None)
    params = getattr(result, "params", None) if result is not None else None
    if isinstance(params, dict):
        return params

    fallback = getattr(tool_call, "params", None)
    if isinstance(fallback, dict):
        return fallback

    description = getattr(tool_call, "description", "") or ""
    if "{" in description and "}" in description:
        try:
            payload = description[description.index("{") : description.rindex("}") + 1]
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, json.JSONDecodeError):
            logging.debug(
                "Could not parse SUGGEST_RUNBOOKS arguments from tool call description"
            )

    return None

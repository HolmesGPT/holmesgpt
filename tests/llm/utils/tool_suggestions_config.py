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
# Purpose: capture env-specific knowledge that lets a FUTURE investigation
# in this environment skip tool calls. Two kinds qualify:
#
# - "correction": the LLM tried a tool call with parameters a fresh LLM
#   would default to, got the wrong answer (empty/error/irrelevant), and
#   succeeded only after adjusting parameters in an env-specific way.
#   Saving the wrong→right pair lets the next run skip the failed attempt.
#
# - "discovery": the LLM had to spend exploratory calls (inspect a mapping,
#   list labels/metrics/indices, sample documents) to learn STABLE facts
#   about this environment before it could issue the real query — even
#   though nothing failed. Saving the facts + resulting call shape lets the
#   next run skip the exploration entirely. This is the "Holmes re-learns
#   the schema from scratch in every chat" cost the mechanism exists to
#   eliminate.
#
# What is NOT worth saving: generic methodology the model already knows,
# generic mistakes any LLM self-corrects, transient incident facts, and
# anything derivable from the user's question or training data.
SUGGEST_RUNBOOKS_TOOL_DESCRIPTION = (
    "Call this tool to save environment-specific knowledge you gained "
    "during this investigation that would let a FUTURE investigation in "
    "this environment skip tool calls. Each suggestion is one entry; "
    "declare its `kind`:\n\n"
    "kind=\"correction\" — you called a tool with parameters a fresh LLM "
    "would default to, got an empty result / error / irrelevant data, and "
    "succeeded only after changing a parameter in a way that required "
    "environment-specific knowledge (custom label scheme, renamed metric, "
    "non-standard field name, alias-only index, custom CRD...). Capture "
    "the failed call shape AND the working call shape.\n\n"
    "kind=\"discovery\" — nothing failed, but you had to spend exploratory "
    "tool calls (inspecting a schema/mapping, listing labels, metrics, "
    "indices or streams, sampling documents) to learn STABLE facts about "
    "this environment before you could issue the real query. Capture the "
    "facts and the direct call shape they enable, so the next "
    "investigation queries immediately instead of re-exploring. Set "
    "failed_call to the empty string \"\" for this kind.\n\n"
    "Do NOT call this tool — emit zero suggestions — for:\n"
    "  - Generic mistakes any LLM would self-correct (typo, missing "
    "`-n <namespace>`, forgetting `--previous` — already in training "
    "data).\n"
    "  - Facts derivable from the user's question, from tool descriptions, "
    "or from general training data.\n"
    "  - Transient values: current counts, statuses, the ROOT CAUSE of "
    "this incident (once fixed it's gone — we save how to FIND things, "
    "not what was wrong today).\n"
    "  - Routine Kubernetes resource enumeration (listing/describing "
    "pods, namespaces, configmaps, PVCs...). Resource state is transient "
    "inventory, not schema — do NOT save it as a discovery. Only save a "
    "Kubernetes lesson when it's a stable CONVENTION (a custom label "
    "scheme, CRD, or annotation that is the only way to find something "
    "here).\n"
    "  - Generic methodology (\"check pods first\", \"inspect the mapping "
    "when a query returns empty\") — the act of exploring is generic; "
    "only the FACT discovered is env-specific and worth saving.\n"
    "  - Alert/symptom→cause mappings.\n"
    "  - Knowledge that duplicates a skill you already fetched this turn.\n\n"
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
                    "kind": {
                        "type": "string",
                        "enum": ["correction", "discovery"],
                        "description": (
                            "correction = a wrong→right tool-call pair: the "
                            "default call failed/returned nothing and an "
                            "env-specific parameter change made it work. "
                            "discovery = stable environment facts (schema, "
                            "field names, label sets, index layout) learned "
                            "through exploratory calls that succeeded — "
                            "saving them lets the next investigation skip "
                            "the exploration and query directly."
                        ),
                    },
                    "skill_domain": {
                        "type": "string",
                        "description": (
                            "The data source / tool family this correction "
                            "belongs to. Use a stable, coarse identifier "
                            "like `elasticsearch`, `loki`, `prometheus`, "
                            "`kubernetes`, `grafana`, `datadog`, "
                            "`coralogix`, `confluence`, `newrelic`, `aws`, "
                            "`gcp`, `azure`. All quirks for the same data "
                            "source MUST share the same `skill_domain` "
                            "string — the system will merge them into a "
                            "single \"Known quirks for querying <domain>\" "
                            "skill rather than create one skill per quirk. "
                            "Prefer adding to an existing domain over "
                            "inventing a new one."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "Short one-line name for THIS specific quirk "
                            "within the domain skill (e.g. \"app-261-logs-* "
                            "uses `severity` instead of `level`\", "
                            "\"Kafka metrics renamed to `acme_kafka_*` "
                            "prefix\"). The full domain skill will list "
                            "many such quirks; this is the heading of one. "
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
                            "For kind=correction: concrete shape of the call "
                            "you tried that did NOT work, with the exact "
                            "parameter that was wrong (e.g. \"PromQL: "
                            'sum(rate(http_requests_total{app=\\"checkout\\"}'
                            "[5m])) — returned empty because the label is "
                            'service.team/component, not app"). Omit '
                            "incident-specific values; keep the call-shape "
                            "and the wrong parameter name. "
                            "For kind=discovery: pass the empty string \"\"."
                        ),
                    },
                    "working_call": {
                        "type": "string",
                        "description": (
                            "For kind=correction: concrete shape of the call "
                            "that DID work, with the env-specific parameter "
                            "that made it work (e.g. \"PromQL: sum(rate("
                            "http_requests_total{service.team/component="
                            "\\\"checkout\\\"}[5m])) — use service.team/"
                            "component label\"). "
                            "For kind=discovery: the discovered environment "
                            "facts PLUS the direct call shape they enable "
                            "(e.g. \"Index app-x-logs-* fields: lvl(keyword: "
                            "ERROR/WARN/INFO), txt(text), app(keyword), "
                            "ts(date). Query levels with term lvl=<LEVEL>; "
                            "no mapping inspection needed\"). This is the "
                            "durable lesson: what a future investigation "
                            "should reach for first."
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
                    "kind",
                    "skill_domain",
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
    f"face the same kind of question against the same data sources. Two "
    f"kinds of knowledge gained this turn are worth saving:\n"
    f"1. kind=correction — you called a tool the way a fresh LLM would "
    f"default to, got the wrong answer or an empty result, and only "
    f"succeeded after discovering an env-specific call shape. Capture the "
    f"wrong→right pair so the next run skips the failed attempt.\n"
    f"2. kind=discovery — nothing failed, but you spent exploratory calls "
    f"(inspecting schemas/mappings, listing labels, metrics, indices or "
    f"streams, sampling documents) to learn STABLE environment facts "
    f"before you could issue the real query. Capture the facts and the "
    f"direct call shape they enable so the next run queries immediately "
    f"instead of re-exploring. Ask yourself at the end of any "
    f"investigation that touched a data source: \"which of my tool calls "
    f"would I repeat verbatim next time just to re-learn what I now "
    f"know?\" — those calls' findings are the discovery to save.\n\n"
    f"CAPTURE a correction when you encounter patterns like:\n"
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
    f"CAPTURE a discovery when you encounter patterns like:\n"
    f"- You inspected an index/table schema or mapping to learn its field "
    f"names and types before querying — save the schema summary and the "
    f"resulting query shape (kind=discovery, failed_call=\"\").\n"
    f"- You listed label names/values, metric names, index names, or log "
    f"streams to find out what exists here before the real query — save "
    f"the inventory facts you actually used.\n"
    f"- You sampled a document/series/row to learn the data shape (value "
    f"conventions, units, enum values) before filtering on it.\n"
    f"Do NOT capture the exploration METHOD (everyone knows to inspect a "
    f"mapping) — capture the env-specific FACTS it returned, stated so a "
    f"future run can query directly without repeating the exploration. "
    f"And do NOT capture transient inventory as discovery: listing pods, "
    f"namespaces, configmaps or other Kubernetes resources is routine "
    f"state inspection, not schema — those facts change daily and saving "
    f"them would mislead future runs. The line: schemas, field names, "
    f"label/metric naming conventions, and addressing rules are stable "
    f"and capturable; resource state and counts are not.\n\n"
    f"For each capture, the `failed_call` and `working_call` fields should "
    f"contain the CONCRETE call shape (tool name + the parameter that was "
    f"wrong vs the parameter that worked; for discoveries, working_call = "
    f"the facts + direct call shape, failed_call = \"\"). Things you did "
    f"NOT know before this investigation began — write them down so you "
    f"don't have to rediscover them.\n\n"
    f"CONSOLIDATION — quirks group by data source, not one skill per "
    f"quirk. Every suggestion you emit must include a `skill_domain` "
    f"field naming the underlying tool family (e.g. `elasticsearch`, "
    f"`loki`, `prometheus`, `kubernetes`, `grafana`, `datadog`, "
    f"`coralogix`, `confluence`, `newrelic`, `aws`). The harness merges "
    f"all quirks sharing a `skill_domain` into ONE \"Known quirks for "
    f"querying <domain>\" skill — so a future investigation that uses "
    f"that data source fetches one skill listing every quirk this team's "
    f"environment has, instead of having to find and load N separate "
    f"single-quirk skills. If a single investigation discovers three ES "
    f"quirks across three indices, emit three suggestions all tagged "
    f"`skill_domain: \"elasticsearch\"` — do NOT invent three different "
    f"domain names. Pick the COARSEST, MOST STABLE name for the data "
    f"source. Cross-domain corrections (e.g. \"join Kubernetes pod name "
    f"with Loki log stream\") are rare; only invent a new domain if the "
    f"correction genuinely doesn't fit any existing one.\n\n"
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
    f"- Investigations that answered directly with default parameters and "
    f"no exploration — nothing was learned about this environment.\n\n"
    f"WORKFLOW — when you have gathered enough information to answer "
    f"the user, follow this order:\n"
    f"  STEP 1. Scan your tool-call history this turn for BOTH kinds:\n"
    f"  (a) corrections — did any call return empty / wrong / irrelevant "
    f"data, followed by a successful call with DIFFERENT parameters "
    f"(different label name, field name, metric prefix, index/alias, "
    f"path, etc.), where the difference was env-specific (a custom "
    f"convention this team uses that a fresh LLM would not have known)?\n"
    f"  (b) discoveries — did you spend exploratory calls (mapping/schema "
    f"inspection, label/metric/index listing, document sampling) to learn "
    f"stable env facts a future run would otherwise have to re-learn "
    f"before querying?\n"
    f"  STEP 2. If either — invoke {SUGGEST_RUNBOOKS_TOOL_NAME} NOW, "
    f"BEFORE writing your final answer, with one suggestion per lesson "
    f"and the right `kind` on each. The tool returns silently — no data, "
    f"no acknowledgement.\n"
    f"  STEP 3. THEN, in your next assistant message, write your final "
    f"answer text to the user. The tool call from STEP 2 is invisible "
    f"to the user; only your STEP 3 answer text is what they see.\n"
    f"Skipping STEP 2 when a real correction or discovery happened is a "
    f"defect. Mentioning the lesson in your STEP 3 prose is NOT a "
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


def _normalize_skill_domain(raw: Optional[str]) -> str:
    """Coerce a skill_domain string into a stable, lowercase, hyphenated
    identifier. Strips surrounding whitespace, lowercases, replaces non-
    alphanumerics with hyphens. Returns ``"general"`` for empty input so
    older emissions without a domain still produce a single fallback skill
    rather than crashing.
    """
    if not raw:
        return "general"
    text = str(raw).strip().lower()
    import re

    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text or "general"


def _parse_quirks_from_skill_md(skill_md_path: str) -> List[Dict[str, Any]]:
    """Parse a previously-written domain SKILL.md back into a list of quirk
    dicts. Lets the harness MERGE a new emission into a pre-existing
    domain skill rather than overwriting it — the update-existing path
    that lets a customer's domain skill grow across investigations.

    The parser is intentionally lenient: missing fields produce empty
    strings, the file may be a legacy one-quirk skill (no numbered
    headings) — best-effort recovery so older saved skills can still be
    extended.
    """
    import re

    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError):
        return []

    # Strip YAML frontmatter if present.
    body = re.sub(r"^---\n.*?\n---\n", "", content, count=1, flags=re.DOTALL)

    # Split into entries by `## N. Title` headings. Skill files we write
    # number their entries; if the file is hand-written or legacy, fall
    # back to splitting on `## ` at the start of a line.
    entry_pattern = re.compile(r"^## \d+\.\s+(.+?)$", re.MULTILINE)
    matches = list(entry_pattern.finditer(body))
    if not matches:
        # Fall back: any `## ` heading except the top-level title
        entry_pattern = re.compile(
            r"^## (?!Known quirks)(.+?)$", re.MULTILINE
        )
        matches = list(entry_pattern.finditer(body))

    quirks: List[Dict[str, Any]] = []
    for idx, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        section = body[start:end]

        def _extract(label: str) -> str:
            # Pulls the text between `**<label>:**` and the next `**...**:`
            # bold-label or end of section. Tolerates inline-value style
            # ("**When to use:** text") and block-value style.
            pat = re.compile(
                rf"\*\*{re.escape(label)}:?\*\*\s*(.*?)(?=\n\*\*[^*]+:?\*\*|\Z)",
                re.DOTALL,
            )
            mm = pat.search(section)
            if not mm:
                return ""
            return mm.group(1).strip().lstrip("-").strip()

        failed_call = _extract("Failed call shape (avoid)")
        # working_call lives under different labels per kind.
        working_call = _extract("Working call shape") or _extract(
            "Environment facts and call shape"
        )
        importance = _extract("Importance").lower() or "medium"
        if importance not in ("low", "medium", "high"):
            importance = "medium"

        quirks.append(
            {
                # Kind round-trips structurally: correction entries have a
                # failed-call section, discovery entries don't.
                "kind": "correction" if failed_call else "discovery",
                "title": title,
                "when_to_use": _extract("When to use"),
                "failed_call": failed_call,
                "working_call": working_call,
                "why_env_specific": _extract("Why this is env-specific"),
                "importance": importance,
            }
        )
    return quirks


def _existing_domain_skill_path(
    target_dir: str, domain: str
) -> Optional[str]:
    """Locate an existing ``quirks-for-querying-<domain>/SKILL.md`` under
    ``target_dir`` if one was written by a prior investigation. Returns
    the absolute path or None.

    The match is on the suffix ``quirks-for-querying-<domain>`` so the
    numeric prefix (``01-``, ``02-`` …) the writer adds for ordering
    doesn't break lookup.
    """
    import os

    suffix = f"quirks-for-querying-{domain}"
    if not os.path.isdir(target_dir):
        return None
    for entry in os.listdir(target_dir):
        full = os.path.join(target_dir, entry)
        if not os.path.isdir(full):
            continue
        if entry == suffix or entry.endswith(f"-{suffix}"):
            skill_md = os.path.join(full, "SKILL.md")
            if os.path.isfile(skill_md):
                return skill_md
    return None


def write_memories_as_skill_files(
    memories: List[Dict[str, Any]], target_dir: str
) -> List[str]:
    """Render captured memories into ONE consolidated SKILL.md per
    ``skill_domain`` under ``target_dir``. If a domain skill already
    exists in ``target_dir`` (from a prior investigation), its existing
    quirks are READ, merged with the new emissions (deduplicating by
    title), and the file is rewritten — supporting the
    update-existing-skill flow where a single domain skill grows entry
    by entry across many investigations.

    Returns the list of skill directories written.
    """
    import os
    from collections import OrderedDict

    # Group new emissions by normalized domain, preserving emission order.
    new_by_domain: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for mem in memories:
        domain = _normalize_skill_domain(mem.get("skill_domain"))
        new_by_domain.setdefault(domain, []).append(mem)

    written: List[str] = []
    for idx, (domain, new_quirks) in enumerate(new_by_domain.items(), start=1):
        skill_name = f"quirks-for-querying-{domain}"

        # If this domain already has a pre-existing skill file in
        # target_dir, parse and prepend its quirks so the merged file
        # preserves prior knowledge. The new emissions follow (dedupe by
        # title).
        existing_path = _existing_domain_skill_path(target_dir, domain)
        existing_quirks: List[Dict[str, Any]] = []
        skill_dir: str
        if existing_path:
            existing_quirks = _parse_quirks_from_skill_md(existing_path)
            skill_dir = os.path.dirname(existing_path)
        else:
            skill_dir = os.path.join(target_dir, f"{idx:02d}-{skill_name}")
            os.makedirs(skill_dir, exist_ok=True)

        # Merge: existing first, then new, deduplicating on title.
        seen_titles: set = {q["title"].strip().lower() for q in existing_quirks if q.get("title")}
        merged_quirks: List[Dict[str, Any]] = list(existing_quirks)
        for q in new_quirks:
            t = str(q.get("title") or "").strip().lower()
            if t and t in seen_titles:
                continue
            merged_quirks.append(q)
            if t:
                seen_titles.add(t)

        # Rule-based description: aggressively imperative + broad trigger
        # set + explicit pushback on the speculative-fetch gate the base
        # prompt enforces ("only fetch skills that clearly match"). The
        # agent on replay won't know it's about to make a wrong call, so
        # framing this as a pre-flight optimization doesn't work — frame
        # it instead as "any touch of {domain} is a clear match".
        description = (
            f"MANDATORY pre-read for ANY investigation that will touch "
            f"{domain}. Fetch this skill BEFORE your first {domain} tool "
            f"call — applies to every search, count, filter, aggregation, "
            f"schema/mapping inspection, label/series listing, log query, "
            f"metric query, or trace query against {domain} in this "
            f"environment. This team's {domain} uses non-default field "
            f"names, indices, labels, or routing conventions; entries below "
            f"list each quirk so your first call lands right instead of "
            f"empty. Any user question that will touch {domain} is a clear "
            f"match for this skill — fetch it, do not skip."
        )

        body_parts: List[str] = [
            "",
            f"# Known quirks for querying {domain}",
            "",
            (
                "This skill collects the env-specific lessons this team's "
                f"investigations have learned about {domain}. Correction "
                "entries list a wrong call shape (the default a fresh "
                "agent would try) and the working shape; discovery "
                "entries list stable environment facts (schemas, field "
                "names, label sets) and the direct call shape they "
                "enable."
            ),
            "",
            (
                "DIRECTIVE — these entries are VERIFIED FACTS about this "
                "environment, captured from real tool results in earlier "
                "investigations. They are not examples. Treat them as "
                "current: issue your query directly using them, and do "
                "NOT re-run schema/mapping inspection, label listing, or "
                "sampling to confirm what an entry already tells you. "
                "Re-verify only if a query built on an entry returns an "
                "error or an implausibly empty result."
            ),
            "",
        ]

        for entry_idx, mem in enumerate(merged_quirks, start=1):
            # Titles become `## N. <title>` headings; collapse any
            # newlines the LLM emitted so the heading (and the parse
            # round-trip that splits on headings) stays intact.
            title = " ".join(
                str(mem.get("title") or f"quirk-{entry_idx}").split()
            )
            when_to_use = str(mem.get("when_to_use") or "").strip()
            failed_call = str(mem.get("failed_call") or "").strip()
            importance = str(mem.get("importance") or "medium").strip()
            body_parts += [
                "---",
                "",
                f"## {entry_idx}. {title}",
                "",
                f"**When to use:** {when_to_use}" if when_to_use else "",
                "",
            ]
            # Correction entries carry the wrong→right pair; discovery
            # entries have no failed call — just the facts + call shape.
            if failed_call:
                body_parts += [
                    "**Failed call shape (avoid):**",
                    "",
                    failed_call,
                    "",
                    "**Working call shape:**",
                    "",
                    str(mem.get("working_call") or "").strip(),
                    "",
                ]
            else:
                body_parts += [
                    "**Environment facts and call shape:**",
                    "",
                    str(mem.get("working_call") or "").strip(),
                    "",
                ]
            body_parts += [
                "**Why this is env-specific:**",
                "",
                str(mem.get("why_env_specific") or "").strip(),
                "",
                f"**Importance:** {importance}",
                "",
            ]

        # YAML frontmatter must escape embedded single quotes and newlines.
        safe_description = description.replace("'", "''").replace("\n", " ")
        frontmatter = (
            "---\n"
            f"name: {skill_name}\n"
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

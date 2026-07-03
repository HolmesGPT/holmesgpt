"""Helpers for the SuggestSkills closed-loop evals.

The SuggestSkills frontend tool (defined in
tests/llm/fixtures/shared/skill_suggestion_tool.yaml, mirroring the Robusta
UI) emits skill suggestions with the shape
``{title, symptoms, instructions, alerts, importance}``. This module
extracts those suggestions from a run's tool calls and renders them as
SKILL.md files so a replay pass can load them through the SkillsToolset —
closing the loop: a skill captured in one investigation must actually pay
off in the next.

Ported from the claude/consolidated-skills-per-domain branch and adapted to
the SuggestSkills suggestion schema (freeform instructions, no
skill_domain/consolidation — one SKILL.md per suggestion).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

SUGGEST_SKILLS_TOOL_NAME = "SuggestSkills"
FETCH_SKILL_TOOL_NAME = "fetch_skill"


def _slugify(text: str) -> str:
    """Normalize a free-form title to a filesystem-safe slug."""
    text = (text or "skill").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text[:60] or "skill"


def extract_suggested_skills(tool_calls: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """Pull the parsed ``suggestions`` arrays out of any SuggestSkills calls
    found in the LLM tool-call history. Each dict is one suggestion; multiple
    calls are flattened in the order they occurred.
    """
    if not tool_calls:
        return []

    suggestions: List[Dict[str, Any]] = []
    for tc in tool_calls:
        if getattr(tc, "tool_name", None) != SUGGEST_SKILLS_TOOL_NAME:
            continue
        params = _extract_tool_call_params(tc)
        if not params:
            continue
        raw = params.get("suggestions") or []
        if not isinstance(raw, list):
            continue
        for suggestion in raw:
            if isinstance(suggestion, dict):
                suggestions.append(suggestion)

    return suggestions


def _extract_tool_call_params(tool_call: Any) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of the tool-call arguments dict.

    The runtime stores arguments on ``tool_call.result.params`` (set by
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
                "Could not parse SuggestSkills arguments from tool call description"
            )

    return None


def count_fetch_skill_calls(tool_calls: Optional[List[Any]]) -> int:
    """Number of fetch_skill calls in a run's tool-call history."""
    return sum(
        1
        for tc in (tool_calls or [])
        if getattr(tc, "tool_name", "") == FETCH_SKILL_TOOL_NAME
    )


def write_suggestions_as_skill_files(
    suggestions: List[Dict[str, Any]], target_dir: str
) -> List[str]:
    """Render captured SuggestSkills suggestions into SKILL.md files under
    ``target_dir``, one skill directory per suggestion. The suggestion's
    ``symptoms`` field becomes the skill description (what the agent sees in
    the catalog listing when deciding whether to fetch) and ``instructions``
    becomes the body.

    Returns the list of skill directories written.
    """
    written: List[str] = []
    for idx, suggestion in enumerate(suggestions, start=1):
        title = " ".join(str(suggestion.get("title") or f"skill-{idx}").split())
        symptoms = str(suggestion.get("symptoms") or "").strip()
        instructions = str(suggestion.get("instructions") or "").strip()
        importance = str(suggestion.get("importance") or "medium").strip()
        alerts = suggestion.get("alerts") or []
        updates_skill = str(suggestion.get("updates_skill") or "").strip()

        slug = _slugify(title)
        skill_dir = os.path.join(target_dir, f"{idx:02d}-{slug}")
        os.makedirs(skill_dir, exist_ok=True)

        # YAML frontmatter must escape embedded single quotes and newlines;
        # the name is quoted so numeric/boolean-looking slugs stay strings.
        safe_description = symptoms.replace("'", "''").replace("\n", " ")
        frontmatter = (
            "---\n"
            f"name: '{slug}'\n"
            f"description: '{safe_description}'\n"
            "---\n"
        )

        body_parts: List[str] = [
            "",
            f"# {title}",
            "",
        ]
        if symptoms:
            body_parts += [f"**When to use:** {symptoms}", ""]
        if instructions:
            body_parts += [instructions, ""]
        if alerts:
            body_parts += [f"**Applies to alerts:** {', '.join(alerts)}", ""]
        if updates_skill:
            # Provenance marker: this suggestion corrects an existing skill
            # (the production UI offers it as an update via updateRunbook).
            # In the replay the corrected SKILL.md is simply loaded alongside
            # whatever else is on the search path; the marker tells the
            # agent which earlier skill it supersedes.
            body_parts += [f"**Supersedes skill:** {updates_skill}", ""]
        body_parts += [f"**Importance:** {importance}", ""]

        skill_md = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md, "w", encoding="utf-8") as f:
            f.write(frontmatter)
            f.write("\n".join(body_parts).strip() + "\n")
        written.append(skill_dir)

    return written

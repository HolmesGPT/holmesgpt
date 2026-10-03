# Design: Runbook to Skills Migration

**Status:** Proposed
**Date:** 2026-03-07

## Goal

Replace HolmesGPT's custom runbook catalog system (`catalog.json` + `.md` files + `custom_runbook_catalogs` config) with the [Agent Skills](https://agentskills.io) open standard (`SKILL.md` files with YAML frontmatter). Remote/Supabase runbooks remain unchanged.

**Why:** Users are already authoring Claude Code skills and want to use them with Holmes without rewriting them as runbooks. The two systems are architecturally identical (catalog of descriptions -> on-demand content loading), so the migration is a format unification, not a paradigm shift.

## Current Architecture

```
catalog.json             .md files (content)          Supabase (remote)
  description + link  ->  fetched on demand        ->  fetched by UUID
       |                       |                           |
       v                       v                           v
   RunbookCatalog       get_runbook_by_path()      dal.get_runbook_content()
       |                       |                           |
       +----------+------------+---------------------------+
                  |
                  v
        Prompt: descriptions injected into user prompt
        Tool:   fetch_runbook loads content on-demand
        Gate:   _runbook_in_use unlocks ALL restricted tools
```

**Key files:**

- `holmes/plugins/runbooks/__init__.py` — RunbookCatalog, load_runbook_catalog()
- `holmes/plugins/runbooks/catalog.json` — builtin catalog (currently empty)
- `holmes/plugins/toolsets/runbook/runbook_fetcher.py` — RunbookFetcher tool + RunbookToolset
- `holmes/core/tool_calling_llm.py` — _runbook_in_use flag, restricted tool gating
- `holmes/plugins/prompts/_runbook_instructions.jinja2` — user prompt
- `holmes/plugins/prompts/_runbooks_instructions.jinja2` — system prompt
- `holmes/plugins/prompts/investigation_procedure.jinja2` — investigation flow
- `holmes/config.py` — custom_runbook_catalogs config field

## Target Architecture

```
LOCAL SKILLS                              REMOTE (SUPABASE)
<skill-dir>/<name>/SKILL.md               HolmesRunbooks table
  (frontmatter + markdown content)         runbook_id, symptoms, instructions
         |                                          |
         v                                          v
   parse_local_skill()                    map_supabase_to_skill()
         |                                          |
         +-----------------+------------------------+
                           |
                           v
                    List[Skill]  (unified internal model)
                           |
                           v
              +--------------------------+
              |     Skill Catalog        |  descriptions only injected in prompt
              |     (~100 tokens each)   |  with context budget enforcement
              +--------------------------+
                           |
                   LLM matches by description
                           |
                           v
              +--------------------------+
              |   fetch_runbook tool     |  full content loaded on-demand
              |   (name unchanged)       |  returns content + metadata
              +--------------------------+
                           |
                           v
              +--------------------------+
              | Metadata-driven gate     |  allowed_restricted_tools from frontmatter
              | per-skill tool allowlist |  controls which restricted tools unlock
              +--------------------------+
```

## Unified Skill Model

Both local files and Supabase rows produce the same internal model:

```python
from enum import Enum
from typing import Optional, Union

from pydantic import BaseModel


class SkillSource(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"     # Supabase
    BUILTIN = "builtin"   # shipped with Holmes


class Skill(BaseModel):
    name: str                  # normalized: lowercase, hyphens only
    description: str           # shown to LLM for matching
    content: str               # full markdown body (loaded on fetch)
    source: SkillSource
    source_path: Optional[str] = None  # file path or Supabase UUID

    # Controls which restricted tools become available after this skill is fetched.
    # - None (default): no restricted tools unlocked
    # - True: ALL restricted tools unlocked
    # - List[str]: only named restricted tools unlocked
    allowed_restricted_tools: Optional[Union[bool, list[str]]] = None
```

### Mapping from current formats

**Local SKILL.md:**
```yaml
---
name: dns-troubleshooting
description: Diagnose DNS resolution failures in Kubernetes clusters
allowed_restricted_tools: true
---

## Goal
Diagnose DNS resolution issues...
```

Maps to:
```python
Skill(
    name="dns-troubleshooting",
    description="Diagnose DNS resolution failures in Kubernetes clusters",
    content="## Goal\nDiagnose DNS resolution issues...",
    source=SkillSource.LOCAL,
    source_path="/home/user/skills/dns-troubleshooting/SKILL.md",
    allowed_restricted_tools=True,
)
```

**Supabase row:**
```
runbook_id: "abc-123"
subject_name: "DNS Troubleshooting"
symptoms: "DNS resolution failures, CoreDNS issues"
runbook.instructions: "Step 1: Check CoreDNS pods..."
```

Maps to:
```python
Skill(
    name="abc-123",
    description="DNS Troubleshooting — DNS resolution failures, CoreDNS issues",
    content="Step 1: Check CoreDNS pods...",
    source=SkillSource.REMOTE,
    source_path="abc-123",
    allowed_restricted_tools=True,  # remote runbooks always unlock (preserves current behavior)
)
```

## Restricted Tool Gating

Current system uses a binary flag:
```python
# tool_calling_llm.py
self._runbook_in_use: bool = False
```

New system tracks which restricted tools are allowed:
```python
self._allowed_restricted_tools: set[str] | None = None
# None = no restricted tools allowed
# set() with "*" = all restricted tools allowed
# set("kubectl_exec", "kubectl_delete_pod") = only those tools

def _should_include_restricted_tool(self, tool_name: str) -> bool:
    if self._allowed_restricted_tools is None:
        return False
    if "*" in self._allowed_restricted_tools:
        return True
    return tool_name in self._allowed_restricted_tools
```

When `fetch_runbook` succeeds:
```python
if tool_name == "fetch_runbook" and status == SUCCESS:
    skill_metadata = tool_response.metadata  # parsed frontmatter
    art = skill_metadata.get("allowed_restricted_tools")
    if art is True:
        self._allowed_restricted_tools = {"*"}
    elif isinstance(art, list):
        if self._allowed_restricted_tools is None:
            self._allowed_restricted_tools = set()
        self._allowed_restricted_tools.update(art)
    # art is None/absent -> no change (skill is informational only)
```

Multiple skills can be fetched in one investigation. Their allowed tools **accumulate** — if skill A allows `kubectl_exec` and skill B allows `kubectl_delete_pod`, both are available.

### Frontmatter examples

```yaml
# Unlock ALL restricted tools
allowed_restricted_tools: true

# Unlock specific tools only
allowed_restricted_tools:
  - kubectl_exec
  - kubectl_delete_pod

# No restricted tools (default, can be omitted)
# This skill provides guidance only
```

## Skill Discovery and Loading

### Directory scanning

Scan `custom_skill_paths` recursively up to 2 levels deep for `SKILL.md` files:

```
/path/to/skills/
  dns-troubleshooting/
    SKILL.md                    <- found (level 1)
  kubernetes/
    pod-crashes/
      SKILL.md                  <- found (level 2)
    oom-kills/
      SKILL.md                  <- found (level 2)
  too/deep/nested/
    SKILL.md                    <- NOT found (level 3)
```

### Name collision resolution

**Priority order (highest wins):**

1. Supabase/remote runbooks
2. User skill directories (`custom_skill_paths`, in order listed)
3. Builtin skills (shipped with Holmes)

**Dedup key:** Normalize the `name` field — lowercase, replace underscores and spaces with hyphens.

On collision: use highest priority entry, log a `WARNING` with both sources:

```
WARNING: Skill "dns-troubleshooting" from /home/user/skills/ overridden by Supabase runbook with same name
```

### Context budget for descriptions

Skill descriptions are injected into the prompt. To prevent token overflow with many skills:

- **Budget:** 2% of model context window (min 16KB characters), matching Claude Code's approach.
- **On overflow:** Do NOT silently drop skills. Instead:
  1. Include skills up to budget, ordered by priority (remote > user > builtin).
  2. Log a `WARNING` listing which skills were excluded.
  3. **Stream a warning message over SSE** using the same pattern as conversation compaction — emit a `StreamEvents.AI_MESSAGE` event so the user sees it:

```python
# In the skill catalog loading path, before the LLM call:
if excluded_skills:
    warning = (
        f"Skill descriptions exceed context budget. "
        f"{len(excluded_skills)} skills were excluded: "
        f"{', '.join(s.name for s in excluded_skills[:5])}"
        f"{'...' if len(excluded_skills) > 5 else ''}. "
        f"Consider shortening skill descriptions or reducing the number of installed skills."
    )
    events.append(StreamMessage(
        event=StreamEvents.AI_MESSAGE,
        data={"content": warning},
    ))
```

This mirrors the existing compaction message pattern at `holmes/core/truncation/input_context_window_limiter.py:190-195`.

## Config Changes

### New config field

```yaml
# ~/.holmes/config.yaml
custom_skill_paths:
  - /path/to/my-skills/
  - /another/skills/dir/
```

### Deprecated config field

```yaml
# OLD - will cause Holmes to fail with an actionable error
custom_runbook_catalogs:
  - /path/to/catalog.json
```

On startup, if `custom_runbook_catalogs` is present in config, Holmes fails fast:

```
ERROR: 'custom_runbook_catalogs' is no longer supported.
Run 'holmes migrate-runbooks' to convert your runbooks to skills format,
then replace 'custom_runbook_catalogs' with 'custom_skill_paths' in your config.
See https://holmesgpt.dev/migration/runbooks-to-skills for details.
```

Uses Pydantic `extra="allow"` with a model validator to detect the old field, per the pattern documented in CLAUDE.md.

## Prompt Templates — No Rename

The term "runbook" remains in all LLM-facing prompts. "Runbook" is the correct ops terminology the LLM understands well. The authoring format changes (skills), but the runtime concept presented to the LLM stays as "runbook."

**No changes needed to:**

- `_runbook_instructions.jinja2`
- `_runbooks_instructions.jinja2`
- `_general_instructions.jinja2`
- `_noflag_general_instructions.jinja2`
- `investigation_procedure.jinja2`

**The `fetch_runbook` tool name also stays unchanged.**

The only prompt change: the catalog format string changes from `to_prompt_string()` on `RunbookCatalogEntry`/`RobustaRunbookInstruction` to `to_prompt_string()` on the unified `Skill` model. The output format stays similar.

## Migration Utility

A CLI command `holmes migrate-runbooks` that:

1. Reads all `catalog.json` files (builtin + any provided paths).
2. For each `RunbookCatalogEntry`:
   - Reads the linked `.md` file.
   - Creates `<output-dir>/<name>/SKILL.md` with generated YAML frontmatter:
     ```yaml
     ---
     name: <filename-without-extension>
     description: <description from catalog entry>
     allowed_restricted_tools: true
     ---
     <original markdown content>
     ```
3. Prints summary of migrated files and instructions to update config.

### Files to delete after migration

- `examples/custom_runbooks.yaml` — dead code, `issue_name` regex matching was never implemented in runtime.
- `holmes/plugins/runbooks/catalog.json` — replaced by `SKILL.md` files.

## What Does NOT Change

- **Supabase/remote runbooks** — `RobustaRunbookInstruction`, `SupabaseDal.get_runbook_catalog()`, `SupabaseDal.get_runbook_content()` all stay as-is. They map to virtual `Skill` objects at runtime.
- **`fetch_runbook` tool name** — no rename.
- **Prompt template terminology** — "runbook" everywhere in prompts.
- **RunbookToolset class** — renamed internally to reflect skills but the toolset `name` can stay "runbook".
- **`runbooks_enabled` template variable** — stays, controls whether skill/runbook instructions appear in prompts.

## Implementation Plan

### Phase 1: Core model + local skill loading

1. Create `Skill` model and `SkillCatalog` class.
2. Implement `parse_local_skill()` — read `SKILL.md`, parse YAML frontmatter, return `Skill`.
3. Implement directory scanner with 2-level recursive discovery.
4. Implement name normalization and priority-based deduplication.
5. Add `custom_skill_paths` config field with `extra="allow"` backwards compat detection.
6. Tests for parsing, scanning, dedup, and config.

### Phase 2: Replace RunbookFetcher internals

1. Update `RunbookFetcher.__init__()` to build catalog from `Skill` objects instead of `RunbookCatalog`.
2. Update `_get_md_runbook()` to load from skill directories.
3. Keep `_get_robusta_runbook()` unchanged (Supabase path).
4. Map Supabase results to `Skill` objects via `map_supabase_to_skill()`.
5. Implement context budget enforcement with overflow warning.
6. Wire overflow warning into SSE stream via `StreamEvents.AI_MESSAGE`.

### Phase 3: Metadata-driven restricted tool gating

1. Replace `_runbook_in_use: bool` with `_allowed_restricted_tools: set[str] | None`.
2. Update `_should_include_restricted_tools()` to per-tool checking.
3. Pass skill metadata through `StructuredToolResult` when `fetch_runbook` succeeds.
4. Update `_get_tools()` to filter restricted tools based on allowed set.
5. Update `reset_interaction_state()` to clear `_allowed_restricted_tools`.
6. Tests for all three modes: `None`, `True`, and explicit tool list.

### Phase 4: Migration and cleanup

1. Create `holmes migrate-runbooks` CLI command.
2. Add fail-fast detection for `custom_runbook_catalogs` in config.
3. Delete `examples/custom_runbooks.yaml`.
4. Delete `holmes/plugins/runbooks/catalog.json` (or convert to builtin skills if we ship any).
5. Update documentation.
6. Update existing tests.

## Open Questions

1. **Builtin skills:** The current `catalog.json` is empty. Do we ship any builtin skills with Holmes, or is this purely a user/server feature?
2. **`alerts` field on Supabase runbooks:** Currently only used in `to_prompt_string()` as part of the description. In the skill model, fold it into `description`. Confirm this is acceptable — the field is never used for programmatic matching.
3. **Supporting files:** Claude Code skills support a directory with `SKILL.md` + additional files (templates, scripts, reference docs). Do we want Holmes to support referencing supporting files from skills, or is the single `SKILL.md` sufficient for now?

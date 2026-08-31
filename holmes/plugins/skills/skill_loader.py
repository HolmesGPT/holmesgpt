import logging
import os
import re
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

import yaml
from pydantic import BaseModel

from holmes.plugins.skills import RobustaSkillInstruction

if TYPE_CHECKING:
    from holmes.core.supabase_dal import SupabaseDal

THIS_DIR = os.path.abspath(os.path.dirname(__file__))
BUILTIN_SKILLS_DIR = os.path.join(THIS_DIR, "builtin")

SKILL_FILENAME = "SKILL.md"


class SkillSource(str, Enum):
    BUILTIN = "builtin"
    USER = "user"
    REMOTE = "remote"
    PERSONAL = "personal"


# Priority tiers as named in the per-account hierarchy config. "custom" covers every
# filesystem skill (GitHub repo, inline Helm values, ConfigMap/Secret) since they all
# load as SkillSource.USER -- it is deliberately not called "github".
TIER_GLOBAL = "global"
TIER_CUSTOM = "custom"
TIER_PERSONAL = "personal"

DEFAULT_HIERARCHY_ORDER: List[str] = [TIER_GLOBAL, TIER_CUSTOM, TIER_PERSONAL]

TIER_TO_SOURCE = {
    TIER_GLOBAL: SkillSource.REMOTE,
    TIER_CUSTOM: SkillSource.USER,
    TIER_PERSONAL: SkillSource.PERSONAL,
}


class SkillHierarchyConfig(BaseModel):
    """Per-account name-collision policy, read from AccountSettings.settings.

    enabled defaults to False, which preserves today's behaviour: no cross-tier
    collision resolution at all. order is highest-priority-first; BUILTIN is always
    implicitly lowest and is not listed.
    """

    enabled: bool = False
    order: List[str] = DEFAULT_HIERARCHY_ORDER


class Skill(BaseModel):
    name: str
    description: str
    content: str
    source: SkillSource
    source_path: Optional[str] = None
    # Human-readable title for skills whose name is an opaque id (remote and personal skills
    # are named by their HolmesRunbooks UUID); None when the name is already readable
    # (filesystem skills). Doubles as the key for cross-tier collision detection.
    title: Optional[str] = None
    # GroupedIssues.aggregation_key values this skill is scoped to. Empty means all alerts,
    # as `clusters = null` means all clusters. Filesystem skills never set it.
    alerts: List[str] = []

    def applies_to_alert(self, alert_name: Optional[str]) -> bool:
        """Whether this skill may run for the given alert.

        Unscoped skills always apply. With NO alert context (chat, CLI) nothing is filtered:
        scoped skills stay on offer, with their alert names in the description so the model
        can judge relevance itself.
        """
        if not self.alerts or alert_name is None:
            return True
        return alert_name in self.alerts

    def collision_key(self) -> str:
        return normalize_skill_name(self.title or self.name)

    def to_prompt_string(self) -> str:
        return f"{self.name} | description: {self.description}"


class SkillCatalog(BaseModel):
    skills: List[Skill]

    def list_available_skills(self) -> List[str]:
        return [s.name for s in self.skills]

    def to_prompt_string(self) -> str:
        priority = {
            SkillSource.REMOTE: 0,
            SkillSource.PERSONAL: 1,
            SkillSource.USER: 2,
            SkillSource.BUILTIN: 3,
        }
        sorted_skills = sorted(self.skills, key=lambda s: priority.get(s.source, 99))

        remote_sources = (SkillSource.REMOTE, SkillSource.PERSONAL)
        local = [s for s in sorted_skills if s.source not in remote_sources]
        remote = [s for s in sorted_skills if s.source == SkillSource.REMOTE]
        personal = [s for s in sorted_skills if s.source == SkillSource.PERSONAL]

        parts: List[str] = [""]
        if local:
            # "custom skills" matches the UI's wording for skills loaded from
            # files; the model parrots this header back to users, so never call
            # them "local" here.
            parts.append("Here are built-in and custom skills:")
            parts.extend(f"* {s.to_prompt_string()}" for s in local)
        if remote:
            parts.append("\nHere are Robusta skills:")
            parts.extend(f"* {s.to_prompt_string()}" for s in remote)
        if personal:
            parts.append("\nHere are the current user's personal skills:")
            parts.extend(f"* {s.to_prompt_string()}" for s in personal)
        return "\n".join(parts)


def normalize_skill_name(name: str) -> str:
    """Normalize a skill name: lowercase, replace underscores/spaces with hyphens."""
    return re.sub(r"[\s_]+", "-", name.strip().lower())


def parse_skill_file(path: Path, source: SkillSource = SkillSource.USER) -> Skill:
    """Parse a SKILL.md file with YAML frontmatter + markdown body.

    Expected format:
        ---
        name: my-skill  (optional, defaults to parent directory name)
        description: What this skill does  (required)
        ---
        Markdown content here...
    """
    text = path.read_text(encoding="utf-8")

    # Split frontmatter from content
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter_str = parts[1]
            content = parts[2].strip()
        else:
            raise ValueError(
                f"Invalid SKILL.md format in {path}: missing closing '---'"
            )
    else:
        raise ValueError(
            f"SKILL.md file {path} must start with '---' (YAML frontmatter)"
        )

    frontmatter = yaml.safe_load(frontmatter_str) or {}

    name = frontmatter.get("name") or path.parent.name
    name = normalize_skill_name(name)

    description = frontmatter.get("description")
    if not description:
        raise ValueError(
            f"SKILL.md file {path} is missing required 'description' field in frontmatter"
        )

    return Skill(
        name=name,
        description=description,
        content=content,
        source=source,
        source_path=str(path),
    )


class SkillLoadProblem(BaseModel):
    """Something that went wrong while loading filesystem skills.

    `skill_name` is what splits the two kinds apart, and the split matters:

    * NAMED -- a specific SKILL.md exists but could not be parsed. We know exactly which
      skill is broken, so the mirror can show it with status="error" and the user can find
      it in the UI instead of wondering why their file has no effect.
    * UNNAMED -- a directory is missing or unreadable, or a configured path is not a skill
      source at all. We do not know what skills should have been there, so anything built
      from this load is incomplete and must not be used to DELETE.

    Only the unnamed kind blocks pruning: a parse failure is a known state we can represent,
    not an unknown one. Without that distinction a single malformed SKILL.md would suppress
    pruning for the whole cluster, so deleting an unrelated skill would leave its row behind.
    """

    error: str
    # Set only when a specific skill is identifiable. The normalized directory name, which
    # is exactly what parse_skill_file would have defaulted to had the frontmatter parsed.
    skill_name: Optional[str] = None
    source_path: Optional[str] = None
    source: Optional[SkillSource] = None


def scan_skill_directory(
    directory: Path,
    source: SkillSource = SkillSource.USER,
    max_depth: int = 2,
    problems: Optional[List[SkillLoadProblem]] = None,
) -> List[Skill]:
    """Scan a directory for SKILL.md files up to max_depth levels deep.

    When `problems` is passed, everything that went wrong is appended to it, so a caller can
    tell "this directory really holds no skills" from "this directory could not be read" --
    a distinction an empty return value cannot express. See SkillLoadProblem.
    """
    skills: List[Skill] = []
    directory = directory.resolve()

    if not directory.is_dir():
        logging.warning(f"Skill directory does not exist: {directory}")
        if problems is not None:
            problems.append(
                SkillLoadProblem(error=f"skill directory does not exist: {directory}")
            )
        return skills

    def on_walk_error(error: OSError) -> None:
        """os.walk swallows traversal errors unless this is passed.

        Without it, a directory that exists but cannot be read into yields nothing and
        records nothing -- and `is_dir()` above succeeds for such a directory, so the whole
        scan looks like "readable and genuinely empty". For a caller that DELETES based on
        what loaded that is the worst possible confusion: the mirror would prune rows for
        skills that are still on disk and merely unreadable this cycle. Applies to nested
        directories too, not just the root.
        """
        logging.warning(f"Failed to read skill directory {error.filename}: {error}")
        if problems is not None:
            problems.append(
                SkillLoadProblem(
                    error=f"failed to read skill directory {error.filename}: {error}"
                )
            )

    # followlinks=True so we traverse Kubernetes ConfigMap mounts, which
    # surface each key as `<dir>/<name>` -> `..data/<name>` -> a real file
    # under a timestamped `..NNN/` directory. Depth is computed against the
    # walked (unresolved) path so the symlink-traversed path is at depth 1,
    # not depth 2 from the resolved `..NNN/` real dir.
    seen_paths: set[str] = set()
    for root, dirs, files in os.walk(directory, followlinks=True, onerror=on_walk_error):
        depth = len(Path(root).relative_to(directory).parts)
        if depth >= max_depth:
            dirs.clear()
            continue

        if SKILL_FILENAME in files:
            skill_path = Path(root) / SKILL_FILENAME
            resolved = str(skill_path.resolve())
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                skill = parse_skill_file(skill_path, source=source)
                skills.append(skill)
            except Exception as e:
                logging.error(f"Failed to parse {skill_path}: {e}")
                if problems is not None:
                    problems.append(
                        SkillLoadProblem(
                            error=str(e),
                            # The dir name is what parse_skill_file falls back to, so the
                            # failed row keys exactly as the successful one would have.
                            skill_name=normalize_skill_name(Path(root).name),
                            source_path=str(skill_path),
                            source=source,
                        )
                    )

    return skills


def map_robusta_instruction_to_skill(
    instr: RobustaSkillInstruction,
    source: SkillSource = SkillSource.REMOTE,
) -> Skill:
    """Convert a Supabase RobustaSkillInstruction into a Skill.

    `name` stays the runbook UUID because that is the id the LLM passes to fetch_skill.
    The human name is kept in `title` so cross-tier collisions can be detected.
    """
    description = instr.title
    if instr.symptom:
        description = f"{instr.title} — {instr.symptom}"
    # Surface the alert scoping in the description too. The deterministic filter only applies
    # when there IS an alert context, so in chat this is the only signal the model has that a
    # skill is alert-specific -- and for an alert-only skill (no symptoms) it is the ONLY
    # description content beyond the title.
    if instr.alerts:
        description = f"{description} (applies to alerts: {', '.join(instr.alerts)})"

    return Skill(
        name=instr.id,
        description=description,
        content=instr.instruction or "",
        source=source,
        source_path=instr.id,
        title=instr.title,
        alerts=instr.alerts,
    )


class FilesystemSkills(BaseModel):
    """Builtin + filesystem skills, plus everything that went wrong loading them.

    An empty skill list is ambiguous on its own -- either "there genuinely are no skills" or
    "nothing could be read". A caller that only ADDS rows can ignore the difference; one that
    DELETES based on what loaded cannot, since treating a failed load as authoritative would
    prune rows for skills still on disk. `sources_ok` is what separates the two.
    """

    skills: List[Skill]
    problems: List[SkillLoadProblem] = []

    @property
    def sources_ok(self) -> bool:
        """Whether this load is complete enough to DELETE from.

        Only unnamed problems disqualify it. A named one (a SKILL.md that failed to parse)
        is a known state the caller can represent as a row, so it must not suppress pruning
        -- otherwise one malformed file would freeze the mirror for the whole cluster.
        """
        return all(p.skill_name is not None for p in self.problems)

    @property
    def failed_skills(self) -> List[SkillLoadProblem]:
        """Problems attributable to one skill, for callers that surface them to users."""
        return [p for p in self.problems if p.skill_name is not None]


def load_filesystem_skills_by_name(
    custom_skill_paths: Optional[List[Union[str, Path]]] = None,
    problems: Optional[List[SkillLoadProblem]] = None,
) -> dict[str, Skill]:
    """Load builtin skills, then filesystem skills which override builtins by name.

    Shared by load_skill_catalog and load_filesystem_skills so the override and
    error-handling rules cannot drift between the prompt catalog and the UI mirror.
    Unreadable sources are appended to `problems` when it is provided.
    """
    skills_by_name: dict[str, Skill] = {}

    # 1. Load builtin skills. A missing builtin dir is not recorded as a problem: it ships
    # with the package and is not what the mirror prunes on.
    builtin_dir = Path(BUILTIN_SKILLS_DIR)
    if builtin_dir.is_dir():
        for skill in scan_skill_directory(
            builtin_dir, source=SkillSource.BUILTIN, problems=problems
        ):
            skills_by_name[skill.name] = skill

    # 2. Load user skills from custom_skill_paths (overrides builtins)
    if custom_skill_paths:
        for skill_path in custom_skill_paths:
            path = Path(str(skill_path))
            if path.is_dir():
                for skill in scan_skill_directory(
                    path, source=SkillSource.USER, problems=problems
                ):
                    if skill.name in skills_by_name:
                        logging.warning(
                            f"Skill '{skill.name}' from {skill.source_path} "
                            f"overrides {skills_by_name[skill.name].source_path}"
                        )
                    skills_by_name[skill.name] = skill
            elif path.is_file() and path.name == SKILL_FILENAME:
                try:
                    skill = parse_skill_file(path, source=SkillSource.USER)
                    if skill.name in skills_by_name:
                        logging.warning(
                            f"Skill '{skill.name}' from {skill.source_path} "
                            f"overrides {skills_by_name[skill.name].source_path}"
                        )
                    skills_by_name[skill.name] = skill
                except Exception as e:
                    logging.error(f"Failed to parse skill file {path}: {e}")
                    if problems is not None:
                        problems.append(
                            SkillLoadProblem(
                                error=str(e),
                                skill_name=normalize_skill_name(path.parent.name),
                                source_path=str(path),
                                source=SkillSource.USER,
                            )
                        )
            else:
                logging.warning(f"Skill path is not a directory or SKILL.md file: {path}")
                if problems is not None:
                    problems.append(
                        SkillLoadProblem(
                            error=(
                                f"skill path is not a directory or "
                                f"{SKILL_FILENAME} file: {path}"
                            )
                        )
                    )

    return skills_by_name


def load_filesystem_skills(
    custom_skill_paths: Optional[List[Union[str, Path]]] = None,
) -> FilesystemSkills:
    """Load only the builtin + filesystem skills, reporting whether every source was read.

    Deliberately does not touch Supabase: global and personal skills live in HolmesRunbooks
    and must not be mirrored into HolmesCustomSkills.

    Unlike load_skill_catalog this returns an empty list rather than None for "no skills",
    because for the mirror an empty result is a meaningful state (prune everything) as long
    as `sources_ok` is True.
    """
    problems: List[SkillLoadProblem] = []
    skills_by_name = load_filesystem_skills_by_name(custom_skill_paths, problems)

    unreadable = [p for p in problems if p.skill_name is None]
    if unreadable:
        logging.warning(
            "%d skill source(s) could not be read; treating this load as incomplete: %s",
            len(unreadable),
            "; ".join(p.error for p in unreadable),
        )

    return FilesystemSkills(
        skills=list(skills_by_name.values()), problems=problems
    )


def _resolve_name_collisions(skills: List[Skill], order: List[str]) -> List[Skill]:
    """Keep only the highest-priority skill per normalized human name.

    Deterministic and resolved in Python rather than left to the prompt, so losers are not
    offered to the model. BUILTIN is always lowest and is not part of `order`.

    NOT an access control. This shapes the per-request prompt catalog only; the cached
    fetch_skill toolset is built without a hierarchy, so a loser's id stays resolvable if the
    model supplies it from somewhere else. Treat this as ranking what gets advertised, not as
    a guarantee that a shadowed skill can never run.

    Callers MUST apply cluster/agent/alert filtering BEFORE this, so a higher-tier skill that
    does not apply cannot suppress an applicable lower-tier one.
    """
    rank_by_source: dict[SkillSource, int] = {}
    for index, tier in enumerate(order):
        source = TIER_TO_SOURCE.get(tier)
        if source is None:
            logging.warning(
                f"Unknown skill hierarchy tier '{tier}' in skill_name_hierarchy_order; ignoring it"
            )
            continue
        rank_by_source.setdefault(source, index)

    # Unlisted sources sort below everything named, and BUILTIN below even those. Ranking all
    # unlisted equally would tie personal against builtin under order=["global"], letting
    # insertion order decide.
    unlisted = len(order) + 1
    builtin = unlisted + 1

    def rank(skill: Skill) -> int:
        if skill.source in rank_by_source:
            return rank_by_source[skill.source]
        return builtin if skill.source == SkillSource.BUILTIN else unlisted

    winners: dict[str, Skill] = {}
    for skill in skills:
        key = skill.collision_key()
        incumbent = winners.get(key)
        if incumbent is None:
            winners[key] = skill
            continue
        if rank(skill) < rank(incumbent):
            logging.info(
                f"Skill name collision on '{key}': {skill.source.value} skill wins over "
                f"{incumbent.source.value} (hierarchy order: {order})"
            )
            winners[key] = skill
        else:
            logging.info(
                f"Skill name collision on '{key}': {incumbent.source.value} skill wins over "
                f"{skill.source.value} (hierarchy order: {order})"
            )

    kept = {id(skill) for skill in winners.values()}
    return [skill for skill in skills if id(skill) in kept]


def load_skill_catalog(
    dal: Optional["SupabaseDal"] = None,
    custom_skill_paths: Optional[List[Union[str, Path]]] = None,
    user_id: Optional[str] = None,
    hierarchy: Optional[SkillHierarchyConfig] = None,
    alert_name: Optional[str] = None,
) -> Optional[SkillCatalog]:
    """Load skills from all sources and merge into a single catalog.

    Filesystem skills (builtin, then user) are keyed by name, so a user skill overrides a
    same-named builtin. Remote and personal skills are keyed by UUID.

    `user_id` must be the END USER's id from the request -- personal skills load only when it
    is present, keeping them out of server-initiated flows (alert triage, triggered
    workflows, scheduled prompts). Never pass SupabaseDal.user_id: that is Holmes's own
    service identity and would leak an identity into unattended runs.

    `hierarchy` controls cross-tier collision resolution; None or disabled (the default)
    means no dedup at all.

    `alert_name` is the firing alert's GroupedIssues.aggregation_key, set only for alert
    investigations. Present, it drops skills scoped to other alerts; absent (chat, CLI)
    nothing is filtered.
    """
    # 1 + 2. Builtin skills, then filesystem skills (which override builtins by name)
    skills_by_name = load_filesystem_skills_by_name(custom_skill_paths)

    # 3. Load remote (global) skills from Supabase
    if dal:
        try:
            supabase_entries = dal.get_skill_catalog()
            if supabase_entries:
                for entry in supabase_entries:
                    skill = map_robusta_instruction_to_skill(entry)
                    if skill.name in skills_by_name:
                        logging.warning(
                            f"Remote skill '{skill.name}' overrides "
                            f"{skills_by_name[skill.name].source_path}"
                        )
                    skills_by_name[skill.name] = skill
        except Exception as e:
            logging.error(f"Error loading skills from Supabase: {e}")

    # 4. Load the requesting end user's personal skills. Gated on an explicit end-user
    #    user_id so server-initiated runs never pick up anyone's personal skills.
    if dal and user_id:
        try:
            personal_entries = dal.get_personal_skill_catalog(user_id)
            if personal_entries:
                for entry in personal_entries:
                    skill = map_robusta_instruction_to_skill(
                        entry, source=SkillSource.PERSONAL
                    )
                    skills_by_name[skill.name] = skill
        except Exception as e:
            logging.error(f"Error loading personal skills from Supabase: {e}")

    if not skills_by_name:
        return None

    skills = list(skills_by_name.values())

    # BEFORE the hierarchy dedup, like the DAL's cluster filter: a higher-tier skill scoped to
    # a different alert must not suppress an applicable lower-tier one.
    if alert_name is not None:
        skills = [s for s in skills if s.applies_to_alert(alert_name)]

    # Cross-tier name-collision resolution. Runs AFTER the per-tier cluster/agent filtering
    # done by the DAL, so only skills that actually apply to this request compete.
    if hierarchy and hierarchy.enabled:
        skills = _resolve_name_collisions(skills, hierarchy.order or DEFAULT_HIERARCHY_ORDER)

    if not skills:
        return None

    return SkillCatalog(skills=skills)

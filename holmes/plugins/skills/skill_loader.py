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
    # Human-readable name used for cross-tier collision detection. Remote and personal
    # skills carry a UUID in `name` (that is the id the LLM must pass to fetch_skill),
    # so their human name has to be tracked separately. Filesystem skills leave this
    # unset because `name` already IS the human name.
    display_name: Optional[str] = None

    def collision_key(self) -> str:
        return normalize_skill_name(self.display_name or self.name)

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
            parts.append("Here are local skills:")
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
            raise ValueError(f"Invalid SKILL.md format in {path}: missing closing '---'")
    else:
        raise ValueError(f"SKILL.md file {path} must start with '---' (YAML frontmatter)")

    frontmatter = yaml.safe_load(frontmatter_str) or {}

    name = frontmatter.get("name") or path.parent.name
    name = normalize_skill_name(name)

    description = frontmatter.get("description")
    if not description:
        raise ValueError(f"SKILL.md file {path} is missing required 'description' field in frontmatter")

    return Skill(
        name=name,
        description=description,
        content=content,
        source=source,
        source_path=str(path),
    )


def scan_skill_directory(
    directory: Path, source: SkillSource = SkillSource.USER, max_depth: int = 2
) -> List[Skill]:
    """Scan a directory for SKILL.md files up to max_depth levels deep."""
    skills: List[Skill] = []
    directory = directory.resolve()

    if not directory.is_dir():
        logging.warning(f"Skill directory does not exist: {directory}")
        return skills

    # followlinks=True so we traverse Kubernetes ConfigMap mounts, which
    # surface each key as `<dir>/<name>` -> `..data/<name>` -> a real file
    # under a timestamped `..NNN/` directory. Depth is computed against the
    # walked (unresolved) path so the symlink-traversed path is at depth 1,
    # not depth 2 from the resolved `..NNN/` real dir.
    seen_paths: set[str] = set()
    for root, dirs, files in os.walk(directory, followlinks=True):
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

    return skills


def map_robusta_instruction_to_skill(
    instr: RobustaSkillInstruction,
    source: SkillSource = SkillSource.REMOTE,
) -> Skill:
    """Convert a Supabase RobustaSkillInstruction into a Skill.

    `name` stays the runbook UUID because that is the id the LLM passes to fetch_skill.
    The human name is kept in display_name so cross-tier collisions can be detected.
    """
    description = instr.title
    if instr.symptom:
        description = f"{instr.title} — {instr.symptom}"

    return Skill(
        name=instr.id,
        description=description,
        content=instr.instruction or "",
        source=source,
        source_path=instr.id,
        display_name=instr.title,
    )


def _resolve_name_collisions(skills: List[Skill], order: List[str]) -> List[Skill]:
    """Keep only the highest-priority skill per normalized human name.

    Resolution is deterministic and happens here in Python, not in the prompt: losers are
    dropped before anything reaches the model, so a shadowed duplicate can never be
    fetched. BUILTIN is always lowest priority and is not part of `order`.

    Callers MUST filter skills (cluster/agent scoping) BEFORE calling this, so that a
    higher-tier skill which does not apply to this request cannot suppress an applicable
    lower-tier one.
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

    # Anything not named in the order sorts below everything named, and BUILTIN sorts below
    # even that. Ranking every unlisted source equally would break the documented "builtin is
    # always lowest" contract under a partial order: with order=["global"], a personal and a
    # builtin skill would tie and insertion order would decide the winner.
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
) -> Optional[SkillCatalog]:
    """Load skills from all sources and merge into a single catalog.

    Filesystem skills (builtin, then user) are keyed by name, so a user skill still
    overrides a builtin of the same name. Remote (global) and personal skills are keyed
    by UUID and so never collide by name on their own.

    `user_id` must be the END USER's id from the request. Personal skills are loaded only
    when it is present, which keeps them out of every server-initiated flow (alert triage,
    triggered workflows, scheduled prompts). Never pass SupabaseDal.user_id here -- that is
    Holmes's own service identity and would leak an identity into unattended runs.

    `hierarchy` controls cross-tier name-collision resolution. When it is None or disabled
    (the default) no cross-tier dedup happens at all, which is exactly today's behaviour.
    """
    skills_by_name: dict[str, Skill] = {}

    # 1. Load builtin skills
    builtin_dir = Path(BUILTIN_SKILLS_DIR)
    if builtin_dir.is_dir():
        for skill in scan_skill_directory(builtin_dir, source=SkillSource.BUILTIN):
            skills_by_name[skill.name] = skill

    # 2. Load user skills from custom_skill_paths (overrides builtins)
    if custom_skill_paths:
        for skill_path in custom_skill_paths:
            path = Path(str(skill_path))
            if path.is_dir():
                for skill in scan_skill_directory(path, source=SkillSource.USER):
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
            else:
                logging.warning(f"Skill path is not a directory or SKILL.md file: {path}")

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

    # Cross-tier name-collision resolution. Runs AFTER the per-tier cluster/agent filtering
    # done by the DAL, so only skills that actually apply to this request compete.
    if hierarchy and hierarchy.enabled:
        skills = _resolve_name_collisions(skills, hierarchy.order or DEFAULT_HIERARCHY_ORDER)

    if not skills:
        return None

    return SkillCatalog(skills=skills)

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


class Skill(BaseModel):
    name: str
    description: str
    content: str
    source: SkillSource
    source_path: Optional[str] = None

    def to_prompt_string(self) -> str:
        return f"{self.name} | description: {self.description}"


class SkillCatalog(BaseModel):
    skills: List[Skill]

    def list_available_skills(self) -> List[str]:
        return [s.name for s in self.skills]

    def to_prompt_string(self) -> str:
        priority = {SkillSource.REMOTE: 0, SkillSource.USER: 1, SkillSource.BUILTIN: 2}
        sorted_skills = sorted(self.skills, key=lambda s: priority[s.source])

        local = [s for s in sorted_skills if s.source != SkillSource.REMOTE]
        remote = [s for s in sorted_skills if s.source == SkillSource.REMOTE]

        parts: List[str] = [""]
        if local:
            parts.append("Here are local skills:")
            parts.extend(f"* {s.to_prompt_string()}" for s in local)
        if remote:
            parts.append("\nHere are Robusta skills:")
            parts.extend(f"* {s.to_prompt_string()}" for s in remote)
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

    for root, dirs, files in os.walk(directory):
        depth = len(Path(root).resolve().relative_to(directory).parts)
        if depth >= max_depth:
            dirs.clear()
            continue

        if SKILL_FILENAME in files:
            skill_path = Path(root) / SKILL_FILENAME
            try:
                skill = parse_skill_file(skill_path, source=source)
                skills.append(skill)
            except Exception as e:
                logging.error(f"Failed to parse {skill_path}: {e}")

    return skills


def map_robusta_instruction_to_skill(
    instr: RobustaSkillInstruction,
) -> Skill:
    """Convert a Supabase RobustaSkillInstruction into a Skill."""
    description = instr.title
    if instr.symptom:
        description = f"{instr.title} — {instr.symptom}"

    return Skill(
        name=instr.id,
        description=description,
        content=instr.instruction or "",
        source=SkillSource.REMOTE,
        source_path=instr.id,
    )


def load_skill_catalog(
    dal: Optional["SupabaseDal"] = None,
    custom_skill_paths: Optional[List[Union[str, Path]]] = None,
) -> Optional[SkillCatalog]:
    """Load skills from all sources and merge into a single catalog.

    Priority (highest wins on name collision): remote > user > builtin.
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

    # 3. Load remote skills from Supabase (overrides all)
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

    if not skills_by_name:
        return None

    return SkillCatalog(skills=list(skills_by_name.values()))

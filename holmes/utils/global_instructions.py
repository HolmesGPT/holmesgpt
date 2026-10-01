from typing import Dict, List, Optional

from pydantic import BaseModel

from holmes.plugins.skills.skill_loader import SkillCatalog


class Instructions(BaseModel):
    instructions: List[str] = []


def _format_instructions_block(items: List[str], header: str = "") -> str:
    lines = [f"* {s}" for s in items if isinstance(s, str) and s.strip()]
    if not lines:
        return ""
    bullets = "\n".join(lines) + "\n"
    return f"{header}\n{bullets}"


def generate_skills_args(
    skill_catalog: Optional[SkillCatalog],
    global_instructions: Optional[Instructions] = None,
) -> Dict[str, str]:
    catalog_str = skill_catalog.to_prompt_string() if skill_catalog else ""

    gi_list = getattr(global_instructions, "instructions", None) or []
    global_block = (
        _format_instructions_block(
            [s for s in gi_list if isinstance(s, str)], header=""
        )
        if gi_list
        else ""
    )

    return {
        "skill_catalog": catalog_str,
        "global_instructions": global_block,
    }

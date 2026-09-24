
import pytest

from holmes.core.prompt import generate_user_prompt
from holmes.utils.global_instructions import generate_skills_args


class DummySkillCatalog:
    skills = (True,)  # non-empty so getattr check passes

    def to_prompt_string(self):
        return "SKILL CATALOG PROMPT"


class DummyInstructions:
    def __init__(self, instructions):
        self.instructions = instructions


@pytest.mark.parametrize(
    "user_prompt,skill_catalog,global_instructions,expected_substrings",
    [
        # Only user_prompt
        ("Prompt", None, None, ["Prompt"]),
        # Only skill_catalog
        ("", DummySkillCatalog(), None, ["SKILL CATALOG PROMPT"]),
        # Only global_instructions
        (
            "",
            None,
            DummyInstructions(["global 1", "global 2"]),
            ["global 1", "global 2"],
        ),
        # All together
        (
            "Prompt",
            DummySkillCatalog(),
            DummyInstructions(["global step"]),
            ["Prompt", "SKILL CATALOG PROMPT", "global step"],
        ),
    ],
)
def test_generate_user_prompt_with_skills(
    user_prompt,
    skill_catalog,
    global_instructions,
    expected_substrings,
):
    ctx = generate_skills_args(
        skill_catalog=skill_catalog,
        global_instructions=global_instructions,
    )

    result = generate_user_prompt(user_prompt, ctx)
    for substring in expected_substrings:
        assert substring in result

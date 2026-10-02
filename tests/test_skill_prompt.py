from types import SimpleNamespace

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
    "user_prompt,skill_catalog,issue_instructions,resource_instructions,global_instructions,expected_substrings",
    [
        # Only user_prompt
        ("Prompt", None, None, None, None, ["Prompt"]),
        # Only skill_catalog
        ("", DummySkillCatalog(), None, None, None, ["SKILL CATALOG PROMPT"]),
        # Only issue_instructions
        (
            "",
            None,
            ["step 1", "step 2"],
            None,
            None,
            ["My instructions to check", "* step 1", "* step 2"],
        ),
        # Only resource_instructions (with instructions and documents)
        (
            "",
            None,
            None,
            SimpleNamespace(
                instructions=["do X", "do Y"],
                documents=[
                    SimpleNamespace(url="http://doc1"),
                    SimpleNamespace(url="http://doc2"),
                ],
            ),
            None,
            [
                "My instructions to check",
                "* do X",
                "* do Y",
                "* fetch information from this URL: http://doc1",
                "* fetch information from this URL: http://doc2",
            ],
        ),
        # Only global_instructions
        (
            "",
            None,
            None,
            None,
            DummyInstructions(["global 1", "global 2"]),
            ["global 1", "global 2"],
        ),
        # All together
        (
            "Prompt",
            DummySkillCatalog(),
            ["issue step"],
            SimpleNamespace(
                instructions=["resource step"],
                documents=[SimpleNamespace(url="http://doc")],
            ),
            DummyInstructions(["global step"]),
            [
                "Prompt",
                "SKILL CATALOG PROMPT",
                "* issue step",
                "* resource step",
                "* fetch information from this URL: http://doc",
                "global step",
            ],
        ),
    ],
)
def test_generate_user_prompt_with_skills(
    user_prompt,
    skill_catalog,
    issue_instructions,
    resource_instructions,
    global_instructions,
    expected_substrings,
):
    ctx = generate_skills_args(
        skill_catalog=skill_catalog,
        issue_instructions=issue_instructions,
        resource_instructions=resource_instructions,
        global_instructions=global_instructions,
    )

    result = generate_user_prompt(user_prompt, ctx)
    for substring in expected_substrings:
        assert substring in result

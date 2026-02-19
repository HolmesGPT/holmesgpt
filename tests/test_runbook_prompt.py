from types import SimpleNamespace

import pytest

from holmes.core.prompt import generate_user_prompt
from holmes.utils.global_instructions import generate_runbooks_args


class DummyRunbookCatalog:
    def to_prompt_string(self):
        return "RUNBOOK CATALOG PROMPT"


class DummyInstructions:
    def __init__(self, instructions):
        self.instructions = instructions


@pytest.mark.parametrize(
    "user_prompt,runbook_catalog,issue_instructions,resource_instructions,global_instructions,expected_substrings",
    [
        # Only user_prompt
        ("Prompt", None, None, None, None, ["Prompt"]),
        # Only runbook_catalog
        ("", DummyRunbookCatalog(), None, None, None, ["RUNBOOK CATALOG PROMPT"]),
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
            DummyRunbookCatalog(),
            ["issue step"],
            SimpleNamespace(
                instructions=["resource step"],
                documents=[SimpleNamespace(url="http://doc")],
            ),
            DummyInstructions(["global step"]),
            [
                "Prompt",
                "RUNBOOK CATALOG PROMPT",
                "* issue step",
                "* resource step",
                "* fetch information from this URL: http://doc",
                "global step",
            ],
        ),
    ],
)
def test_generate_user_prompt_with_runbooks(
    user_prompt,
    runbook_catalog,
    issue_instructions,
    resource_instructions,
    global_instructions,
    expected_substrings,
):
    ctx = generate_runbooks_args(
        runbook_catalog=runbook_catalog,
        issue_instructions=issue_instructions,
        resource_instructions=resource_instructions,
        global_instructions=global_instructions,
    )

    result = generate_user_prompt(user_prompt, ctx)
    for substring in expected_substrings:
        assert substring in result

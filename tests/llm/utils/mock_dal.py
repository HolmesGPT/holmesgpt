# type: ignore
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import TypeAdapter

from holmes.core.resource_instruction import ResourceInstructions
from holmes.core.supabase_dal import SupabaseDal
from holmes.plugins.skills import RobustaSkillInstruction
from holmes.utils.global_instructions import Instructions
from tests.llm.utils.test_case_utils import read_file


class TestSupabaseDal(SupabaseDal):
    """Test DAL that loads fixture data from JSON files in the test case folder."""

    def __init__(
        self,
        test_case_folder: Path,
        issue_data: Optional[Dict] = None,
        resource_instructions: Optional[ResourceInstructions] = None,
        initialize_base: bool = True,
    ):
        if initialize_base:
            try:
                super().__init__(cluster="test")
            except:  # noqa: E722
                self.enabled = True
                self.cluster = "test"
                logging.warning(
                    "TestSupabaseDal could not connect to db. Running with fixture data only."
                )
        else:
            self.enabled = True
            self.cluster = "test"

        self._issue_data = issue_data
        self._resource_instructions = resource_instructions
        self._test_case_folder = test_case_folder

    def get_issue_data(self, issue_id: Optional[str]) -> Optional[Dict]:
        if self._issue_data is not None:
            return self._issue_data
        return super().get_issue_data(issue_id)

    def get_resource_instructions(
        self, type: str, name: Optional[str]
    ) -> Optional[ResourceInstructions]:
        if self._resource_instructions is not None:
            return self._resource_instructions
        return None

    def get_skill_catalog(self) -> Optional[List[RobustaSkillInstruction]]:
        # Fixture files keep the "runbook_" prefix to match existing test data
        file_path = self._get_fixture_file_path("runbook_catalog")
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return [RobustaSkillInstruction(**item) for item in data]
                    return None
            except Exception as e:
                logging.warning(f"Failed to read skill catalog fixture file: {e}")
        return None

    def get_skill_content(
        self, skill_id: str
    ) -> Optional[RobustaSkillInstruction]:
        file_path = self._get_fixture_file_path(f"runbook_content_{skill_id}")
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                    return RobustaSkillInstruction(**data)
            except Exception as e:
                logging.warning(f"Failed to read skill content fixture file: {e}")
        return None

    def _get_fixture_file_path(self, entity_type: str) -> Path:
        return self._test_case_folder / f"{entity_type}.json"

    def get_global_instructions_for_account(self) -> Optional[Instructions]:
        file_path = self._get_fixture_file_path("global_instructions")
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                    return Instructions(**data)
            except Exception as e:
                logging.warning(
                    f"Failed to read global instructions fixture file: {e}"
                )

        return None

# Backwards-compatible aliases
MockSupabaseDal = TestSupabaseDal

pydantic_resource_instructions = TypeAdapter(ResourceInstructions)
pydantic_instructions = TypeAdapter(Instructions)


def load_test_dal(
    test_case_folder: Path, initialize_base: bool = True
) -> TestSupabaseDal:
    """Load a TestSupabaseDal with fixture data from the test case folder."""
    issue_data_path = test_case_folder.joinpath(Path("issue_data.json"))
    issue_data = None
    if issue_data_path.exists():
        issue_data = json.loads(read_file(issue_data_path))

    resource_instructions_path = test_case_folder.joinpath(
        Path("resource_instructions.json")
    )
    resource_instructions = None
    if resource_instructions_path.exists():
        resource_instructions = pydantic_resource_instructions.validate_json(
            read_file(Path(resource_instructions_path))
        )

    return TestSupabaseDal(
        test_case_folder=test_case_folder,
        issue_data=issue_data,
        resource_instructions=resource_instructions,
        initialize_base=initialize_base,
    )


# Backwards-compatible alias
load_mock_dal = load_test_dal

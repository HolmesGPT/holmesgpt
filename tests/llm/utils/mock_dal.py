# type: ignore
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import TypeAdapter

from holmes.core.resource_instruction import ResourceInstructions
from holmes.core.supabase_dal import FindingType, SupabaseDal
from holmes.plugins.runbooks import RobustaRunbookInstruction
from holmes.utils.global_instructions import Instructions
from tests.llm.utils.test_case_utils import read_file


class TestSupabaseDal(SupabaseDal):
    """Test DAL that loads fixture data from JSON files in the test case folder."""

    def __init__(
        self,
        test_case_folder: Path,
        issue_data: Optional[Dict] = None,
        issues_metadata: Optional[List[Dict]] = None,
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
        self._issues_metadata = issues_metadata
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

    def get_runbook_catalog(self) -> Optional[List[RobustaRunbookInstruction]]:
        file_path = self._get_fixture_file_path("runbook_catalog")
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return [RobustaRunbookInstruction(**item) for item in data]
                    return None
            except Exception as e:
                logging.warning(f"Failed to read runbook catalog fixture file: {e}")
        return None

    def get_runbook_content(
        self, runbook_id: str
    ) -> Optional[RobustaRunbookInstruction]:
        file_path = self._get_fixture_file_path(f"runbook_content_{runbook_id}")
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                    return RobustaRunbookInstruction(**data)
            except Exception as e:
                logging.warning(f"Failed to read runbook content fixture file: {e}")
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

    def get_resource_recommendation(
        self,
        limit: int = 10,
        sort_by: str = "cpu_total",
        namespace: Optional[str] = None,
        name_pattern: Optional[str] = None,
        kind: Optional[str] = None,
        container: Optional[str] = None,
        clusters: Optional[List[str]] = None,
    ) -> Optional[List[Dict]]:
        return []

    def get_issues_metadata(
        self,
        start_datetime: str,
        end_datetime: str,
        limit: int = 100,
        workload: Optional[str] = None,
        ns: Optional[str] = None,
        clusters: Optional[List[str]] = None,
        include_external: bool = True,
        finding_type: FindingType = FindingType.CONFIGURATION_CHANGE,
    ) -> Optional[List[Dict]]:
        if self._issues_metadata is not None:
            filtered_data = []
            target_clusters = clusters if clusters else [self.cluster]
            for item in self._issues_metadata:
                creation_date, start, end = [
                    datetime.fromisoformat(dt.replace("Z", "+00:00")).astimezone(
                        timezone.utc
                    )
                    for dt in (item["creation_date"], start_datetime, end_datetime)
                ]
                if not (start <= creation_date <= end):
                    continue
                if item.get("finding_type") != finding_type.value:
                    continue
                item_cluster = item.get("cluster")
                if target_clusters == ["*"]:
                    if not include_external and item_cluster == "external":
                        continue
                else:
                    allowed = target_clusters + (["external"] if include_external else [])
                    if item_cluster not in allowed:
                        continue
                if workload:
                    if item.get("subject_name") != workload:
                        continue
                if ns:
                    if item.get("subject_namespace") != ns:
                        continue

                filtered_item = {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "subject_name": item.get("subject_name"),
                    "subject_namespace": item.get("subject_namespace"),
                    "subject_type": item.get("subject_type"),
                    "description": item.get("description"),
                    "starts_at": item.get("starts_at"),
                    "ends_at": item.get("ends_at"),
                }
                filtered_data.append(filtered_item)
            filtered_data = filtered_data[:limit]

            return filtered_data if filtered_data else None
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

    issues_metadata_path = test_case_folder.joinpath(Path("issues_metadata.json"))
    issues_metadata = None
    if issues_metadata_path.exists():
        issues_metadata = json.loads(read_file(issues_metadata_path))

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
        issues_metadata=issues_metadata,
        initialize_base=initialize_base,
    )


# Backwards-compatible alias
load_mock_dal = load_test_dal

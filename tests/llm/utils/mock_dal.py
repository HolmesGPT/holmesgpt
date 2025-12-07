# type: ignore
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import TypeAdapter

from holmes.core.supabase_dal import SupabaseDal, FindingType
from holmes.core.tool_calling_llm import ResourceInstructions
from holmes.plugins.runbooks import RobustaRunbookInstruction
from holmes.utils.global_instructions import Instructions
from tests.llm.utils.test_case_utils import read_file
from datetime import datetime, timezone


class MockSupabaseDal(SupabaseDal):
    def __init__(
        self,
        test_case_folder: Path,
        issue_data: Optional[Dict],
        issues_metadata: Optional[List[Dict]],
        resource_instructions: Optional[ResourceInstructions],
        generate_mocks: bool,
        initialize_base: bool = True,
    ):
        if initialize_base:
            try:
                self.mock_mode = False
                super().__init__(cluster="test")
            except:  # noqa: E722
                self.enabled = True
                self.cluster = "test"
                self.mock_mode = True
                logging.warning(
                    "Mocksupabase dal could not connect to db. Running in pure mock mode. real db calls and --generate-mock will fail."
                )
        else:
            # For only using mock data without initializing the base class
            # Don't call super().__init__ to avoid initializing Supabase connection
            # Set necessary attributes that would normally be set by SupabaseDal.__init__
            self.enabled = True
            self.cluster = "test"
            self.mock_mode = True

        self._issue_data = issue_data
        self._resource_instructions = resource_instructions
        self._issues_metadata = issues_metadata
        self._test_case_folder = test_case_folder
        self._generate_mocks = generate_mocks

    def get_issue_data(self, issue_id: Optional[str]) -> Optional[Dict]:
        if self._issue_data is not None:
            return self._issue_data
        else:
            data = super().get_issue_data(issue_id)
            if self._generate_mocks:
                file_path = self._get_mock_file_path("issue_data")

                with open(file_path, "w") as f:
                    f.write(json.dumps(data or {}, indent=2))
                    f.close()

                logging.warning(
                    f"A mock file was generated for you at {file_path} with the contentof dal.get_issue_data({issue_id})"
                )

            return data

    def get_resource_instructions(
        self, type: str, name: Optional[str]
    ) -> Optional[ResourceInstructions]:
        if self._resource_instructions is not None:
            return self._resource_instructions
        elif self.mock_mode:
            return None
        else:
            data = super().get_resource_instructions(type, name)
            if self._generate_mocks:
                file_path = self._get_mock_file_path("resource_instructions")

                with open(file_path, "w") as f:
                    f.write(json.dumps(data or {}, indent=2))
                    f.close()

                logging.warning(
                    f"A mock file was generated for you at {file_path} with the contentof dal.get_resource_instructions({type}, {name})"
                )

                return data

    def get_runbook_catalog(self) -> Optional[List[RobustaRunbookInstruction]]:
        # Try to read from mock file first
        mock_file_path = self._get_mock_file_path("runbook_catalog")
        if mock_file_path.exists():
            try:
                with open(mock_file_path, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return [RobustaRunbookInstruction(**item) for item in data]
                    return None
            except Exception as e:
                logging.warning(f"Failed to read runbook catalog mock file: {e}")
        return None

    def get_runbook_content(
        self, runbook_id: str
    ) -> Optional[RobustaRunbookInstruction]:
        # Try to read from mock file first
        mock_file_path = self._get_mock_file_path(f"runbook_content_{runbook_id}")
        if mock_file_path.exists():
            try:
                with open(mock_file_path, "r") as f:
                    data = json.load(f)
                    return RobustaRunbookInstruction(**data)
            except Exception as e:
                logging.warning(f"Failed to read runbook content mock file: {e}")
        return None

    def _get_mock_file_path(self, entity_type: str) -> Path:
        return self._test_case_folder / f"{entity_type}.json"

    def get_global_instructions_for_account(self) -> Optional[Instructions]:
        # Try to read from mock file first
        mock_file_path = self._get_mock_file_path("global_instructions")
        if mock_file_path.exists():
            try:
                with open(mock_file_path, "r") as f:
                    data = json.load(f)
                    return Instructions(**data)
            except Exception as e:
                logging.warning(f"Failed to read global instructions mock file: {e}")

        return None

    def get_workload_issues(self, *args) -> list:
        return []

    def get_resource_recommendation(
        self,
        limit: int = 10,
        sort_by: str = "cpu_total",
        namespace: Optional[str] = None,
        name_pattern: Optional[str] = None,
        kind: Optional[str] = None,
        container: Optional[str] = None,
    ) -> list:
        return []

    def get_issues_metadata(
        self,
        start_datetime: str,
        end_datetime: str,
        limit: int = 100,
        workload: Optional[str] = None,
        ns: Optional[str] = None,
        cluster: Optional[str] = None,
        finding_type: FindingType = FindingType.CONFIGURATION_CHANGE,
    ) -> Optional[List[Dict]]:
        if self._issues_metadata is not None:
            filtered_data = []
            if not cluster:
                cluster = self.cluster
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
                if item.get("cluster") != cluster:
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
        elif self.mock_mode:
            return None
        else:
            data = super().get_issues_metadata(
                start_datetime, end_datetime, limit, workload, ns, cluster, finding_type
            )
            if self._generate_mocks:
                file_path = self._get_mock_file_path("issues_metadata")

                with open(file_path, "w") as f:
                    f.write(json.dumps(data or {}, indent=2))
                    f.close()

                logging.warning(
                    f"A mock file was generated for you at {file_path} "
                    f"with the content of dal.get_issues_metadata("
                    f"{start_datetime}, {end_datetime}, {limit}, "
                    f"{workload}, {ns}, {finding_type})"
                )

            return data


pydantic_resource_instructions = TypeAdapter(ResourceInstructions)
pydantic_instructions = TypeAdapter(Instructions)


def load_mock_dal(
    test_case_folder: Path, generate_mocks: bool, initialize_base: bool = True
):
    issue_data_mock_path = test_case_folder.joinpath(Path("issue_data.json"))
    issue_data = None
    if issue_data_mock_path.exists():
        issue_data = json.loads(read_file(issue_data_mock_path))

    issues_metadata_path = test_case_folder.joinpath(Path("issues_metadata.json"))
    issues_metadata = None
    if issues_metadata_path.exists():
        issues_metadata = json.loads(read_file(issues_metadata_path))

    resource_instructions_mock_path = test_case_folder.joinpath(
        Path("resource_instructions.json")
    )
    resource_instructions = None
    if resource_instructions_mock_path.exists():
        resource_instructions = pydantic_resource_instructions.validate_json(
            read_file(Path(resource_instructions_mock_path))
        )

    return MockSupabaseDal(
        test_case_folder=test_case_folder,
        issue_data=issue_data,
        resource_instructions=resource_instructions,
        issues_metadata=issues_metadata,
        generate_mocks=generate_mocks,
        initialize_base=initialize_base,
    )

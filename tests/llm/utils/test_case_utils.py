import json
import logging
import os
from pathlib import Path
from typing import Any, List, Literal, Optional, TypeVar, Union, cast

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from typing_extensions import Dict

from holmes.config import Config
from holmes.core.llm import DefaultLLM
from holmes.core.models import InvestigateRequest, WorkloadHealthRequest
from holmes.core.prompt import append_file_to_user_prompt
from holmes.core.resource_instruction import ResourceInstructions
from tests.llm.utils.constants import ALLOWED_EVAL_TAGS, get_allowed_tags_list
from tests.llm.utils.test_env_vars import (
    CLASSIFIER_MODEL,
    MODEL,
    MODEL_LIST_FILE_LOCATION,
)


class SetupFailureError(Exception):
    """Custom exception for setup failures with additional context."""

    def __init__(
        self,
        message: str,
        test_id: str,
        command: Optional[str] = None,
        output: Optional[str] = None,
    ):
        super().__init__(message)
        self.test_id = test_id
        self.command = command
        self.output = output


def _model_list_exists() -> bool:
    if not MODEL_LIST_FILE_LOCATION:
        return False
    if not os.path.exists(MODEL_LIST_FILE_LOCATION):
        logging.warning(
            f"MODEL_LIST_FILE_LOCATION is set to '{MODEL_LIST_FILE_LOCATION}' but file does not exist. "
            "Falling back to MODEL environment variable."
        )
        return False
    return True


def _get_models_from_model_list() -> Optional[List[str]]:
    if not _model_list_exists():
        return None
    config = Config()
    models = config.get_models_list()
    return models or []


def _filter_models_from_env(
    requested_models: List[str], available_models: List[str]
) -> List[str]:
    missing = [m for m in requested_models if m not in available_models]
    if missing:
        available = ", ".join(available_models)
        raise ValueError(
            f"The following models from MODEL are not defined in the model list: "
            f"{', '.join(missing)}. Available models: {available}"
        )
    return requested_models


def get_models() -> List[str]:
    """Get list of models to test from MODEL env var (supports comma-separated list)."""
    # Strip whitespace from each model and filter out empty strings
    models = [m.strip() for m in MODEL.split(",") if m.strip()]

    model_list_models = _get_models_from_model_list()

    if model_list_models:
        models = _filter_models_from_env(models, model_list_models)

    if len(models) > 1:
        if not CLASSIFIER_MODEL:
            raise ValueError("Multiple models require CLASSIFIER_MODEL to be set")

    return models


def read_file(file_path: Path):
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read().strip()


TEST_CASE_ID_PATTERN = r"^[\d+]_(?:[a-z]+_)*[a-z]+$"
CONFIG_FILE_NAME = "test_case.yaml"


# TODO: do we ever use this? or do we always just use float below
class Evaluation(BaseModel):
    expected_score: float = 1
    type: Union[Literal["loose"], Literal["strict"]]


class LLMEvaluations(BaseModel):
    correctness: Union[float, Evaluation] = 1


class Message(BaseModel):
    message: str


T = TypeVar("T")


class HolmesTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    folder: str
    base_id: Optional[str] = None  # Base test case ID for parameterized tests
    mocked_date: Optional[str] = None
    tags: Optional[list[ALLOWED_EVAL_TAGS]] = None
    skip: Optional[bool] = None
    skip_reason: Optional[str] = None
    expected_output: Union[str, List[str]]  # Whether an output is expected
    evaluation: LLMEvaluations = LLMEvaluations()
    include_tool_calls: Optional[bool] = False  # Include tool calls in LLM evaluation
    before_test: Optional[str] = None
    after_test: Optional[str] = None
    setup_timeout: Optional[int] = None  # Override default setup timeout in seconds
    conversation_history: Optional[list[dict]] = None
    test_env_vars: Optional[Dict[str, str]] = (
        None  # Environment variables to set during test execution
    )
    mock_policy: Optional[str] = (
        "inherit"  # Mock policy: always_mock, never_mock, or inherit
    )
    mock_overrides: Optional[Dict[str, str]] = (
        None  # Per-toolset mock policy overrides: {"toolset_name": "always_mock|never_mock|inherit"}
    )
    description: Optional[str] = None
    generate_mocks: Optional[bool] = None
    toolsets: Optional[Dict[str, Any]] = None
    port_forwards: Optional[List[Dict[str, Any]]] = (
        None  # Port forwarding configurations
    )


class AskHolmesTestCase(HolmesTestCase, BaseModel):
    user_prompt: Union[
        str, List[str]
    ]  # The user's question(s) to ask holmes - can be single string or array
    cluster_name: Optional[str] = None
    include_files: Optional[List[str]] = None  # matches include_files option of the CLI
    runbooks: Optional[Dict[str, Any]] = None  # Optional runbook catalog override
    allow_toolset_failures: Optional[bool] = (
        False  # Allow toolsets to fail prerequisite checks (default False)
    )

    # Internal fields for variant handling
    variant_index: Optional[int] = None  # Which variant this instance represents
    original_user_prompt: Optional[Union[str, List[str]]] = (
        None  # Store original prompt(s)
    )
    test_type: Optional[str] = None  # The type of test to run


class InvestigateTestCase(HolmesTestCase, BaseModel):
    investigate_request: InvestigateRequest
    issue_data: Optional[Dict]
    resource_instructions: Optional[ResourceInstructions]
    expected_sections: Optional[Dict[str, Union[List[str], bool]]] = None
    request: Any = None


class HealthCheckTestCase(HolmesTestCase, BaseModel):
    workload_health_request: WorkloadHealthRequest
    issue_data: Optional[Dict]
    resource_instructions: Optional[ResourceInstructions]
    expected_sections: Optional[Dict[str, Union[List[str], bool]]] = None
    request: Any = None


def check_and_skip_test(
    test_case: HolmesTestCase, request=None, shared_test_infrastructure=None
) -> None:
    """Check if test should be skipped or has setup failures, and raise appropriate pytest exceptions.

    Args:
        test_case: A HolmesTestCase or any of its subclasses
        request: The pytest request object (optional, needed for setup failure tracking)
        shared_test_infrastructure: Shared test infrastructure dict (optional, needed for setup failure checking)
    """
    # Check if test should be skipped
    if test_case.skip:
        pytest.skip(test_case.skip_reason or "Test skipped")

    # Check for setup failures FIRST - before any other skips
    if shared_test_infrastructure is not None and request is not None:
        setup_failures = shared_test_infrastructure.get("setup_failures", {})
        if test_case.id in setup_failures:
            setup_error_detail = setup_failures[test_case.id]
            request.node.user_properties.append(("is_setup_failure", True))
            request.node.user_properties.append(
                ("setup_failure_detail", setup_error_detail)
            )

            # Just pass the full error detail through - no parsing needed
            raise SetupFailureError(
                message=setup_error_detail,
                test_id=test_case.id,
                command="Setup script",
                output=setup_error_detail,  # Full details including stdout/stderr
            )

    # Check if --only-setup is set (AFTER checking for setup failures)
    if request and request.config.getoption("--only-setup", False):
        print("   ⚙️  --only-setup mode: Skipping test execution, only ran setup")
        pytest.skip("Skipping test execution due to --only-setup flag")

    # Check if --only-cleanup is set
    if request and request.config.getoption("--only-cleanup", False):
        print(
            "   ⚙️  --only-cleanup mode: Skipping test execution, only running cleanup"
        )
        pytest.skip("Skipping test execution due to --only-cleanup flag")

    # Check for setup failures - early return if no infrastructure or request
    if shared_test_infrastructure is None or request is None:
        return

    # Check if test should be skipped due to port conflicts
    tests_to_skip_port_conflicts = shared_test_infrastructure.get(
        "tests_to_skip_port_conflicts", {}
    )
    if test_case.id in tests_to_skip_port_conflicts:
        skip_reason = tests_to_skip_port_conflicts[test_case.id]
        if request:
            request.node.user_properties.append(("port_conflict_skip", True))
            request.node.user_properties.append(("port_conflict_reason", skip_reason))
        pytest.skip(f"Test skipped due to port conflict: {skip_reason}")


class MockHelper:
    def __init__(self, test_cases_folder: Path) -> None:
        super().__init__()
        self._test_cases_folder = test_cases_folder

    def load_workload_health_test_cases(self) -> List[HealthCheckTestCase]:
        return cast(List[HealthCheckTestCase], self.load_test_cases())

    def load_investigate_test_cases(self) -> List[InvestigateTestCase]:
        return cast(List[InvestigateTestCase], self.load_test_cases())

    def load_ask_holmes_test_cases(self) -> List[AskHolmesTestCase]:
        return cast(List[AskHolmesTestCase], self.load_test_cases())

    def _add_port_forward_tag(self, test_case: HolmesTestCase) -> None:
        """Automatically add port-forward tag if test has port forwards."""
        if test_case and test_case.port_forwards:
            if test_case.tags is None:
                test_case.tags = []
            if "port-forward" not in test_case.tags:
                test_case.tags.append("port-forward")

    def load_test_cases(self) -> List[HolmesTestCase]:
        test_cases: List[HolmesTestCase] = []
        test_cases_ids: List[str] = [
            f
            for f in os.listdir(self._test_cases_folder)
            if not f.startswith(".")
            and os.path.isdir(self._test_cases_folder.joinpath(f))
        ]  # ignoring hidden files like Mac's .DS_Store and non-directory files
        for test_case_id in test_cases_ids:
            test_case_folder = self._test_cases_folder.joinpath(test_case_id)
            logging.debug(f"Evaluating potential test case folder: {test_case_folder}")
            try:
                config_dict = yaml.safe_load(
                    read_file(test_case_folder.joinpath(CONFIG_FILE_NAME))
                )
                config_dict["id"] = test_case_id
                config_dict["folder"] = str(test_case_folder)
                test_case: Optional[HolmesTestCase] = None

                if config_dict.get("user_prompt"):
                    config_dict["conversation_history"] = load_conversation_history(
                        test_case_folder
                    )
                    extra_prompt = load_include_files(
                        test_case_folder, config_dict.get("include_files", None)
                    )

                    original_user_prompt = config_dict["user_prompt"]

                    # Handle array of user prompts - create multiple test case instances
                    if isinstance(original_user_prompt, list):
                        for i, prompt in enumerate(original_user_prompt):
                            variant_config = config_dict.copy()
                            variant_config["user_prompt"] = prompt + extra_prompt
                            variant_config["variant_index"] = i
                            variant_config["original_user_prompt"] = (
                                original_user_prompt
                            )
                            variant_config["id"] = f"{test_case_id}[{i}]"
                            variant_config["base_id"] = (
                                test_case_id  # Store base ID for deduplication
                            )
                            test_case = TypeAdapter(AskHolmesTestCase).validate_python(
                                variant_config
                            )
                            self._add_port_forward_tag(test_case)
                            test_cases.append(test_case)
                        continue  # Skip the normal append at the end
                    else:
                        # Single prompt case
                        config_dict["user_prompt"] = (
                            config_dict["user_prompt"] + extra_prompt
                        )
                        config_dict["original_user_prompt"] = original_user_prompt
                        test_case = TypeAdapter(AskHolmesTestCase).validate_python(
                            config_dict
                        )

                elif self._test_cases_folder.name == "test_investigate":
                    config_dict["investigate_request"] = load_investigate_request(
                        test_case_folder
                    )
                    config_dict["issue_data"] = load_issue_data(test_case_folder)
                    config_dict["resource_instructions"] = load_resource_instructions(
                        test_case_folder
                    )
                    config_dict["request"] = TypeAdapter(InvestigateRequest)
                    test_case = TypeAdapter(InvestigateTestCase).validate_python(
                        config_dict
                    )
                elif self._test_cases_folder.name == "test_workload_health":
                    config_dict["workload_health_request"] = (
                        load_workload_health_request(test_case_folder)
                    )
                    config_dict["issue_data"] = load_issue_data(test_case_folder)
                    config_dict["resource_instructions"] = load_resource_instructions(
                        test_case_folder
                    )
                    config_dict["request"] = TypeAdapter(WorkloadHealthRequest)
                    test_case = TypeAdapter(HealthCheckTestCase).validate_python(
                        config_dict
                    )
                elif self._test_cases_folder.name == "compaction":
                    # Compaction tests only need conversation_history and expected_output
                    config_dict["conversation_history"] = load_conversation_history(
                        test_case_folder
                    )
                    test_case = TypeAdapter(HolmesTestCase).validate_python(config_dict)
                else:
                    # Skip test cases that don't match any known type
                    logging.debug(
                        f"Skipping test case {test_case_id} - unknown test type"
                    )
                    continue

                self._add_port_forward_tag(test_case)

                logging.debug(f"Successfully loaded test case {test_case_id}")
                test_cases.append(test_case)
            except ValidationError as e:
                error_msg = (
                    f"\n❌ VALIDATION ERROR in test case: {test_case_folder.name}\n"
                )
                error_msg += "=" * 60 + "\n"

                # Check for common issues first
                if (
                    not config_dict.get("user_prompt")
                    and self._test_cases_folder.name == "test_ask_holmes"
                ):
                    error_msg += "Missing required field: 'user_prompt'\n"
                    error_msg += "Note: Use 'user_prompt' instead of 'question' for ask_holmes tests\n"

                if "id" in config_dict:
                    error_msg += "⚠️  Found 'id' field in test_case.yaml - this should not be included\n"
                    error_msg += (
                        "   (ID is automatically derived from the directory name)\n"
                    )

                if "description" in config_dict and not config_dict.get("user_prompt"):
                    error_msg += (
                        "⚠️  Found 'description' but missing 'user_prompt' field\n"
                    )

                # Check for tag issues
                problematic_tags = []
                for error in e.errors():
                    if error["type"] == "literal_error" and "tags" in str(error["loc"]):
                        problematic_tags.append(error["input"])

                if problematic_tags:
                    error_msg += f"Invalid tags: {', '.join(problematic_tags)}\n"
                    error_msg += f"Allowed tags: {get_allowed_tags_list()}\n"

                # Show all validation errors
                error_msg += "\nDetailed validation errors:\n"
                for error in e.errors():
                    loc = " -> ".join(str(item) for item in error["loc"])
                    error_msg += f"  - {loc}: {error['msg']}\n"
                    if error.get("input") is not None:
                        error_msg += f"    Input value: {error['input']}\n"

                error_msg += "=" * 60
                print(error_msg)
                raise e
            except FileNotFoundError:
                logging.debug(
                    f"Folder {self._test_cases_folder}/{test_case_id} ignored because it is missing a {CONFIG_FILE_NAME} file."
                )
                continue
        logging.debug(f"Found {len(test_cases)} in {self._test_cases_folder}")

        return test_cases


def load_issue_data(test_case_folder: Path) -> Optional[Dict]:
    issue_data_mock_path = test_case_folder.joinpath(Path("issue_data.json"))
    if issue_data_mock_path.exists():
        return json.loads(read_file(issue_data_mock_path))
    return None


def load_resource_instructions(
    test_case_folder: Path,
) -> Optional[ResourceInstructions]:
    resource_instructions_mock_path = test_case_folder.joinpath(
        Path("resource_instructions.json")
    )
    if resource_instructions_mock_path.exists():
        return TypeAdapter(ResourceInstructions).validate_json(
            read_file(Path(resource_instructions_mock_path))
        )
    return None


def load_investigate_request(test_case_folder: Path) -> InvestigateRequest:
    investigate_request_path = test_case_folder.joinpath(
        Path("investigate_request.json")
    )
    if investigate_request_path.exists():
        return TypeAdapter(InvestigateRequest).validate_json(
            read_file(Path(investigate_request_path))
        )
    raise Exception(
        f"Investigate test case declared in folder {str(test_case_folder)} should have an investigate_request.json file but none is present"
    )


def load_workload_health_request(test_case_folder: Path) -> WorkloadHealthRequest:
    workload_health_request_path = test_case_folder.joinpath(
        Path("workload_health_request.json")
    )
    if workload_health_request_path.exists():
        return TypeAdapter(WorkloadHealthRequest).validate_json(
            read_file(Path(workload_health_request_path))
        )
    raise Exception(
        f"Workload health test case declared in folder {str(test_case_folder)} should have an workload_health_request.json file but none is present"
    )


def _parse_conversation_history_md_files(
    conversation_history_dir,
) -> None | List[Dict[str, str]]:
    # If no .md files are found in the directory, return None.
    md_files = sorted(list(conversation_history_dir.glob("*.md")))

    if not md_files:
        return None

    conversation_history: list[dict[str, str]] = []
    for md_file_path in md_files:
        # Get the filename without the .md extension (the "stem")
        # e.g., "01_system.md" -> "01_system"
        stem = md_file_path.stem

        # The filename pattern is "<index>_<role>.md".
        # The role is the part of the stem after the first underscore.
        # Example: "01_system" -> role is "system"
        # str.split("_", 1) splits the string at the first underscore.
        # It will return a list of two strings if an underscore is present.
        # e.g., "01_system".split("_", 1) -> ["01", "system"]
        try:
            _index_part, role = stem.split("_", 1)
        except ValueError:
            raise ValueError(
                f"Filename '{md_file_path.name}' in '{conversation_history_dir}' "
                f"does not conform to the expected '<index>_<role>.md' pattern."
            )

        content = md_file_path.read_text(encoding="utf-8")

        conversation_history.append({"role": role, "content": content})
    return conversation_history


def load_conversation_history(test_case_folder: Path) -> Optional[list[dict[str, str]]]:
    """
    Loads conversation history from either .md files or a JSON file.

    Supports two formats (checked in this order):
    1. Directory with .md files:
       test_case_folder/
           conversation_history/
               01_system.md
               02_user.md
               03_assistant.md
               ...

    2. Single JSON file:
       test_case_folder/
           conversation_history.json

       Format: [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, ...]

    Returns:
        List of message dicts with 'role' and 'content' keys, or None if not found.
    """
    conversation_history_dir = test_case_folder / "conversation_history"
    if conversation_history_dir.is_dir():
        conversation_history = _parse_conversation_history_md_files(
            conversation_history_dir
        )
    elif test_case_folder.joinpath("conversation_history.json").exists():
        conversation_history = json.loads(
            read_file(test_case_folder.joinpath("conversation_history.json"))
        )
    else:
        conversation_history = None

    return conversation_history


def load_include_files(
    test_case_folder: Path, include_files: Optional[list[str]]
) -> str:
    extra_prompt: str = ""
    if include_files:
        for file_path_str in include_files:
            file_path = Path(test_case_folder.joinpath(file_path_str))
            extra_prompt = append_file_to_user_prompt(extra_prompt, file_path)

    return extra_prompt


def create_eval_llm(model: str, tracer=None) -> DefaultLLM:
    if _model_list_exists():
        config = Config()
        return config._get_llm(model_key=model, tracer=tracer)  # type: ignore[arg-type]
    return DefaultLLM(model, tracer=tracer)

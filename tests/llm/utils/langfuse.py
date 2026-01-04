# type: ignore
import logging
from typing import Any, Dict, List, Optional, Union

from langfuse import Langfuse
from langfuse.client import DatasetItemClient
from langfuse.model import DatasetItem

from tests.llm.utils.test_case_utils import (
    AskHolmesTestCase,
    HolmesTestCase,
    InvestigateTestCase,
)

# init
langfuse = Langfuse()


def pop_test_case(
    test_cases: List[HolmesTestCase], id: str
) -> Optional[HolmesTestCase]:
    for test_case in test_cases:
        if test_case.id == id:
            test_cases.remove(test_case)
            return test_case


def pop_matching_test_case_if_exists(
    test_cases: List[HolmesTestCase], item: Union[DatasetItem, DatasetItemClient]
) -> Optional[HolmesTestCase]:
    """
    This function is expected to mutate the test_cases list then
    remove the matching test case from the list and return it
    """
    if not item.metadata:
        return None

    test_case_id = item.metadata.get("test_case", {}).get("id")
    return pop_test_case(test_cases, test_case_id)


def archive_dataset_item(
    dataset_name: str, item: Union[DatasetItem, DatasetItemClient]
):
    langfuse.create_dataset_item(
        id=item.id, dataset_name=dataset_name, status="ARCHIVED"
    )


def get_input(test_case: HolmesTestCase) -> Dict[str, Any]:
    input = {}
    if isinstance(test_case, AskHolmesTestCase):
        input = {"user_prompt": test_case.user_prompt}
    elif isinstance(test_case, InvestigateTestCase):
        input = test_case.investigate_request.model_dump()
    input["id"] = test_case.id

    return input


def upload_test_cases(test_cases: List[HolmesTestCase], dataset_name: str):
    logging.info(f"Uploading f{len(test_cases)} test cases to langfuse")
    try:
        dataset = langfuse.get_dataset(dataset_name)
    except Exception:
        langfuse.create_dataset(name=dataset_name)

    dataset = langfuse.get_dataset(dataset_name)
    logging.info(f"Found f{len(dataset.items)} existing dataset items")

    for item in dataset.items:
        test_case = pop_matching_test_case_if_exists(test_cases, item)

        if not test_case:
            archive_dataset_item(dataset.name, item)
            continue

        logging.info(f"Updating f{test_case.id}")
        # update the existing dataset item
        langfuse.create_dataset_item(
            id=item.id,
            dataset_name=dataset_name,
            input=get_input(test_case),
            expected_output=test_case.expected_output,
            metadata={"test_case": test_case.model_dump()},
        )

    for test_case in test_cases:
        logging.info(f"Creating f{test_case.id}")
        langfuse.create_dataset_item(
            dataset_name=dataset_name,
            input=get_input(test_case),
            expected_output={"answer": test_case.expected_output},
            metadata={"test_case": test_case.model_dump()},
        )

"""Utilities for handling test iterations with proper ordering."""

import os
from pathlib import Path
from typing import List, TypeVar

import pytest

from tests.llm.utils.test_case_utils import (
    MockHelper,
    HolmesTestCase,
)


def get_tags(test_case: HolmesTestCase):
    """
    Converts a list of tag strings into a list of pytest.mark objects.
    Example: ["smoke", "ui"] -> [pytest.mark.smoke, pytest.mark.ui]
    """
    if not test_case.tags:
        return []
    return [getattr(pytest.mark, tag) for tag in test_case.tags]


def add_id_and_tags_to_test_case(test_case: HolmesTestCase):
    return pytest.param(test_case, marks=get_tags(test_case), id=test_case.id)


T = TypeVar("T", bound=HolmesTestCase)


def expand_with_iterations(test_cases: List[T]) -> List:
    """
    Helper to expand test cases based on ITERATIONS env var and apply tags.

    Args:
        test_cases: List of test cases to expand

    Returns:
        List of test cases with proper IDs for multiple iterations
    """
    iterations = int(os.environ.get("ITERATIONS", "1"))

    # If only one iteration, return processed test cases
    if iterations == 1:
        return [add_id_and_tags_to_test_case(tc) for tc in test_cases]

    # For multiple iterations, create a flat list
    result = []
    for i in range(1, iterations + 1):
        for test_case in test_cases:
            result.append(add_id_and_tags_to_test_case(test_case))
    return result


def get_test_cases(test_cases_folder: Path) -> List:
    """
    Unified function to load test cases for any test type.

    Args:
        test_cases_folder: Path to the folder containing test cases

    Returns:
        List of test cases with iterations expanded and tags added
    """
    mh = MockHelper(test_cases_folder)

    # The MockHelper determines the test type based on the folder name,
    # so we just use the generic load_test_cases() method
    test_cases = mh.load_test_cases()

    return expand_with_iterations(test_cases)

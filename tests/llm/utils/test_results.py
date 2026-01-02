"""Data models and utilities for test result processing."""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TestResult:
    nodeid: str
    expected: str
    actual: str
    pass_fail: str
    tools_called: List[str]
    logs: str
    test_type: str = ""
    error_message: Optional[str] = None
    user_prompt: Optional[str] = None
    execution_time: Optional[float] = None
    holmes_duration: Optional[float] = (
        None  # Pure agent execution time (excludes setup/teardown)
    )
    num_llm_calls: Optional[int] = None  # Number of LLM API calls (turns)
    tool_call_count: Optional[int] = None  # Total number of tool calls
    expected_correctness_score: float = 1.0
    actual_correctness_score: float = 0.0
    mock_data_failure: bool = False

    @property
    def test_case_name(self) -> str:
        """Extract full test case name from pytest nodeid.

        Example: 'test_ask_holmes[01_how_many_pods]' -> '01_how_many_pods'
        Example: 'test_ask_holmes[01_how_many_pods0]' -> '01_how_many_pods' (removes iteration)
        """
        if "[" in self.nodeid and "]" in self.nodeid:
            test_case = self.nodeid.split("[")[1].split("]")[0]
            # Remove trailing digits (iteration numbers added by pytest)
            while test_case and test_case[-1].isdigit():
                # But keep digits that are part of the test name (e.g., "113_" in "113_checkout")
                # Check if removing this digit would leave us with underscore or nothing
                if len(test_case) == 1 or test_case[-2] == "_":
                    break
                test_case = test_case[:-1]
            return test_case
        return self.nodeid.split("::")[-1] if "::" in self.nodeid else self.nodeid


class TestStatus:
    """Encapsulates test status determination logic."""

    def __init__(self, result: dict):
        self.actual_score = int(result.get("actual_correctness_score", 0))
        self.expected_score = int(result.get("expected_correctness_score", 1))
        self.is_mock_failure = result.get("mock_data_failure", False)
        self.status = result.get(
            "status", ""
        )  # pytest status (passed, failed, skipped, etc.)
        self.is_setup_failure = result.get("is_setup_failure", False)
        # Throttling flags
        self.failed_due_to_throttling = result.get("failed_due_to_throttling", False)
        self.encountered_throttling = result.get("encountered_throttling", False)

    @property
    def passed(self) -> bool:
        return (
            self.actual_score == 1
        )  # TODO: possibly add `and not self.is_mock_failure`

    @property
    def is_skipped(self) -> bool:
        return self.status == "skipped"

    @property
    def is_regression(self) -> bool:
        if (
            self.is_skipped
            or self.passed
            or self.is_mock_failure
            or self.is_setup_failure
            or self.failed_due_to_throttling
        ):
            return False
        # Known failure (expected to fail)
        if self.actual_score == 0 and self.expected_score == 0:
            return False
        return True

    @property
    def markdown_symbol(self) -> str:
        if self.failed_due_to_throttling:
            return ":no_entry_sign:"
        elif self.is_skipped:
            return ":minus:"
        elif self.is_setup_failure:
            return ":construction:"
        elif self.is_mock_failure:
            return ":wrench:"
        elif self.passed:
            return ":white_check_mark:"
        elif self.actual_score == 0 and self.expected_score == 0:
            return ":warning:"
        else:
            return ":x:"

    @property
    def console_status(self) -> str:
        if self.failed_due_to_throttling:
            return "[red]THROTTLED[/red]"
        elif self.is_skipped:
            return "[cyan]SKIPPED[/cyan]"
        elif self.is_setup_failure:
            return "[magenta]SETUP FAIL[/magenta]"
        elif self.is_mock_failure:
            return "[yellow]MOCK FAILURE[/yellow]"
        elif self.passed:
            return "[green]PASS[/green]"
        else:
            return "[red]FAIL[/red]"

    @property
    def short_status(self) -> str:
        if self.failed_due_to_throttling:
            return "THROTTLED"
        elif self.is_skipped:
            return "SKIPPED"
        elif self.is_setup_failure:
            return "SETUP FAILURE"
        elif self.is_mock_failure:
            return "MOCK FAILURE"
        elif self.passed:
            return "PASS"
        else:
            return "FAIL"

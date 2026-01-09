"""Terminal reporting functionality for test results."""

import textwrap
from collections import defaultdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.table import Table

from holmes.common.env_vars import RECOMMENDED_OPENAI_MODEL
from tests.llm.utils.test_results import TestResult, TestStatus


class ResultType(Enum):
    """Types of test results to count."""

    PASSED = "passed"
    FAILED = "failed"  # Real test failures
    SKIPPED = "skipped"
    SETUP_FAILED = "setup_failed"
    MOCK_FAILED = "mock_failed"
    THROTTLED = "throttled"  # API throttling/overload
    VALID_RUNS = "valid_runs"  # Not skipped, setup failed, or throttled
    ALL = "all"


def count_results(results: List[dict], result_type: ResultType) -> int:
    """Count results of a specific type.

    Args:
        results: List of test result dictionaries
        result_type: Type of results to count

    Returns:
        Count of results matching the type
    """
    if not results:
        return 0

    if result_type == ResultType.ALL:
        return len(results)

    if result_type == ResultType.PASSED:
        return sum(1 for r in results if TestStatus(r).passed)

    if result_type == ResultType.SKIPPED:
        return sum(1 for r in results if r.get("status") == "skipped")

    if result_type == ResultType.SETUP_FAILED:
        return sum(1 for r in results if r.get("is_setup_failure", False))

    if result_type == ResultType.MOCK_FAILED:
        return sum(1 for r in results if r.get("mock_data_failure", False))

    if result_type == ResultType.THROTTLED:
        return sum(1 for r in results if r.get("failed_due_to_throttling", False))

    if result_type == ResultType.FAILED:
        # Real failures (not mock, setup, or throttled)
        return sum(
            1
            for r in results
            if not TestStatus(r).passed
            and not r.get("mock_data_failure", False)
            and not r.get("is_setup_failure", False)
            and not r.get("failed_due_to_throttling", False)
            and r.get("status") != "skipped"
        )

    if result_type == ResultType.VALID_RUNS:
        # Runs that actually executed (not skipped, setup failed, or throttled)
        skipped = count_results(results, ResultType.SKIPPED)
        setup_failed = count_results(results, ResultType.SETUP_FAILED)
        throttled = count_results(results, ResultType.THROTTLED)
        return len(results) - skipped - setup_failed - throttled

    raise ValueError(f"Unknown result type: {result_type}")


def _calculate_p90(times: List[float]) -> float:
    """Calculate the 90th percentile of a list of times.

    Args:
        times: List of execution times

    Returns:
        P90 time value, or 0 if times is empty
    """
    if not times:
        return 0

    sorted_times = sorted(times)
    p90_index = int(len(sorted_times) * 0.9)
    # Handle edge case for small sample sizes
    if p90_index >= len(sorted_times):
        p90_index = len(sorted_times) - 1
    return sorted_times[p90_index]


def _calculate_pass_percentage(passed: int, valid_runs: int) -> float:
    """Calculate pass percentage from passed count and valid runs.

    Args:
        passed: Number of passed tests
        valid_runs: Number of valid test runs

    Returns:
        Pass percentage (0-100)
    """
    if valid_runs > 0:
        return (passed / valid_runs) * 100
    return 0


def _get_failure_indicators(
    mock_failures: int, setup_failures: int, runs: Optional[int] = None
) -> str:
    """Get emoji indicators for failure types.

    Args:
        mock_failures: Number of mock data failures
        setup_failures: Number of setup failures
        runs: Total number of runs (optional, for partial setup failure detection)

    Returns:
        String with emoji indicators (e.g., " 📦" or " 🔧")
    """
    indicators = ""
    if mock_failures > 0:
        indicators = " 📦"
    elif setup_failures > 0:
        # Only show setup indicator if it's partial (not all runs failed setup)
        if runs is None or setup_failures < runs:
            indicators = " 🔧"
    return indicators


def _parse_test_name(nodeid: str, remove_iteration: bool = True) -> str:
    """Parse test name from nodeid, optionally removing iteration numbers.

    This function extracts the test case identifier from a pytest nodeid.

    Args:
        nodeid: Full node ID from pytest (e.g., 'test_ask_holmes[01_how_many_pods0]')
        remove_iteration: Whether to remove iteration numbers

    Returns:
        Parsed test name (e.g., '01_how_many_pods')
    """
    if "[" in nodeid and "]" in nodeid:
        # Extract the parametrized part between brackets
        test_case = nodeid.split("[")[1].split("]")[0]

        # Remove trailing iteration numbers if requested
        if remove_iteration and test_case:
            # Remove trailing digits (iteration numbers added by pytest)
            # Iteration numbers are appended directly to the end without separator
            while test_case and test_case[-1].isdigit():
                test_case = test_case[:-1]

        return test_case
    else:
        # No parameters, use the test function name
        return nodeid.split("::")[-1] if "::" in nodeid else nodeid


def handle_console_output(sorted_results: List[dict], terminalreporter=None) -> None:
    """Display Rich table and Braintrust links for developers."""
    if not sorted_results:
        return

    # Group results by test name to calculate P90
    test_time_groups = defaultdict(list)
    for result in sorted_results:
        test_key = result.get("nodeid", "")
        exec_time = result.get("holmes_duration")
        if exec_time:
            test_time_groups[test_key].append(exec_time)

    # Create Rich table
    console = Console()
    table = Table(
        title="🔍 HOLMES TESTS SUMMARY",
        show_header=True,
        header_style="bold magenta",
        show_lines=True,
    )

    # Add columns with specific widths (reduced to fit terminal width)
    table.add_column("Test", style="cyan", width=12)
    table.add_column("Status", justify="center", width=13)
    table.add_column("Time", justify="right", width=8)
    table.add_column("Turns", justify="right", width=5)
    table.add_column("Tools", justify="right", width=5)
    table.add_column("Cost", justify="right", width=8)
    table.add_column("User Prompt", style="white", width=16)
    table.add_column("Expected", style="green", width=16)
    table.add_column("Actual", style="yellow", width=16)

    # Add rows to table
    for result in sorted_results:
        status = TestStatus(result)

        # Wrap long content for table readability
        user_prompt_wrapped = (
            "\n".join(textwrap.wrap(result["user_prompt"], width=20))
            if result["user_prompt"]
            else ""
        )
        expected_wrapped = (
            "\n".join(textwrap.wrap(result["expected"], width=20))
            if result["expected"]
            else ""
        )
        actual_wrapped = (
            "\n".join(textwrap.wrap(result["actual"], width=20))
            if result["actual"]
            else ""
        )

        # Use test_case_name and model that are already in the result dict
        test_case_name = result["test_case_name"]
        model = result.get("model", "")
        if model:
            combined_test_name = f"{test_case_name} ({model})"
        else:
            combined_test_name = f"{test_case_name} ({result['test_type']})"
        # Wrap test name to fit column
        test_name_wrapped = "\n".join(textwrap.wrap(combined_test_name, width=10))

        # Format execution time - show individual time for this specific test run
        exec_time = result.get("holmes_duration")
        time_str = _format_time(exec_time)

        # Format turns (LLM calls)
        num_llm_calls = result.get("num_llm_calls")
        turns_str = str(num_llm_calls) if num_llm_calls and num_llm_calls > 0 else "—"

        # Format tool calls
        tool_call_count = result.get("tool_call_count")
        tools_str = (
            str(tool_call_count) if tool_call_count and tool_call_count > 0 else "—"
        )

        # Format cost - show individual cost for this specific test run
        cost = result.get("cost", 0)
        if cost > 0:
            cost_str = f"${cost:.4f}"
        else:
            cost_str = "—"

        # Disabled for now - get analysis for failed tests with openai
        # analysis = _get_analysis_for_result(test_result)

        table.add_row(
            test_name_wrapped,
            status.console_status,
            time_str,
            turns_str,
            tools_str,
            cost_str,
            user_prompt_wrapped,
            expected_wrapped,
            actual_wrapped,
        )

    # Use force_terminal to ensure output is displayed even when captured
    console.print(table)

    # Print summary statistics table (always shown)
    _print_summary_statistics(sorted_results, console)

    # Print model comparison tables if multiple models detected
    _print_model_comparison_if_multiple(sorted_results, console)


def _get_analysis_for_result(result: TestResult) -> str:
    """Get analysis text for a test result, with proper text wrapping."""
    if "PASS" in result.pass_fail:
        return ""

    try:
        analysis = _get_llm_analysis(result)
        # Wrap analysis text for table readability
        return "\n".join(textwrap.wrap(analysis, width=26))
    except Exception as e:
        return f"Analysis failed: {str(e)}"


def _get_llm_analysis(result: TestResult) -> str:
    """Get LLM analysis of test failure using GPT-4o.

    Args:
        result: TestResult object containing test details

    Returns:
        Analysis text explaining why the test failed
    """
    from litellm import completion

    # Check if this is a MockDataError case and add context
    mock_data_context = ""
    if result.mock_data_failure:
        mock_data_context = "\n\nIMPORTANT CONTEXT: This test failed due to MockDataError - no mock data files were found for the tool calls that the agent tried to make. This is a test infrastructure issue, not a problem with the agent's logic."

    prompt = textwrap.dedent(f"""\
        Analyze this failed eval for an AIOps agent why it failed.
        TEST: {result.test_case_name}
        EXPECTED: {result.expected}
        ACTUAL: {result.actual}
        TOOLS CALLED: {', '.join(result.tools_called)}
        ERROR: {result.error_message or 'Test assertion failed'}

        LOGS:
        {result.logs if result.logs else 'No logs available'}{mock_data_context}

        Please provide a concise analysis (2-3 sentences) and categorize this as one of:
        - MockDataError - the test failed because mock data files were missing for the tool calls (this is a test infrastructure issue).
          To fix (show bullet points with each option - any are valid solutions so user should see all options):
          - Run with RUN_LIVE=true
          - Use --generate-mocks (may cause inconsistent data)
          - Use --regenerate-all-mocks (ensures consistency)
        - Problem with mock data - the test is failing due to incorrect or incomplete mock data, but the agent itself did the correct queries you would expect it to do
        - Setup issue - the test is failing due to an issue with the test setup, such as missing tools or incorrect before_test/after_test configuration
        - Real failure - the test is failing because the agent did not perform as expected, and this is a real issue that needs to be fixed
        """)

    try:
        response = completion(
            model=RECOMMENDED_OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.1,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Analysis failed: {e}"


def _get_status_emoji(
    pass_pct: float,
    setup_fail: bool = False,
    all_skipped: bool = False,
    all_throttled: bool = False,
) -> str:
    """Get the appropriate emoji based on pass percentage and status.

    Args:
        pass_pct: Pass percentage (0-100)
        setup_fail: Whether all runs were setup failures
        all_skipped: Whether all runs were skipped
        all_throttled: Whether all runs were throttled

    Returns:
        Emoji string representing the status
    """
    if all_throttled:
        return "🚫"
    if all_skipped:
        return "➖"
    if setup_fail:
        return "🔧"
    if pass_pct == 100.0:
        return "✅"
    elif pass_pct == 0.0:
        return "❌"
    else:
        return "⚠️"


def _get_pass_color(pass_pct: float, bold: bool = False) -> str:
    """Get color for pass percentage.

    Args:
        pass_pct: Pass percentage (0-100)
        bold: Whether to use bold variant

    Returns:
        Color name for Rich formatting
    """
    base_color = "green" if pass_pct == 100 else "red" if pass_pct == 0 else "yellow"
    return f"bold {base_color}" if bold else base_color


def _format_pass_percentage(pass_pct: float, with_emoji: bool = False) -> str:
    """Format pass percentage with color and optionally emoji.

    Args:
        pass_pct: Pass percentage (0-100)
        with_emoji: Whether to include emoji prefix

    Returns:
        Formatted string with Rich color tags
    """
    color = _get_pass_color(pass_pct)

    if with_emoji:
        emoji = _get_status_emoji(pass_pct)
        return f"{emoji} [{color}]{pass_pct:.1f}%[/{color}]"
    else:
        return f"[{color}]{pass_pct:.1f}%[/{color}]"


def _create_separator_row(column_widths: List[int]) -> List[str]:
    """Create a separator row for tables.

    Args:
        column_widths: List of column widths

    Returns:
        List of separator strings
    """
    return ["─" * width for width in column_widths]


def _format_time(time_value: Optional[float], default: str = "N/A") -> str:
    """Format a time value for display.

    Args:
        time_value: Time in seconds or None
        default: Default string if time is None or <= 0

    Returns:
        Formatted time string
    """
    return f"{time_value:.1f}s" if time_value and time_value > 0 else default


def _get_time_color(value: float, best: float, worst: float) -> str:
    """Get color for a time value based on best/worst in the set.

    Args:
        value: The time value to color
        best: The best (minimum) time
        worst: The worst (maximum) time

    Returns:
        Color name for Rich formatting
    """
    if best == worst:
        # All times are the same
        return ""
    elif value == best:
        return "green"
    elif value == worst:
        return "red"
    else:
        return "yellow"


def _format_colored_times(
    times_dict: Dict[str, Optional[float]], label: str = ""
) -> List[str]:
    """Format a row of times with appropriate coloring.

    Args:
        times_dict: Dictionary mapping model names to time values
        label: Optional label prefix for the time (e.g., "Avg: ")

    Returns:
        List of formatted time strings for each model
    """
    # Filter out None values
    valid_times = [(k, v) for k, v in times_dict.items() if v is not None and v > 0]

    if not valid_times:
        return ["—"] * len(times_dict)

    # Find best and worst
    time_values = [v for _, v in valid_times]
    best_time = min(time_values)
    worst_time = max(time_values)

    # Build formatted strings
    result = []
    for model in times_dict.keys():
        time_val = times_dict[model]
        if time_val is None or time_val <= 0:
            result.append("—")
        else:
            time_str = f"{time_val:.1f}s"
            color = _get_time_color(time_val, best_time, worst_time)
            if color:
                result.append(f"{label}[{color}]{time_str}[/{color}]")
            else:
                result.append(f"{label}{time_str}")

    return result


def _format_colored_costs(costs_dict: Dict[str, float]) -> List[str]:
    """Format a row of costs with appropriate coloring (cheapest=green, most expensive=red).

    Args:
        costs_dict: Dictionary mapping model names to cost values

    Returns:
        List of formatted cost strings for each model
    """
    # Filter out zero/None values
    valid_costs = [(k, v) for k, v in costs_dict.items() if v and v > 0]

    if not valid_costs:
        return ["—"] * len(costs_dict)

    # Find cheapest and most expensive
    cost_values = [v for _, v in valid_costs]
    cheapest = min(cost_values)
    most_expensive = max(cost_values)

    # Build formatted strings
    result = []
    for model in costs_dict.keys():
        cost = costs_dict[model]
        if cost is None or cost <= 0:
            result.append("—")
        else:
            cost_str = f"${cost:.4f}"
            # Color: green for cheapest, red for most expensive, yellow for middle
            if cheapest == most_expensive:
                # All costs are the same
                result.append(cost_str)
            elif cost == cheapest:
                result.append(f"[green]{cost_str}[/green]")
            elif cost == most_expensive:
                result.append(f"[red]{cost_str}[/red]")
            else:
                result.append(f"[yellow]{cost_str}[/yellow]")

    return result


class TestStatistics:
    """Calculates statistics for test results."""

    def __init__(self, results: List[dict]):
        """Initialize with test results.

        Args:
            results: List of test result dictionaries
        """
        self.results = results
        self._build_data()

    def _build_data(self) -> None:
        """Organize results by test case and model."""
        self._data: Dict[str, Dict[str, List[dict]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for result in self.results:
            model = result.get("model", "Unknown")
            test_case = result["test_case_name"]
            self._data[test_case][model].append(result)

        self._models = sorted({r.get("model", "Unknown") for r in self.results})
        self._test_cases = sorted(self._data.keys())

    @property
    def models(self) -> List[str]:
        """Get sorted list of unique models."""
        return self._models

    @property
    def test_cases(self) -> List[str]:
        """Get sorted list of unique test cases."""
        return self._test_cases

    def _calculate_stats_for_results(
        self, results: List[dict]
    ) -> Optional[Dict[str, Any]]:
        """Calculate statistics for a list of results.

        Args:
            results: List of result dictionaries

        Returns:
            Dictionary with statistics or None if no results
        """
        if not results:
            return None

        # Calculate all statistics using count_results
        runs = count_results(results, ResultType.ALL)
        passed = count_results(results, ResultType.PASSED)
        skipped = count_results(results, ResultType.SKIPPED)
        setup_failures = count_results(results, ResultType.SETUP_FAILED)
        throttled = count_results(results, ResultType.THROTTLED)
        valid_runs = count_results(results, ResultType.VALID_RUNS)

        times = [
            r.get("holmes_duration", 0) for r in results if r.get("holmes_duration")
        ]

        # Calculate cost metrics
        total_cost = sum(r.get("cost", 0) for r in results)
        avg_cost = total_cost / len(results) if results else 0

        return {
            "runs": runs,
            "passed": passed,
            "skipped": skipped,
            "setup_failures": setup_failures,
            "throttled": throttled,
            "times": times,
            "avg_time": sum(times) / len(times) if times else None,
            "valid_runs": valid_runs,
            "pass_rate": (passed / valid_runs * 100) if valid_runs > 0 else 0,
            "total_cost": total_cost,
            "avg_cost": avg_cost,
        }

    def get_stats(self, test_case: str, model: str) -> Optional[Dict[str, Any]]:
        """Get statistics for a specific test case and model combination.

        Args:
            test_case: Name of the test case
            model: Name of the model

        Returns:
            Dictionary with statistics or None if no results exist
        """
        results = self._data[test_case].get(model, [])
        return self._calculate_stats_for_results(results)

    def get_test_times(self, test_case: str) -> Dict[str, Optional[float]]:
        """Get average execution times for a test case across all models.

        Args:
            test_case: Name of the test case

        Returns:
            Dictionary mapping model names to average times
        """
        result = {}
        for model in self.models:
            stats = self.get_stats(test_case, model)
            result[model] = stats["avg_time"] if stats else None
        return result

    def get_test_time_range(
        self, test_case: str
    ) -> Tuple[Optional[float], Optional[float]]:
        """Get min and max average times for a test case across models.

        Args:
            test_case: Name of the test case

        Returns:
            Tuple of (min_time, max_time) or (None, None) if no valid times
        """
        times = [t for t in self.get_test_times(test_case).values() if t and t > 0]
        return (min(times), max(times)) if times else (None, None)

    def get_model_summary(self, model: str) -> Dict[str, Any]:
        """Get summary statistics for a model across all test cases.

        Args:
            model: Name of the model

        Returns:
            Dictionary with aggregate statistics
        """
        summaries = [self.get_stats(test_case, model) for test_case in self.test_cases]

        # Filter out None results
        valid_summaries = [s for s in summaries if s]

        if not valid_summaries:
            return {
                "runs": 0,
                "passed": 0,
                "skipped": 0,
                "setup_failures": 0,
                "throttled": 0,
                "valid_runs": 0,
                "pass_rate": 0,
                "avg_time": None,
                "p90_time": None,
                "all_times": [],
            }

        total_runs = sum(s["runs"] for s in valid_summaries)
        total_passed = sum(s["passed"] for s in valid_summaries)
        total_skipped = sum(s["skipped"] for s in valid_summaries)
        total_setup_fail = sum(s["setup_failures"] for s in valid_summaries)
        total_throttled = sum(s.get("throttled", 0) for s in valid_summaries)
        total_cost = sum(s.get("total_cost", 0) for s in valid_summaries)

        all_times = []
        for s in valid_summaries:
            all_times.extend(s["times"])

        valid_runs = total_runs - total_skipped - total_setup_fail - total_throttled

        return {
            "runs": total_runs,
            "passed": total_passed,
            "skipped": total_skipped,
            "setup_failures": total_setup_fail,
            "throttled": total_throttled,
            "valid_runs": valid_runs,
            "pass_rate": (total_passed / valid_runs * 100) if valid_runs > 0 else 0,
            "avg_time": sum(all_times) / len(all_times) if all_times else None,
            "p90_time": _calculate_p90(all_times) if all_times else None,
            "all_times": all_times,
            "total_cost": total_cost,
        }

    def get_model_times(self, metric: str = "avg") -> Dict[str, Optional[float]]:
        """Get time metric for all models.

        Args:
            metric: "avg" or "p90"

        Returns:
            Dictionary mapping model names to time values
        """
        result = {}
        for model in self.models:
            summary = self.get_model_summary(model)
            if metric == "avg":
                result[model] = summary["avg_time"]
            elif metric == "p90":
                result[model] = summary["p90_time"]
            else:
                raise ValueError(f"Unknown metric: {metric}")
        return result

    def get_model_costs(self) -> Dict[str, float]:
        """Get total cost for all models.

        Returns:
            Dictionary mapping model names to total costs
        """
        result = {}
        for model in self.models:
            summary = self.get_model_summary(model)
            result[model] = summary.get("total_cost", 0.0)
        return result

    def get_unique_tags(self) -> List[str]:
        """Get sorted list of all unique tags across all test results.

        Returns:
            Sorted list of unique tag strings
        """
        tags = set()
        for result in self.results:
            result_tags = result.get("tags", [])
            if result_tags:
                tags.update(result_tags)
        return sorted(tags)

    def get_tag_stats(self, tag: str, model: str) -> Optional[Dict[str, Any]]:
        """Get statistics for a specific tag-model combination.

        Args:
            tag: Tag name to filter by
            model: Model name to filter by

        Returns:
            Dictionary with statistics (same format as get_stats) or None if no results
        """
        # Collect all results for this model that have the specified tag
        results = []
        for test_case in self._test_cases:
            model_results = self._data[test_case].get(model, [])
            for result in model_results:
                if tag in result.get("tags", []):
                    results.append(result)

        return self._calculate_stats_for_results(results)

    def get_time_range(
        self, model_times: Dict[str, Optional[float]]
    ) -> Tuple[Optional[float], Optional[float]]:
        """Get min and max from a dictionary of times.

        Args:
            model_times: Dictionary mapping names to time values

        Returns:
            Tuple of (min_time, max_time) or (None, None) if no valid times
        """
        valid_times = [t for t in model_times.values() if t is not None and t > 0]
        return (min(valid_times), max(valid_times)) if valid_times else (None, None)


def _detect_multiple_models(sorted_results: List[dict]) -> bool:
    """Detect if results contain multiple models.

    Args:
        sorted_results: List of test results

    Returns:
        True if multiple models are present
    """
    models = set()
    for result in sorted_results:
        model = result.get("model", "Unknown")
        models.add(model)
    return len(models) > 1


def _print_model_comparison_table(sorted_results: List[dict], console: Console) -> None:
    """Print a model comparison table when multiple models are detected."""
    if not sorted_results:
        return

    # Use TestStatistics for all calculations
    stats = TestStatistics(sorted_results)

    # Create comparison table
    comparison_table = Table(
        title="\n📊 MODEL COMPARISON RESULTS",
        show_header=True,
        header_style="bold cyan",
        show_lines=True,
        padding=(0, 1),
    )

    # Add columns
    comparison_table.add_column("Test Case", style="bright_blue", width=30)
    for model in stats.models:
        comparison_table.add_column(model, justify="center", width=22)

    # Process each test case
    for test_case in stats.test_cases:
        row_data = [test_case]

        # Get time bounds for coloring this row
        min_time, max_time = stats.get_test_time_range(test_case)

        for model in stats.models:
            test_stats = stats.get_stats(test_case, model)

            if not test_stats:
                row_data.append("—")
                continue

            # Determine display based on status
            if test_stats["skipped"] == test_stats["runs"]:
                cell_text = "[cyan]Skipped[/cyan]"
            elif test_stats["setup_failures"] == test_stats["runs"]:
                cell_text = "[magenta]Setup Fail[/magenta]"
            else:
                # Choose score color based on pass percentage
                pass_pct = test_stats["pass_rate"]
                score_color = _get_pass_color(pass_pct)

                # Format as 3 lines: Score, Pass count, Avg time
                cell_lines = []
                cell_lines.append(
                    f"Score: [{score_color}]{pass_pct:.0f}%[/{score_color}]"
                )
                cell_lines.append(
                    f"Pass:  {test_stats['passed']}/{test_stats['valid_runs']}"
                )

                # Color time based on best/worst in row
                if test_stats["avg_time"]:
                    time_str = f"{test_stats['avg_time']:.1f}s"
                    if min_time is not None and max_time is not None:
                        time_color = _get_time_color(
                            test_stats["avg_time"], min_time, max_time
                        )
                        if time_color:
                            cell_lines.append(
                                f"Avg:   [{time_color}]{time_str}[/{time_color}]"
                            )
                        else:
                            cell_lines.append(f"Avg:   {time_str}")
                    else:
                        cell_lines.append(f"Avg:   {time_str}")

                cell_text = "\n".join(cell_lines)

            row_data.append(cell_text)

        comparison_table.add_row(*row_data)

    # Add separator
    separator_row = _create_separator_row([28] + [18] * len(stats.models))
    comparison_table.add_row(*separator_row, style="dim")

    # Add model average row
    average_row = ["Model Average"]
    model_costs = {}  # Track costs for comparison logging
    for model in stats.models:
        summary = stats.get_model_summary(model)
        if summary["runs"] > 0 and summary["pass_rate"] is not None:
            pass_pct = summary["pass_rate"]
            # Choose color based on pass percentage
            color = _get_pass_color(pass_pct, bold=True)
            average_row.append(f"[{color}]{pass_pct:.1f}%[/{color}]")

            # Track model costs if available
            if "total_cost" in summary:
                model_costs[model] = summary["total_cost"]
        else:
            average_row.append("—")
    comparison_table.add_row(*average_row)

    # Add average time row with coloring
    avg_times_dict = stats.get_model_times("avg")
    time_row = ["Average Time"] + _format_colored_times(avg_times_dict)
    comparison_table.add_row(*time_row)

    # Add P90 time row with coloring
    p90_times_dict = stats.get_model_times("p90")
    p90_row = ["P90 Time"] + _format_colored_times(p90_times_dict)
    comparison_table.add_row(*p90_row)

    # Add Total Cost row with coloring
    costs_dict = stats.get_model_costs()
    cost_row = ["Total Cost"] + _format_colored_costs(costs_dict)
    comparison_table.add_row(*cost_row)

    console.print(comparison_table)

    # Find best performing model(s)
    best_models = []
    best_pass_pct = 0.0
    for model in stats.models:
        summary = stats.get_model_summary(model)
        pass_pct = summary["pass_rate"]
        if summary["runs"] > 0:
            if pass_pct > best_pass_pct:
                best_pass_pct = pass_pct
                best_models = [model]
            elif pass_pct == best_pass_pct:
                best_models.append(model)

    if best_models:
        if len(best_models) == 1:
            console.print(
                f"[cyan]Best performing model: {best_models[0]} ({best_pass_pct:.1f}% pass rate)[/cyan]"
            )
        else:
            models_str = ", ".join(best_models)
            console.print(
                f"[cyan]Best performing models: {models_str} ({best_pass_pct:.1f}% pass rate)[/cyan]"
            )

    # Calculate and print total evaluation cost
    total_cost = sum(model_costs.values()) if model_costs else 0
    if total_cost > 0:
        # Count total number of test runs (not unique test cases)
        total_test_runs = len(sorted_results)
        avg_cost_per_test = total_cost / total_test_runs if total_test_runs else 0
        console.print(
            f"[cyan]Total evaluation cost: ${total_cost:.4f}, Average per test: ${avg_cost_per_test:.6f}[/cyan]"
        )

    # Print cost comparison if available (after total cost)
    if model_costs and len(model_costs) >= 2:
        sorted_costs = sorted(model_costs.items(), key=lambda x: x[1])
        cheapest_model, cheapest_cost = sorted_costs[0]
        most_expensive_model, most_expensive_cost = sorted_costs[-1]

        if cheapest_cost > 0:
            diff_pct = (most_expensive_cost / cheapest_cost - 1) * 100
            console.print(
                f"[cyan]Cost comparison - Cheapest: {cheapest_model} (${cheapest_cost:.4f}) vs Most expensive: {most_expensive_model} (${most_expensive_cost:.4f}) - Difference: {diff_pct:+.1f}%[/cyan]"
            )


def _print_tag_performance_table(sorted_results: List[dict], console: Console) -> None:
    """Print a performance table organized by eval tags."""
    if not sorted_results:
        return

    # Use TestStatistics for all calculations
    stats = TestStatistics(sorted_results)

    # Get unique tags
    tags = stats.get_unique_tags()
    if not tags:
        return  # No tags to display

    # Create tag performance table
    tag_table = Table(
        title="\n📏 PERFORMANCE BY EVAL TAG",
        show_header=True,
        header_style="bold magenta",
        show_lines=True,
        padding=(0, 1),
    )

    # Add columns
    tag_table.add_column("Tag", style="cyan", width=20)
    for model in stats.models:
        tag_table.add_column(model, justify="center", width=18)

    # Process each tag
    overall_by_model = {model: {"passed": 0, "total": 0} for model in stats.models}

    for tag in tags:
        row_data = [tag]

        for model in stats.models:
            tag_stats = stats.get_tag_stats(tag, model)

            if not tag_stats or tag_stats["valid_runs"] == 0:
                row_data.append("—")
            else:
                # Track overall stats
                overall_by_model[model]["passed"] += tag_stats["passed"]
                overall_by_model[model]["total"] += tag_stats["valid_runs"]

                # Format cell with pass percentage and count
                pass_pct = tag_stats["pass_rate"]

                # Choose color based on pass percentage
                color = _get_pass_color(pass_pct)

                cell_text = f"[{color}]{pass_pct:.0f}%[/{color}] ({tag_stats['passed']}/{tag_stats['valid_runs']})"
                row_data.append(cell_text)

        tag_table.add_row(*row_data)

    # Add separator
    separator_row = _create_separator_row([18] + [16] * len(stats.models))
    tag_table.add_row(*separator_row, style="dim")

    # Add unique tests row (counting each test only once, regardless of tags)
    unique_row = ["[bold]Unique Tests[/bold]"]
    for model in stats.models:
        # Get unique test counts from model summary
        model_summary = stats.get_model_summary(model)
        if model_summary["runs"] > 0:
            unique_passed = model_summary["passed"]
            unique_total = model_summary["valid_runs"]
            unique_pct = model_summary["pass_rate"]

            # Choose color based on percentage
            color = _get_pass_color(unique_pct, bold=True)

            unique_row.append(
                f"[{color}]{unique_pct:.0f}%[/{color}] ({unique_passed}/{unique_total})"
            )
        else:
            unique_row.append("—")

    tag_table.add_row(*unique_row)

    console.print(tag_table)


def _print_model_comparison_if_multiple(
    sorted_results: List[dict], console: Console
) -> None:
    """Print model comparison and tag performance tables."""
    if not sorted_results:
        return

    _print_model_comparison_table(sorted_results, console)
    _print_tag_performance_table(sorted_results, console)


def _print_summary_statistics(sorted_results: List[dict], console: Console) -> None:
    """Print a summary statistics table similar to pytest coverage reports."""
    if not sorted_results:
        return

    # Check if any tests had retries
    tests_with_retries = 0
    total_retry_time = 0
    for result in sorted_results:
        if result.get("had_retries", False):
            tests_with_retries += 1
            total_retry_time += result.get("retry_wait_time", 0)

    # Display retry warning prominently if any retries occurred
    if tests_with_retries > 0:
        console.print(
            f"\n[bold yellow]⚠️  API THROTTLING DETECTED[/bold yellow]\n"
            f"[yellow]{tests_with_retries} test(s) required retries due to API rate limiting/overload[/yellow]\n"
            f"[yellow]Total time spent waiting for retries: {total_retry_time}s ({total_retry_time // 60}m {total_retry_time % 60}s)[/yellow]\n"
            f"[dim]Use --no-retry-on-throttle to disable automatic retries[/dim]\n"
        )

    # Group results by test name (without iteration number)
    test_groups: Dict[str, List[dict]] = defaultdict(list)

    for result in sorted_results:
        # Use test_case_name from result dict
        base_name = result["test_case_name"]
        test_groups[base_name].append(result)

    # Create summary table
    summary_table = Table(
        title="\n📊 TEST SUMMARY STATISTICS",
        show_header=True,
        header_style="bold cyan",
        show_lines=False,
        padding=(0, 1),
    )

    # Add columns (removed Skip column, added failure breakdown)
    summary_table.add_column("Test Name", style="bright_blue", width=40)
    summary_table.add_column("Runs", justify="center", width=6)
    summary_table.add_column("Pass", justify="center", style="green", width=6)
    summary_table.add_column("Fail", justify="center", style="red", width=6)
    summary_table.add_column("Setup Fail", justify="center", style="magenta", width=10)
    summary_table.add_column("Mock Fail", justify="center", style="yellow", width=10)
    summary_table.add_column("Pass %", justify="right", width=8)
    summary_table.add_column("Avg Time", justify="right", width=10)
    summary_table.add_column("P90 Time", justify="right", width=10)

    # Calculate statistics for each test
    total_runs = 0
    total_pass = 0
    total_fail = 0
    total_setup_fail = 0
    total_mock_fail = 0
    total_skip = 0
    total_throttled = 0

    for test_name in sorted(test_groups.keys()):
        results = test_groups[test_name]

        # Skip entirely skipped tests
        if all(r.get("status") == "skipped" for r in results):
            total_skip += len(results)
            total_runs += len(results)  # Count skipped tests in total runs
            continue

        runs = count_results(results, ResultType.ALL)
        passed = count_results(results, ResultType.PASSED)

        # Count different failure types
        setup_failures = count_results(results, ResultType.SETUP_FAILED)
        mock_failures = count_results(results, ResultType.MOCK_FAILED)
        other_failures = count_results(results, ResultType.FAILED)

        # Determine pass percentage display
        all_skipped = all(r.get("status") == "skipped" for r in results)
        all_setup_fail = setup_failures == runs
        throttled = count_results(results, ResultType.THROTTLED)
        all_throttled = throttled == runs

        # Calculate pass percentage from valid runs
        valid_runs = count_results(results, ResultType.VALID_RUNS)
        pass_pct = _calculate_pass_percentage(passed, valid_runs)

        # Calculate average and P90 execution time
        times = [
            r.get("holmes_duration", 0) for r in results if r.get("holmes_duration")
        ]
        avg_time = sum(times) / len(times) if times else 0
        p90_time = _calculate_p90(times)

        # Update totals
        total_runs += runs
        total_pass += passed
        total_fail += other_failures
        total_setup_fail += setup_failures
        total_mock_fail += mock_failures
        total_throttled += throttled

        # Format pass percentage with color (no emoji)
        if all_throttled:
            pass_pct_str = "[red]Throttled[/red]"
        elif all_skipped:
            pass_pct_str = "[cyan]Skipped[/cyan]"
        elif all_setup_fail:
            pass_pct_str = "[magenta]Setup Fail[/magenta]"
        else:
            indicators = _get_failure_indicators(mock_failures, setup_failures, runs)
            pass_pct_str = f"{_format_pass_percentage(pass_pct)}{indicators}"

        avg_time_str = _format_time(avg_time)
        p90_time_str = _format_time(p90_time)

        # Add row
        summary_table.add_row(
            test_name,
            str(runs),
            str(passed) if passed > 0 else "-",
            str(other_failures) if other_failures > 0 else "-",
            str(setup_failures) if setup_failures > 0 else "-",
            str(mock_failures) if mock_failures > 0 else "-",
            pass_pct_str,
            avg_time_str,
            p90_time_str,
        )

    # Add separator
    separator_row = _create_separator_row([38, 4, 4, 4, 8, 8, 6, 8, 8])
    summary_table.add_row(*separator_row, style="dim")

    # Add totals row
    # Calculate valid runs (exclude skipped, setup-failed, and throttled runs)
    total_valid_runs = total_runs - total_skip - total_setup_fail - total_throttled
    total_pass_pct = _calculate_pass_percentage(total_pass, total_valid_runs)

    # Format total pass percentage with color (no emoji)
    # For totals, always show indicators if any failures exist
    total_indicators = ""
    if total_mock_fail > 0:
        total_indicators += " 📦"
    if total_setup_fail > 0:
        total_indicators += " 🔧"

    # Use bold color for total row
    total_color = _get_pass_color(total_pass_pct, bold=True)

    total_pass_pct_str = f"[{total_color}]{total_pass_pct:.1f}% ({total_pass}/{total_valid_runs})[/{total_color}]{total_indicators}"

    summary_table.add_row(
        "TOTAL",
        str(total_runs),
        str(total_pass),
        str(total_fail),
        str(total_setup_fail),
        str(total_mock_fail),
        total_pass_pct_str,
        "",
        "",
        style="bold",
    )

    console.print(summary_table)

    # Print quick summary line with failure breakdown
    if total_fail > 0 or total_setup_fail > 0 or total_mock_fail > 0:
        failure_parts = []
        if total_fail > 0:
            failure_parts.append(f"{total_fail} real failures")
        if total_setup_fail > 0:
            failure_parts.append(f"{total_setup_fail} setup failures")
        if total_mock_fail > 0:
            failure_parts.append(f"{total_mock_fail} mock data failures")

        total_failures = total_fail + total_setup_fail + total_mock_fail
        console.print(
            f"\n[bold red]FAIL[/bold red] {total_failures} out of {total_valid_runs} tests failed ({', '.join(failure_parts)})"
        )
    else:
        console.print(
            f"\n[bold green]SUCCESS[/bold green] All {total_valid_runs} tests passed!"
        )

    # Print skip info if any
    if total_skip > 0:
        console.print(
            f"[dim]Note: {total_skip // max(len(test_groups.get(name, [])) for name in test_groups)} tests were skipped entirely[/dim]"
        )

    # Print iteration info if multiple runs detected
    max_iterations = max(len(results) for results in test_groups.values())
    if max_iterations > 1:
        # TODO this is wrong - in case you run with 1 Iterations it says '2' instead of '1'
        console.print(
            f"[dim]Note: Tests were run with {max_iterations} iterations[/dim]"
        )

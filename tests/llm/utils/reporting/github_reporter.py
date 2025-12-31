"""GitHub Actions reporting functionality."""

import logging
import os
from typing import Dict, List, Optional, Tuple

from tests.llm.utils.test_results import TestStatus
from tests.llm.utils.braintrust import get_braintrust_url
from tests.llm.utils.braintrust_history import (
    HistoricalComparison,
    HistoricalComparisonDetails,
    HistoricalMetrics,
    compare_with_historical,
    get_historical_metrics,
)


def _build_comparison_map(
    sorted_results: List[dict],
    historical: Dict[str, HistoricalMetrics],
) -> Dict[str, HistoricalComparison]:
    """Build a map of test_id:model to HistoricalComparison.

    Args:
        sorted_results: List of test result dictionaries
        historical: Historical metrics from Braintrust

    Returns:
        Dictionary mapping "test_id:model" to HistoricalComparison
    """
    comparisons = compare_with_historical(sorted_results, historical)
    return {f"{c.test_id}:{c.model}": c for c in comparisons}


def _format_time_with_comparison(
    exec_time: Optional[float],
    comparison: Optional[HistoricalComparison],
) -> str:
    """Format execution time with optional historical comparison indicator.

    Args:
        exec_time: Current execution time in seconds
        comparison: Historical comparison data (optional)

    Returns:
        Formatted string like "12.3s" or "12.3s ↑15%" with comparison
    """
    if not exec_time or exec_time <= 0:
        return "—"

    base = f"{exec_time:.1f}s"

    # Add comparison indicator if we have enough historical data
    if comparison and comparison.duration_diff_pct is not None and comparison.sample_count >= 3:
        diff = comparison.duration_diff_pct
        if abs(diff) >= 10:  # Only show if difference is >= 10%
            if diff > 0:
                return f"{base} ↑{diff:.0f}%"  # Slower
            else:
                return f"{base} ↓{abs(diff):.0f}%"  # Faster

    return base


def _format_cost_with_comparison(
    cost: Optional[float],
    comparison: Optional[HistoricalComparison],
) -> str:
    """Format cost with optional historical comparison indicator.

    Args:
        cost: Current cost in dollars
        comparison: Historical comparison data (optional)

    Returns:
        Formatted string like "$0.0234" or "$0.0234 ↑10%" with comparison
    """
    if not cost or cost <= 0:
        return "—"

    base = f"${cost:.4f}"

    # Add comparison indicator if we have enough historical data
    if comparison and comparison.cost_diff_pct is not None and comparison.sample_count >= 3:
        diff = comparison.cost_diff_pct
        if abs(diff) >= 10:  # Only show if difference is >= 10%
            if diff > 0:
                return f"{base} ↑{diff:.0f}%"  # More expensive
            else:
                return f"{base} ↓{abs(diff):.0f}%"  # Cheaper

    return base


def _generate_historical_details_section(details: HistoricalComparisonDetails) -> str:
    """Generate a collapsible details section for historical comparison transparency.

    Args:
        details: HistoricalComparisonDetails with experiment info

    Returns:
        Markdown string with collapsible details section
    """
    lines = ["\n<details>", "<summary>Historical Comparison Details</summary>\n"]

    # Filter description
    if details.filter_description:
        lines.append(f"**Filter:** {details.filter_description}\n")

    # Status
    if details.status:
        lines.append(f"**Status:** {details.status}\n")
    else:
        lines.append(f"**Status:** Success - {details.metrics_count} test/model combinations loaded\n")

    # Experiments used
    if details.experiments:
        lines.append(f"\n**Experiments compared ({len(details.experiments)}):**\n")
        for exp in details.experiments:
            # Build Braintrust URL for the experiment
            exp_url = f"https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/{exp.id}"
            branch_info = f" (branch: `{exp.branch}`)" if exp.branch else ""
            lines.append(f"- [{exp.name}]({exp_url}){branch_info}")
        lines.append("")

    # Errors
    if details.errors:
        lines.append("\n**Errors:**\n")
        lines.append("```")
        for error in details.errors:
            lines.append(error)
        lines.append("```\n")

    lines.append("</details>\n")
    return "\n".join(lines)


def handle_github_output(sorted_results: List[dict]) -> None:
    """Generate and write GitHub Actions report files."""
    # Generate markdown report
    markdown, _, total_regressions = generate_markdown_report(sorted_results)

    # Always write markdown report
    with open("evals_report.md", "w", encoding="utf-8") as file:
        file.write(markdown)

    if os.environ.get("GENERATE_REGRESSIONS_FILE") and total_regressions > 0:
        with open("regressions.txt", "w", encoding="utf-8") as file:
            file.write(f"{total_regressions}")


def generate_markdown_report(
    sorted_results: List[dict],
    include_historical: bool = True,
) -> Tuple[str, List[dict], int]:
    """Generate markdown report from sorted test results.

    Args:
        sorted_results: List of test result dictionaries
        include_historical: Whether to fetch and include historical comparison

    Returns:
        Tuple of (markdown, sorted_results, total_regressions)
    """
    markdown = "## Results of HolmesGPT evals\n\n"

    # Fetch historical metrics for comparison (only for passing tests)
    historical: Dict[str, HistoricalMetrics] = {}
    comparison_map: Dict[str, HistoricalComparison] = {}
    historical_details: Optional[HistoricalComparisonDetails] = None
    if include_historical:
        try:
            historical, historical_details = get_historical_metrics(limit=10)
            if historical:
                comparison_map = _build_comparison_map(sorted_results, historical)
                logging.info(f"Loaded historical data for {len(historical)} test/model combinations")
        except Exception as e:
            historical_details = HistoricalComparisonDetails(status=f"API error: {e}")
            logging.warning(f"Failed to fetch historical metrics: {e}")

    # Count results by test type and status
    ask_holmes_total = 0
    ask_holmes_passed = 0
    ask_holmes_regressions = 0
    ask_holmes_mock_failures = 0
    ask_holmes_skipped = 0
    ask_holmes_setup_failures = 0

    investigate_total = 0
    investigate_passed = 0
    investigate_regressions = 0
    investigate_mock_failures = 0
    investigate_skipped = 0
    investigate_setup_failures = 0

    workload_health_total = 0
    workload_health_passed = 0
    workload_health_regressions = 0
    workload_health_mock_failures = 0
    workload_health_skipped = 0
    workload_health_setup_failures = 0

    for result in sorted_results:
        status = TestStatus(result)

        if result["test_type"] == "ask":
            ask_holmes_total += 1
            if status.is_skipped:
                ask_holmes_skipped += 1
            elif status.is_setup_failure:
                ask_holmes_setup_failures += 1
            elif status.passed:
                ask_holmes_passed += 1
            elif status.is_regression:
                ask_holmes_regressions += 1
            elif status.is_mock_failure:
                ask_holmes_mock_failures += 1
        elif result["test_type"] == "investigate":
            investigate_total += 1
            if status.is_skipped:
                investigate_skipped += 1
            elif status.is_setup_failure:
                investigate_setup_failures += 1
            elif status.passed:
                investigate_passed += 1
            elif status.is_regression:
                investigate_regressions += 1
            elif status.is_mock_failure:
                investigate_mock_failures += 1
        elif result["test_type"] == "workload_health":
            workload_health_total += 1
            if status.is_skipped:
                workload_health_skipped += 1
            elif status.is_setup_failure:
                workload_health_setup_failures += 1
            elif status.passed:
                workload_health_passed += 1
            elif status.is_regression:
                workload_health_regressions += 1
            elif status.is_mock_failure:
                workload_health_mock_failures += 1

    # Generate summary lines
    if ask_holmes_total > 0:
        markdown += f"- ask_holmes: {ask_holmes_passed}/{ask_holmes_total} test cases were successful, {ask_holmes_regressions} regressions"
        if ask_holmes_skipped > 0:
            markdown += f", {ask_holmes_skipped} skipped"
        if ask_holmes_setup_failures > 0:
            markdown += f", {ask_holmes_setup_failures} setup failures"
        if ask_holmes_mock_failures > 0:
            markdown += f", {ask_holmes_mock_failures} mock failures"
        markdown += "\n"
    if investigate_total > 0:
        markdown += f"- investigate: {investigate_passed}/{investigate_total} test cases were successful, {investigate_regressions} regressions"
        if investigate_skipped > 0:
            markdown += f", {investigate_skipped} skipped"
        if investigate_setup_failures > 0:
            markdown += f", {investigate_setup_failures} setup failures"
        if investigate_mock_failures > 0:
            markdown += f", {investigate_mock_failures} mock failures"
        markdown += "\n"
    if workload_health_total > 0:
        markdown += f"- workload_health: {workload_health_passed}/{workload_health_total} test cases were successful, {workload_health_regressions} regressions"
        if workload_health_skipped > 0:
            markdown += f", {workload_health_skipped} skipped"
        if workload_health_setup_failures > 0:
            markdown += f", {workload_health_setup_failures} setup failures"
        if workload_health_mock_failures > 0:
            markdown += f", {workload_health_mock_failures} mock failures"
        markdown += "\n"

    # Generate detailed table
    markdown += "\n\n| Status | Test case | Time | Turns | Tools | Cost |\n"
    markdown += "| --- | --- | --- | --- | --- | --- |\n"

    # Track totals for summary row
    total_time = 0.0
    total_cost = 0.0
    total_turns = 0
    total_tools = 0
    time_count = 0
    turns_count = 0
    tools_count = 0

    for result in sorted_results:
        test_case_name = result["test_case_name"]
        model = result.get("model", "")

        braintrust_url = get_braintrust_url(
            result.get("braintrust_span_id"),
            result.get("braintrust_root_span_id"),
        )
        if braintrust_url:
            test_case_name = f"[{test_case_name}]({braintrust_url})"

        status = TestStatus(result)

        # Get historical comparison for this test/model
        comparison_key = f"{result.get('test_case_name', '')}:{model}"
        comparison = comparison_map.get(comparison_key)

        # Format time with historical comparison
        exec_time = result.get("holmes_duration")
        time_str = _format_time_with_comparison(exec_time, comparison)
        if exec_time and exec_time > 0:
            total_time += exec_time
            time_count += 1

        # Format turns (LLM calls)
        num_llm_calls = result.get("num_llm_calls")
        if num_llm_calls and num_llm_calls > 0:
            turns_str = str(num_llm_calls)
            total_turns += num_llm_calls
            turns_count += 1
        else:
            turns_str = "—"

        # Format tool calls
        tool_call_count = result.get("tool_call_count")
        if tool_call_count and tool_call_count > 0:
            tools_str = str(tool_call_count)
            total_tools += tool_call_count
            tools_count += 1
        else:
            tools_str = "—"

        # Format cost with historical comparison
        cost = result.get("cost", 0)
        cost_str = _format_cost_with_comparison(cost, comparison)
        if cost and cost > 0:
            total_cost += cost

        markdown += f"| {status.markdown_symbol} | {test_case_name} | {time_str} | {turns_str} | {tools_str} | {cost_str} |\n"

    # Add summary row
    avg_time_str = f"{total_time / time_count:.1f}s" if time_count > 0 else "—"
    avg_turns_str = f"{total_turns / turns_count:.1f}" if turns_count > 0 else "—"
    avg_tools_str = f"{total_tools / tools_count:.1f}" if tools_count > 0 else "—"
    total_cost_str = f"${total_cost:.4f}" if total_cost > 0 else "—"
    markdown += f"| | **Total** | **{avg_time_str}** avg | **{avg_turns_str}** avg | **{avg_tools_str}** avg | **{total_cost_str}** |\n"

    # Add footer explaining historical comparison status
    if historical and comparison_map:
        markdown += f"\n_Time/Cost columns show comparison with last 10 runs from other branches (↑slower/costlier, ↓faster/cheaper). Based on {len(historical)} test/model combinations._\n"
    elif historical_details and historical_details.status:
        markdown += f"\n_Historical comparison unavailable: {historical_details.status}_\n"

    # Add collapsible details section for historical comparison transparency
    if historical_details:
        markdown += _generate_historical_details_section(historical_details)

    return (
        markdown,
        sorted_results,
        ask_holmes_regressions + investigate_regressions + workload_health_regressions,
    )

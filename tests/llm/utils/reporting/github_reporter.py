"""GitHub Actions reporting functionality."""

import os
from typing import List, Tuple

from tests.llm.utils.test_results import TestStatus
from tests.llm.utils.braintrust import get_braintrust_url


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


def generate_markdown_report(sorted_results: List[dict]) -> Tuple[str, List[dict], int]:
    """Generate markdown report from sorted test results."""
    markdown = "## Results of HolmesGPT evals\n\n"

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

        braintrust_url = get_braintrust_url(
            result.get("braintrust_span_id"),
            result.get("braintrust_root_span_id"),
        )
        if braintrust_url:
            test_case_name = f"[{test_case_name}]({braintrust_url})"

        status = TestStatus(result)

        # Format time (use holmes_duration for pure agent time)
        exec_time = result.get("holmes_duration")
        if exec_time and exec_time > 0:
            time_str = f"{exec_time:.1f}s"
            total_time += exec_time
            time_count += 1
        else:
            time_str = "—"

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

        # Format cost
        cost = result.get("cost", 0)
        if cost and cost > 0:
            cost_str = f"${cost:.4f}"
            total_cost += cost
        else:
            cost_str = "—"

        markdown += f"| {status.markdown_symbol} | {test_case_name} | {time_str} | {turns_str} | {tools_str} | {cost_str} |\n"

    # Add summary row
    avg_time_str = f"{total_time / time_count:.1f}s" if time_count > 0 else "—"
    avg_turns_str = f"{total_turns / turns_count:.1f}" if turns_count > 0 else "—"
    avg_tools_str = f"{total_tools / tools_count:.1f}" if tools_count > 0 else "—"
    total_cost_str = f"${total_cost:.4f}" if total_cost > 0 else "—"
    markdown += f"| | **Total** | **{avg_time_str}** avg | **{avg_turns_str}** avg | **{avg_tools_str}** avg | **{total_cost_str}** |\n"

    return (
        markdown,
        sorted_results,
        ask_holmes_regressions + investigate_regressions + workload_health_regressions,
    )

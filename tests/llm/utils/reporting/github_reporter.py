"""GitHub Actions reporting functionality."""

import logging
import os
from typing import Dict, List, Optional, Tuple

from tests.llm.utils.braintrust import get_braintrust_url
from tests.llm.utils.braintrust_history import (
    BRAINTRUST_ORG,
    BRAINTRUST_PROJECT,
    BenchmarkMetrics,
    HistoricalComparison,
    HistoricalComparisonDetails,
    compare_with_benchmark,
    get_benchmark_baseline,
)
from tests.llm.utils.test_results import TestStatus


def _fmt_tokens(value: Optional[int]) -> str:
    """Format a token count: comma-separated if present, dash if absent/zero."""
    if value is not None and value > 0:
        return f"{value:,}"
    return "—"


def _format_diff_pct(diff: Optional[float]) -> str:
    """Format a diff percentage with arrow indicator, bold if >25%."""
    if diff is None:
        return "—"
    if abs(diff) < 10:
        return "±0%"
    bold = abs(diff) > 25
    arrow = "↑" if diff > 0 else "↓"
    indicator = f"{arrow}{abs(diff):.0f}%"
    return f"**{indicator}**" if bold else indicator


def _calc_diff_pct(current: Optional[float], baseline: Optional[float]) -> Optional[float]:
    """Calculate percentage difference: positive = current is higher."""
    if not current or not baseline or baseline == 0:
        return None
    return (current - baseline) / baseline * 100


def _generate_comparison_tables(
    sorted_results: List[dict],
    comparison_map: Dict[str, HistoricalComparison],
    benchmark: Dict[str, BenchmarkMetrics],
) -> str:
    """Generate separate comparison tables for time, cost, tokens, and cached tokens.

    Each table has columns: Test case | This branch | master | Diff
    """
    lines: List[str] = []

    # Build rows with data for all metrics
    rows: List[dict] = []
    for result in sorted_results:
        test_name = result.get("test_case_name", "")
        model = result.get("model", "")
        key = f"{test_name}:{model}"
        comparison = comparison_map.get(key)
        baseline = benchmark.get(key)

        rows.append({
            "name": f"{test_name} ({model})" if model else test_name,
            "current_time": result.get("holmes_duration"),
            "baseline_time": baseline.duration if baseline else None,
            "current_cost": result.get("cost"),
            "baseline_cost": baseline.cost if baseline else None,
            "current_total_tokens": result.get("total_tokens", 0) or 0,
            "baseline_total_tokens": baseline.total_tokens if baseline else None,
            "current_cached_tokens": result.get("cached_tokens"),
            "baseline_cached_tokens": baseline.cached_tokens if baseline else None,
        })

    # --- Time comparison table ---
    has_time_data = any(r["baseline_time"] is not None for r in rows)
    if has_time_data:
        lines.append("\n**Time comparison (seconds):**\n")
        lines.append("| Test case | This branch | master | Diff |")
        lines.append("| --- | --- | --- | --- |")
        for r in rows:
            cur = f"{r['current_time']:.1f}s" if r["current_time"] else "—"
            base = f"{r['baseline_time']:.1f}s" if r["baseline_time"] else "—"
            diff = _format_diff_pct(_calc_diff_pct(r["current_time"], r["baseline_time"]))
            lines.append(f"| {r['name']} | {cur} | {base} | {diff} |")
        lines.append("")

    # --- Cost comparison table ---
    has_cost_data = any(r["baseline_cost"] is not None for r in rows)
    if has_cost_data:
        lines.append("\n**Cost comparison:**\n")
        lines.append("| Test case | This branch | master | Diff |")
        lines.append("| --- | --- | --- | --- |")
        for r in rows:
            cur = f"${r['current_cost']:.4f}" if r["current_cost"] else "—"
            base = f"${r['baseline_cost']:.4f}" if r["baseline_cost"] else "—"
            diff = _format_diff_pct(_calc_diff_pct(r["current_cost"], r["baseline_cost"]))
            lines.append(f"| {r['name']} | {cur} | {base} | {diff} |")
        lines.append("")

    # --- Total tokens comparison table ---
    has_token_data = any(r["baseline_total_tokens"] is not None for r in rows)
    if has_token_data:
        lines.append("\n**Total tokens comparison:**\n")
        lines.append("| Test case | This branch | master | Diff |")
        lines.append("| --- | --- | --- | --- |")
        for r in rows:
            cur_val = r["current_total_tokens"]
            cur = f"{cur_val:,}" if cur_val else "—"
            base_val = r["baseline_total_tokens"]
            base = f"{base_val:,}" if base_val else "—"
            diff = _format_diff_pct(
                _calc_diff_pct(
                    float(cur_val) if cur_val else None,
                    float(base_val) if base_val else None,
                )
            )
            lines.append(f"| {r['name']} | {cur} | {base} | {diff} |")
        lines.append("")

    # --- Cached tokens comparison table ---
    has_cached_data = any(r["baseline_cached_tokens"] is not None for r in rows)
    if has_cached_data:
        lines.append("\n**Cached tokens comparison:**\n")
        lines.append("| Test case | This branch | master | Diff |")
        lines.append("| --- | --- | --- | --- |")
        for r in rows:
            cur_val = r["current_cached_tokens"]
            cur = f"{cur_val:,}" if cur_val is not None else "—"
            base_val = r["baseline_cached_tokens"]
            base = f"{base_val:,}" if base_val is not None else "—"
            diff = _format_diff_pct(
                _calc_diff_pct(
                    float(cur_val) if cur_val is not None else None,
                    float(base_val) if base_val is not None else None,
                )
            )
            lines.append(f"| {r['name']} | {cur} | {base} | {diff} |")
        lines.append("")

    if not lines:
        lines.append("\n_No benchmark data available for comparison._\n")

    # Note missing tables
    missing = []
    if not has_cost_data:
        missing.append("cost")
    if not has_token_data:
        missing.append("total tokens")
    if not has_cached_data:
        missing.append("cached tokens")
    if missing:
        lines.append(
            f"_Benchmark has no {', '.join(missing)} data. "
            "Will appear after the next weekly benchmark run._\n"
        )

    return "\n".join(lines)


def _generate_historical_details_section(
    details: HistoricalComparisonDetails,
    sorted_results: Optional[List[dict]] = None,
    comparison_map: Optional[Dict[str, HistoricalComparison]] = None,
    benchmark: Optional[Dict[str, BenchmarkMetrics]] = None,
) -> str:
    """Generate a collapsible details section with benchmark comparison tables.

    Args:
        details: HistoricalComparisonDetails with experiment info
        sorted_results: Current test results for comparison tables
        comparison_map: Map of test:model to comparison data
        benchmark: Map of test:model to benchmark metrics

    Returns:
        Markdown string with collapsible details section
    """
    lines = ["<details>", "<summary><b>Benchmark Comparison Details</b></summary>\n"]

    # Filter description
    if details.filter_description:
        lines.append(f"**Baseline:** {details.filter_description}\n")

    # Status
    if details.status:
        lines.append(f"**Status:** {details.status}\n")
    else:
        lines.append(
            f"**Status:** Success - {details.metrics_count} test/model combinations loaded\n"
        )

    # Experiments used
    if details.experiments:
        lines.append(f"\n**Benchmark experiment{'s' if len(details.experiments) > 1 else ''}:**\n")
        for exp in details.experiments:
            exp_url = f"https://www.braintrust.dev/app/{BRAINTRUST_ORG}/p/{BRAINTRUST_PROJECT}/experiments/{exp.id}"
            created_info = f" (created: {exp.created[:10]})" if exp.created else ""
            lines.append(f"- [{exp.name}]({exp_url}){created_info}")
        lines.append("")

    # Comparison tables
    if sorted_results and comparison_map and benchmark:
        lines.append(
            _generate_comparison_tables(sorted_results, comparison_map, benchmark)
        )

    # Errors
    if details.errors:
        lines.append("\n**Errors:**\n")
        lines.append("```")
        for error in details.errors:
            lines.append(error)
        lines.append("```\n")

    # Document comparison thresholds
    lines.append("**Comparison indicators:**")
    lines.append("- `±0%` — diff under 10% (within noise threshold)")
    lines.append("- `↑N%`/`↓N%` — diff 10-25%")
    lines.append("- **`↑N%`**/**`↓N%`** — diff over 25% (significant)\n")

    lines.append("</details>\n")
    return "\n".join(lines)


def handle_github_output(sorted_results: List[dict]) -> None:
    """Generate and write GitHub Actions report files."""
    # Generate markdown report (always compare against weekly benchmark when possible)
    markdown, _, total_regressions = generate_markdown_report(sorted_results, True)

    # Always write markdown report
    with open("evals_report.md", "w", encoding="utf-8") as file:
        file.write(markdown)

    if os.environ.get("GENERATE_REGRESSIONS_FILE") and total_regressions > 0:
        with open("regressions.txt", "w", encoding="utf-8") as file:
            file.write(f"{total_regressions}")


def generate_markdown_report(
    sorted_results: List[dict],
    include_historical: bool,
) -> Tuple[str, List[dict], int]:
    """Generate markdown report from sorted test results.

    Args:
        sorted_results: List of test result dictionaries
        include_historical: Whether to fetch and include historical comparison

    Returns:
        Tuple of (markdown, sorted_results, total_regressions)
    """
    # Check if running on a specific branch (for cross-branch comparison)
    eval_branch = os.environ.get("EVAL_BRANCH", "")
    if eval_branch:
        markdown = f"## Results of HolmesGPT evals (branch: `{eval_branch}`)\n\n"
    else:
        markdown = "## Results of HolmesGPT evals\n\n"

    # Fetch benchmark baseline for comparison (latest weekly ci-benchmark run)
    benchmark: Dict[str, BenchmarkMetrics] = {}
    comparison_map: Dict[str, HistoricalComparison] = {}
    historical_details: Optional[HistoricalComparisonDetails] = None
    if include_historical:
        try:
            benchmark, historical_details = get_benchmark_baseline()
            if benchmark:
                comparison_map = compare_with_benchmark(sorted_results, benchmark)
                logging.info(
                    f"Loaded benchmark baseline: {len(benchmark)} test/model combinations"
                )
        except Exception as e:
            historical_details = HistoricalComparisonDetails(status=f"API error: {e}")
            logging.warning(f"Failed to fetch benchmark baseline: {e}")

    # Count results by test type and status
    ask_holmes_total = 0
    ask_holmes_passed = 0
    ask_holmes_regressions = 0
    ask_holmes_mock_failures = 0
    ask_holmes_skipped = 0
    ask_holmes_setup_failures = 0

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
    # Generate detailed table
    markdown += "\n\n| Status | Test case | Time | Turns | Tools | Cost | Total tokens | Input | Output | Cached | Non-cached | Reasoning | Max output | Compactions |\n"
    markdown += "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"

    # Track totals for summary row
    total_time = 0.0
    total_cost = 0.0
    total_tokens_sum = 0
    total_prompt_tokens_sum = 0
    total_completion_tokens_sum = 0
    total_cached_tokens_sum = 0
    total_non_cached_tokens_sum = 0
    total_reasoning_tokens_sum = 0
    max_completion_per_call_max = 0
    total_compactions = 0
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

        # Format time (plain, no inline comparison)
        exec_time = result.get("holmes_duration")
        time_str = f"{exec_time:.1f}s" if exec_time and exec_time > 0 else "—"
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

        # Format cost (plain, no inline comparison)
        cost = result.get("cost", 0)
        cost_str = f"${cost:.4f}" if cost and cost > 0 else "—"
        if cost and cost > 0:
            total_cost += cost

        # Extract token counts
        total_tokens = result.get("total_tokens", 0) or 0
        prompt_tokens = result.get("prompt_tokens", 0) or 0
        completion_tokens = result.get("completion_tokens", 0) or 0
        cached_tokens = result.get("cached_tokens")
        reasoning_tokens = result.get("reasoning_tokens", 0) or 0
        max_completion = result.get("max_completion_tokens_per_call", 0) or 0
        num_compactions = result.get("num_compactions", 0) or 0

        # Compute total_tokens from parts if not reported directly
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens

        # Non-cached = prompt - cached (only meaningful when both are known)
        if prompt_tokens > 0 and cached_tokens is not None:
            non_cached_tokens = prompt_tokens - cached_tokens
        elif prompt_tokens > 0:
            non_cached_tokens = None  # cached unknown, can't compute
        else:
            non_cached_tokens = None

        # Accumulate totals
        total_tokens_sum += total_tokens
        total_prompt_tokens_sum += prompt_tokens
        total_completion_tokens_sum += completion_tokens
        if cached_tokens is not None:
            total_cached_tokens_sum += cached_tokens
        if non_cached_tokens is not None:
            total_non_cached_tokens_sum += non_cached_tokens
        total_reasoning_tokens_sum += reasoning_tokens
        max_completion_per_call_max = max(max_completion_per_call_max, max_completion)
        total_compactions += num_compactions

        # Format for display
        total_tokens_str = _fmt_tokens(total_tokens)
        input_str = _fmt_tokens(prompt_tokens)
        output_str = _fmt_tokens(completion_tokens)
        cached_tokens_str = f"{cached_tokens:,}" if cached_tokens is not None else "—"
        non_cached_tokens_str = f"{non_cached_tokens:,}" if non_cached_tokens is not None else "—"
        reasoning_str = _fmt_tokens(reasoning_tokens)
        max_completion_str = _fmt_tokens(max_completion)
        compactions_str = str(num_compactions) if num_compactions > 0 else "—"

        markdown += f"| {status.markdown_symbol} | {test_case_name} | {time_str} | {turns_str} | {tools_str} | {cost_str} | {total_tokens_str} | {input_str} | {output_str} | {cached_tokens_str} | {non_cached_tokens_str} | {reasoning_str} | {max_completion_str} | {compactions_str} |\n"

    # Add summary row
    avg_time_str = f"{total_time / time_count:.1f}s" if time_count > 0 else "—"
    avg_turns_str = f"{total_turns / turns_count:.1f}" if turns_count > 0 else "—"
    avg_tools_str = f"{total_tools / tools_count:.1f}" if tools_count > 0 else "—"
    total_cost_str = f"${total_cost:.4f}" if total_cost > 0 else "—"
    total_tokens_total_str = _fmt_tokens(total_tokens_sum)
    total_prompt_str = _fmt_tokens(total_prompt_tokens_sum)
    total_completion_str = _fmt_tokens(total_completion_tokens_sum)
    total_cached_tokens_str = _fmt_tokens(total_cached_tokens_sum)
    total_non_cached_tokens_str = _fmt_tokens(total_non_cached_tokens_sum)
    total_reasoning_str = _fmt_tokens(total_reasoning_tokens_sum)
    max_completion_max_str = _fmt_tokens(max_completion_per_call_max)
    total_compactions_str = str(total_compactions) if total_compactions > 0 else "—"
    markdown += f"| | **Total** | **{avg_time_str}** avg | **{avg_turns_str}** avg | **{avg_tools_str}** avg | **{total_cost_str}** | **{total_tokens_total_str}** | **{total_prompt_str}** | **{total_completion_str}** | **{total_cached_tokens_str}** | **{total_non_cached_tokens_str}** | **{total_reasoning_str}** | **{max_completion_max_str}** | **{total_compactions_str}** |\n"

    # Add footer explaining benchmark comparison status
    if not benchmark and historical_details and historical_details.status:
        markdown += (
            f"\n_Benchmark comparison unavailable: {historical_details.status}_\n"
        )

    # Add collapsible details section with comparison tables
    if historical_details:
        markdown += _generate_historical_details_section(
            historical_details,
            sorted_results=sorted_results if comparison_map else None,
            comparison_map=comparison_map or None,
            benchmark=benchmark or None,
        )

    return (
        markdown,
        sorted_results,
        ask_holmes_regressions,
    )

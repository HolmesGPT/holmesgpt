"""GitHub Actions reporting functionality."""

import logging
import os
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from tests.llm.utils.braintrust import get_braintrust_url
from tests.llm.utils.braintrust_history import (
    BRAINTRUST_ORG,
    BRAINTRUST_PROJECT,
    BenchmarkMetrics,
    HistoricalComparisonDetails,
    get_benchmark_baseline,
    get_master_baseline,
)
from tests.llm.utils.test_env_vars import GITHUB_REF_NAME
from tests.llm.utils.test_results import TestStatus


_TEST_TYPE_TO_FIXTURE_DIR = {
    "ask": "test_ask_holmes",
    "investigate": "test_investigate",
}


def _get_eval_source_url(test_type: str, test_case_name: str) -> Optional[str]:
    """Build a GitHub URL to an eval's test_case.yaml on the branch this run executed from.

    Returns None if the test_type is not a known fixture-backed test (e.g. "unknown").

    Ref resolution (first non-empty wins):
      1. EVAL_BRANCH — explicit override
      2. GITHUB_HEAD_REF — PR head branch (only set on pull_request events;
         GITHUB_REF_NAME on PRs is the virtual "<num>/merge" ref which is not browsable)
      3. GITHUB_REF_NAME — branch name on push events
      4. "master"
    """
    fixture_dir = _TEST_TYPE_TO_FIXTURE_DIR.get(test_type)
    if not fixture_dir or not test_case_name:
        return None
    # Strip pytest parametrize suffix (e.g. "227_count_configmaps_per_namespace[0]"
    # → "227_count_configmaps_per_namespace"); the fixture directory on disk
    # does not include the "[…]" portion.
    fixture_name = test_case_name.split("[", 1)[0]
    ref = (
        os.environ.get("EVAL_BRANCH")
        or os.environ.get("GITHUB_HEAD_REF")
        or GITHUB_REF_NAME
        or "master"
    )
    encoded_ref = quote(ref, safe="")
    return (
        f"https://github.com/HolmesGPT/holmesgpt/blob/{encoded_ref}"
        f"/tests/llm/fixtures/{fixture_dir}/{fixture_name}/test_case.yaml"
    )


def _fmt_tokens(value: Optional[int]) -> str:
    """Format a token count: comma-separated if present, dash if absent/zero."""
    if value is not None and value > 0:
        return f"{value:,}"
    return "—"


def _fmt_denied_commands(commands: Optional[List[str]]) -> str:
    """Format denied bash commands for a markdown table cell.

    Each command is wrapped in backticks and pipe/newline characters are escaped
    so they don't break the surrounding table. Multiple commands are stacked with
    <br>. Returns an em dash when there are none.
    """
    if not commands:
        return "—"
    rendered = []
    for cmd in commands:
        safe = str(cmd).replace("|", "\\|").replace("\n", " ").replace("`", "")
        rendered.append(f"`{safe}`")
    return "<br>".join(rendered)


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


def _diff_cell(cur, base) -> str:
    if cur is None or cur == 0 or base is None or base == 0:
        return "—"
    return _format_diff_pct(_calc_diff_pct(float(cur), float(base)))


def _render_metric_table(
    title: str,
    rows: List[dict],
    current_key: str,
    master_key: str,
    benchmark_key: str,
    formatter,
    master_label: str,
    benchmark_label: str,
) -> List[str]:
    """Render a six-column comparison table with two baselines and average rows.

    Columns: Test case | This branch | master (abs) | Δ vs master | benchmark (abs) | Δ vs benchmark

    Skips the table entirely when no row has either master or benchmark data.

    Emits two summary rows:
    - **Total (all)** — each column averages over its own non-null subset.
      Δ cells are intentionally empty because the subsets may differ
      (e.g. a test missing from master vs present in benchmark) and the
      delta would be apples-to-oranges.
    - **Comparable (m=N, b=N)** — per-baseline matched subsets only:
      Δ vs master and the master column are computed across rows where
      both this-branch and master have a value; same for benchmark.
      Use this row to read deltas; use the Total row to read absolutes.
    """
    has_master = any(r[master_key] is not None for r in rows)
    has_bench = any(r[benchmark_key] is not None for r in rows)
    if not has_master and not has_bench:
        return []

    out: List[str] = [
        f"\n**{title}:**\n",
        f"| Test case | This branch | {master_label} | Δ vs master | {benchmark_label} | Δ vs benchmark |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    cur_sum_m = base_sum_m = 0.0
    matched_m = 0
    cur_sum_b = base_sum_b = 0.0
    matched_b = 0
    cur_all: List[float] = []
    mast_all: List[float] = []
    bench_all: List[float] = []

    for r in rows:
        cur = r[current_key]
        mast = r[master_key]
        bench = r[benchmark_key]
        cur_s = formatter(cur) if cur else "—"
        mast_s = formatter(mast) if mast else "—"
        bench_s = formatter(bench) if bench else "—"
        out.append(
            f"| {r['name']} | {cur_s} | {mast_s} | {_diff_cell(cur, mast)} "
            f"| {bench_s} | {_diff_cell(cur, bench)} |"
        )
        if cur is not None:
            cur_all.append(float(cur))
        if mast is not None:
            mast_all.append(float(mast))
        if bench is not None:
            bench_all.append(float(bench))
        if cur and mast:
            cur_sum_m += float(cur); base_sum_m += float(mast); matched_m += 1
        if cur and bench:
            cur_sum_b += float(cur); base_sum_b += float(bench); matched_b += 1

    # Total (all): each column averaged over its own non-null subset.
    # Δ cells stay empty — the subsets may differ, so a delta would compare
    # different sets of tests.
    if cur_all or mast_all or bench_all:
        all_cur_avg = sum(cur_all) / len(cur_all) if cur_all else None
        all_mast_avg = sum(mast_all) / len(mast_all) if mast_all else None
        all_bench_avg = sum(bench_all) / len(bench_all) if bench_all else None
        out.append(
            f"| **Total (all, n={len(cur_all)})** "
            f"| **{formatter(all_cur_avg) if all_cur_avg is not None else '—'}** "
            f"| **{formatter(all_mast_avg) if all_mast_avg is not None else '—'}** "
            f"| **—** "
            f"| **{formatter(all_bench_avg) if all_bench_avg is not None else '—'}** "
            f"| **—** |"
        )

    # Comparable: per-baseline matched subsets only. Apples-to-apples deltas.
    if matched_m or matched_b:
        avg_cur_m = (cur_sum_m / matched_m) if matched_m else None
        avg_mast = (base_sum_m / matched_m) if matched_m else None
        avg_cur_b = (cur_sum_b / matched_b) if matched_b else None
        avg_bench = (base_sum_b / matched_b) if matched_b else None
        # Show whichever current-mean we have; prefer master's matched set for the
        # "This branch" column when both exist (it's the more recent comparison).
        avg_cur = avg_cur_m if avg_cur_m is not None else avg_cur_b
        out.append(
            f"| **Comparable (m={matched_m}, b={matched_b})** "
            f"| **{formatter(avg_cur) if avg_cur is not None else '—'}** "
            f"| **{formatter(avg_mast) if avg_mast is not None else '—'}** "
            f"| **{_diff_cell(avg_cur_m, avg_mast)}** "
            f"| **{formatter(avg_bench) if avg_bench is not None else '—'}** "
            f"| **{_diff_cell(avg_cur_b, avg_bench)}** |"
        )
    out.append("")
    return out


def _format_age(created: Optional[str]) -> str:
    """Format an ISO date string into a short '(N days ago)' style label."""
    if not created:
        return ""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        days = delta.total_seconds() / 86400
        if days < 1:
            hours = max(1, int(delta.total_seconds() // 3600))
            return f"{hours}h ago"
        return f"{int(days)}d ago"
    except Exception:
        return ""


def _generate_comparison_tables(
    sorted_results: List[dict],
    benchmark: Dict[str, BenchmarkMetrics],
    master: Dict[str, BenchmarkMetrics],
    benchmark_age: str = "",
    master_age: str = "",
) -> str:
    """Generate six-column comparison tables (master + benchmark)."""
    lines: List[str] = []
    master_label = f"master ({master_age})" if master_age else "master"
    benchmark_label = f"benchmark ({benchmark_age})" if benchmark_age else "benchmark"

    rows: List[dict] = []
    for result in sorted_results:
        test_name = result.get("test_case_name", "")
        model = result.get("model", "")
        key = f"{test_name}:{model}"
        m = master.get(key)
        b = benchmark.get(key)

        display_name = f"{test_name} ({model})" if model else test_name
        source_url = _get_eval_source_url(result.get("test_type", ""), test_name)
        if source_url:
            display_name = f"{display_name} [📄]({source_url})"

        rows.append({
            "name": display_name,
            "current_time": result.get("holmes_duration"),
            "master_time": m.duration if m else None,
            "benchmark_time": b.duration if b else None,
            "current_cost": result.get("cost"),
            "master_cost": m.cost if m else None,
            "benchmark_cost": b.cost if b else None,
            "current_total_tokens": result.get("total_tokens", 0) or 0,
            "master_total_tokens": m.total_tokens if m else None,
            "benchmark_total_tokens": b.total_tokens if b else None,
            "current_cached_tokens": result.get("cached_tokens"),
            "master_cached_tokens": m.cached_tokens if m else None,
            "benchmark_cached_tokens": b.cached_tokens if b else None,
            "current_turns": result.get("num_llm_calls"),
            "master_turns": m.num_llm_calls if m else None,
            "benchmark_turns": b.num_llm_calls if b else None,
            "current_tool_calls": result.get("tool_call_count"),
            "master_tool_calls": m.tool_call_count if m else None,
            "benchmark_tool_calls": b.tool_call_count if b else None,
        })

    def render(title, ckey, mkey, bkey, fmt):
        return _render_metric_table(title, rows, ckey, mkey, bkey, fmt, master_label, benchmark_label)

    lines += render("Time comparison (seconds)", "current_time", "master_time", "benchmark_time", lambda v: f"{v:.1f}s")
    lines += render("Cost comparison", "current_cost", "master_cost", "benchmark_cost", lambda v: f"${v:.4f}")
    lines += render("Total tokens comparison", "current_total_tokens", "master_total_tokens", "benchmark_total_tokens", lambda v: f"{int(round(v)):,}")
    lines += render("Cached tokens comparison", "current_cached_tokens", "master_cached_tokens", "benchmark_cached_tokens", lambda v: f"{int(round(v)):,}")
    lines += render("Turns comparison", "current_turns", "master_turns", "benchmark_turns", lambda v: f"{v:.1f}" if isinstance(v, float) else str(int(v)))
    lines += render("Tool calls comparison", "current_tool_calls", "master_tool_calls", "benchmark_tool_calls", lambda v: f"{v:.1f}" if isinstance(v, float) else str(int(v)))

    if not lines:
        current_models = sorted({r.get("model", "") for r in sorted_results if r.get("model")})
        all_baseline = {b.model for b in benchmark.values() if b.model} | {m.model for m in master.values() if m.model}
        overlap = set(current_models) & all_baseline
        lines.append("\n_No baseline data available for comparison._\n")
        if current_models and all_baseline and not overlap:
            lines.append(
                f"_Model mismatch: current run uses {current_models} but the "
                f"master/benchmark experiments were run against {sorted(all_baseline)}. "
                "Align the model lists so at least one model is shared._\n"
            )

    return "\n".join(lines)


def _generate_historical_details_section(
    benchmark_details: HistoricalComparisonDetails,
    master_details: Optional[HistoricalComparisonDetails],
    sorted_results: Optional[List[dict]] = None,
    benchmark: Optional[Dict[str, BenchmarkMetrics]] = None,
    master: Optional[Dict[str, BenchmarkMetrics]] = None,
) -> str:
    """Render the collapsible "Benchmark Comparison Details" section.

    Shows status + experiment links for both the master post-merge experiment
    and the weekly ci-benchmark, then renders six-column metric tables.
    """
    lines = ["<details>", "<summary><b>Benchmark Comparison Details</b></summary>\n"]

    def render_source(label: str, d: Optional[HistoricalComparisonDetails]):
        if d is None:
            return
        lines.append(f"**{label}:** {d.filter_description}")
        if d.status:
            lines.append(f"_Status: {d.status}_")
        else:
            lines.append(f"_Status: {d.metrics_count} test/model combinations loaded_")
        if d.experiments:
            for exp in d.experiments:
                exp_url = f"https://www.braintrust.dev/app/{BRAINTRUST_ORG}/p/{BRAINTRUST_PROJECT}/experiments/{exp.id}"
                created_info = f" (created: {exp.created[:10]})" if exp.created else ""
                lines.append(f"- [{exp.name}]({exp_url}){created_info}")
        lines.append("")

    render_source("Master baseline", master_details)
    render_source("Benchmark baseline", benchmark_details)

    # Comparison tables — only render if we have at least one baseline with data
    if sorted_results and (benchmark or master):
        bench_age = ""
        master_age = ""
        if benchmark_details and benchmark_details.experiments:
            bench_age = _format_age(benchmark_details.experiments[0].created)
        if master_details and master_details.experiments:
            master_age = _format_age(master_details.experiments[0].created)
        lines.append(
            _generate_comparison_tables(
                sorted_results,
                benchmark or {},
                master or {},
                benchmark_age=bench_age,
                master_age=master_age,
            )
        )

    # Errors from either source
    errors: List[str] = []
    if benchmark_details and benchmark_details.errors:
        errors.extend(benchmark_details.errors)
    if master_details and master_details.errors:
        errors.extend(master_details.errors)
    if errors:
        lines.append("\n**Errors:**\n")
        lines.append("```")
        for error in errors:
            lines.append(error)
        lines.append("```\n")

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

    # Fetch both baselines: post-merge master eval (recent) + weekly ci-benchmark (stable)
    benchmark: Dict[str, BenchmarkMetrics] = {}
    master: Dict[str, BenchmarkMetrics] = {}
    benchmark_details: Optional[HistoricalComparisonDetails] = None
    master_details: Optional[HistoricalComparisonDetails] = None
    if include_historical:
        try:
            benchmark, benchmark_details = get_benchmark_baseline()
            if benchmark:
                logging.info(f"Loaded benchmark baseline: {len(benchmark)} test/model combinations")
        except Exception as e:
            benchmark_details = HistoricalComparisonDetails(status=f"API error: {e}")
            logging.warning(f"Failed to fetch benchmark baseline: {e}")
        try:
            master, master_details = get_master_baseline()
            if master:
                logging.info(f"Loaded master baseline: {len(master)} test/model combinations")
        except Exception as e:
            master_details = HistoricalComparisonDetails(status=f"API error: {e}")
            logging.warning(f"Failed to fetch master baseline: {e}")

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

    # Warn (above the table) when the run attempted bash commands that were denied
    # by the eval's allow/deny list. These are listed in the "Denied commands" column.
    denied_total = sum(len(r.get("denied_commands") or []) for r in sorted_results)
    if denied_total > 0:
        plural = "s" if denied_total != 1 else ""
        markdown += (
            f"\n> ⚠️ **Warning:** this eval run contains {denied_total} denied "
            f"bash command{plural}.\n"
        )

    # Generate detailed table
    markdown += "\n\n| Status | Test case | Time | Turns | Tools | Cost | Total tokens | Input | Max input | Output | Max output | Cached | Non-cached | Reasoning | Compactions | Denied commands | Src |\n"
    markdown += "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"

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
    max_prompt_per_call_max = 0
    total_compactions = 0
    total_turns = 0
    total_tools = 0
    total_denied_commands = 0
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

        # Link to the eval's test_case.yaml on the branch this run ran from
        # (rendered as its own "Src" column at the end of the row).
        source_url = _get_eval_source_url(
            result.get("test_type", ""), result["test_case_name"]
        )
        source_str = f"[src]({source_url})" if source_url else "—"

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
        max_prompt = result.get("max_prompt_tokens_per_call", 0) or 0
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
        max_prompt_per_call_max = max(max_prompt_per_call_max, max_prompt)
        total_compactions += num_compactions

        # Format for display
        total_tokens_str = _fmt_tokens(total_tokens)
        input_str = _fmt_tokens(prompt_tokens)
        output_str = _fmt_tokens(completion_tokens)
        cached_tokens_str = f"{cached_tokens:,}" if cached_tokens is not None else "—"
        non_cached_tokens_str = f"{non_cached_tokens:,}" if non_cached_tokens is not None else "—"
        reasoning_str = _fmt_tokens(reasoning_tokens)
        max_completion_str = _fmt_tokens(max_completion)
        max_prompt_str = _fmt_tokens(max_prompt)
        compactions_str = str(num_compactions) if num_compactions > 0 else "—"

        # Bash commands HolmesGPT tried to run that were denied by the eval's
        # allow/deny list (no interactive approver exists during evals).
        denied_commands = result.get("denied_commands") or []
        total_denied_commands += len(denied_commands)
        denied_commands_str = _fmt_denied_commands(denied_commands)

        markdown += f"| {status.markdown_symbol} | {test_case_name} | {time_str} | {turns_str} | {tools_str} | {cost_str} | {total_tokens_str} | {input_str} | {max_prompt_str} | {output_str} | {max_completion_str} | {cached_tokens_str} | {non_cached_tokens_str} | {reasoning_str} | {compactions_str} | {denied_commands_str} | {source_str} |\n"

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
    max_prompt_max_str = _fmt_tokens(max_prompt_per_call_max)
    total_compactions_str = str(total_compactions) if total_compactions > 0 else "—"
    total_denied_str = str(total_denied_commands) if total_denied_commands > 0 else "—"
    markdown += f"| | **Total** | **{avg_time_str}** avg | **{avg_turns_str}** avg | **{avg_tools_str}** avg | **{total_cost_str}** | **{total_tokens_total_str}** | **{total_prompt_str}** | **{max_prompt_max_str}** | **{total_completion_str}** | **{max_completion_max_str}** | **{total_cached_tokens_str}** | **{total_non_cached_tokens_str}** | **{total_reasoning_str}** | **{total_compactions_str}** | **{total_denied_str}** | |\n"

    # Add footer explaining when no baseline available
    if not benchmark and not master:
        msgs = []
        if benchmark_details and benchmark_details.status:
            msgs.append(f"benchmark: {benchmark_details.status}")
        if master_details and master_details.status:
            msgs.append(f"master: {master_details.status}")
        if msgs:
            markdown += f"\n_No baseline available for comparison ({'; '.join(msgs)})_\n"

    # Add collapsible details section with comparison tables
    if benchmark_details or master_details:
        markdown += _generate_historical_details_section(
            benchmark_details or HistoricalComparisonDetails(),
            master_details=master_details,
            sorted_results=sorted_results if (benchmark or master) else None,
            benchmark=benchmark or None,
            master=master or None,
        )

    return (
        markdown,
        sorted_results,
        ask_holmes_regressions,
    )

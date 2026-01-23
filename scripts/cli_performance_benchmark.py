#!/usr/bin/env python3
"""
CLI Performance Benchmark for HolmesGPT

Measures CLI performance with focus on deterministic startup overhead.
Independent of the eval framework - pure black-box CLI timing.

Reports both cold start (first run) and warm start (subsequent runs).

Usage:
    # Measure startup time only (deterministic, no API key needed)
    python scripts/cli_performance_benchmark.py --startup-only

    # Full e2e benchmark (startup + LLM)
    python scripts/cli_performance_benchmark.py --e2e-only

    # Compare against baseline
    python scripts/cli_performance_benchmark.py --compare baseline.json
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev


@dataclass
class BenchmarkResult:
    """Result from a single benchmark run."""
    wall_time_seconds: float
    exit_code: int
    timestamp: str
    benchmark_type: str  # "startup" or "e2e"
    model: str = ""
    prompt: str = ""


@dataclass
class BenchmarkSummary:
    """Summary statistics from multiple benchmark runs."""
    benchmark_type: str  # "startup", "e2e", or "combined"
    iterations: int
    # Cold start (first run - no caches warm)
    cold_start_seconds: float
    # Warm start stats (subsequent runs)
    warm_mean_seconds: float
    warm_median_seconds: float
    warm_min_seconds: float
    warm_max_seconds: float
    warm_stdev_seconds: float | None
    # All times for reference
    all_times: list[float]  # [cold, warm1, warm2, ...]
    # Metadata
    timestamp: str
    git_sha: str
    git_branch: str
    # Optional fields for e2e benchmarks
    prompt: str = ""
    model: str = ""
    # For combined benchmarks - startup metrics
    startup_cold_seconds: float | None = None
    startup_warm_mean_seconds: float | None = None


def get_git_info() -> tuple[str, str]:
    """Get current git SHA and branch."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True
        ).stdout.strip()[:8]
    except Exception:
        sha = "unknown"

    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        branch = "unknown"

    return sha, branch


def run_holmes_startup() -> BenchmarkResult:
    """
    Measure CLI startup time using `holmes version`.

    This measures the deterministic overhead:
    - Python interpreter startup
    - Import time for all modules
    - CLI initialization (typer, etc.)

    Does NOT include any LLM calls.
    """
    cmd = ["poetry", "run", "holmes", "version"]

    start = time.perf_counter()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    elapsed = time.perf_counter() - start

    return BenchmarkResult(
        wall_time_seconds=elapsed,
        exit_code=result.returncode,
        timestamp=datetime.utcnow().isoformat(),
        benchmark_type="startup",
    )


def run_holmes_ask(prompt: str, model: str | None = None) -> BenchmarkResult:
    """
    Run holmes ask and measure wall time (startup + LLM call).

    This measures end-to-end time including:
    - CLI startup (imports, init)
    - Config loading
    - Toolset initialization
    - LLM API call
    - Response formatting
    """
    cmd = [
        "poetry", "run", "holmes", "ask",
        prompt,
        "--no-interactive",
        "--no-echo",
    ]

    if model:
        cmd.extend(["--model", model])

    start = time.perf_counter()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    elapsed = time.perf_counter() - start

    return BenchmarkResult(
        wall_time_seconds=elapsed,
        exit_code=result.returncode,
        timestamp=datetime.utcnow().isoformat(),
        benchmark_type="e2e",
        model=model or "default",
        prompt=prompt,
    )


def run_startup_benchmark(iterations: int = 5) -> BenchmarkSummary:
    """
    Benchmark CLI startup time with cold and warm measurements.

    First run = cold start (no bytecode cache, no fs cache)
    Subsequent runs = warm start (caches populated)
    """
    git_sha, git_branch = get_git_info()

    all_times: list[float] = []

    # Run all iterations (first = cold, rest = warm)
    for i in range(iterations):
        run_type = "cold" if i == 0 else "warm"
        print(f"Startup iteration {i + 1}/{iterations} ({run_type})...", file=sys.stderr)
        result = run_holmes_startup()

        if result.exit_code != 0:
            print(f"Warning: iteration {i + 1} failed with exit code {result.exit_code}", file=sys.stderr)
            if i == 0:
                raise RuntimeError("Cold start benchmark failed")
            continue

        all_times.append(result.wall_time_seconds)
        print(f"  Time: {result.wall_time_seconds:.3f}s ({run_type})", file=sys.stderr)

    if len(all_times) < 2:
        raise RuntimeError("Need at least 2 successful iterations (1 cold + 1 warm)")

    cold_time = all_times[0]
    warm_times = all_times[1:]

    return BenchmarkSummary(
        benchmark_type="startup",
        iterations=len(all_times),
        cold_start_seconds=cold_time,
        warm_mean_seconds=mean(warm_times),
        warm_median_seconds=median(warm_times),
        warm_min_seconds=min(warm_times),
        warm_max_seconds=max(warm_times),
        warm_stdev_seconds=stdev(warm_times) if len(warm_times) > 1 else None,
        all_times=all_times,
        timestamp=datetime.utcnow().isoformat(),
        git_sha=git_sha,
        git_branch=git_branch,
    )


def run_e2e_benchmark(
    prompt: str = "hello, please reply with the word hello",
    iterations: int = 3,
    model: str | None = None,
) -> BenchmarkSummary:
    """
    Benchmark full end-to-end CLI execution with cold and warm measurements.

    Serves as sanity check that CLI commands work.
    """
    git_sha, git_branch = get_git_info()

    all_times: list[float] = []

    for i in range(iterations):
        run_type = "cold" if i == 0 else "warm"
        print(f"E2E iteration {i + 1}/{iterations} ({run_type})...", file=sys.stderr)
        result = run_holmes_ask(prompt, model)

        if result.exit_code != 0:
            print(f"Warning: iteration {i + 1} failed with exit code {result.exit_code}", file=sys.stderr)
            if i == 0:
                raise RuntimeError("Cold start e2e benchmark failed")
            continue

        all_times.append(result.wall_time_seconds)
        print(f"  Time: {result.wall_time_seconds:.2f}s ({run_type})", file=sys.stderr)

    if len(all_times) < 2:
        raise RuntimeError("Need at least 2 successful iterations (1 cold + 1 warm)")

    cold_time = all_times[0]
    warm_times = all_times[1:]

    return BenchmarkSummary(
        benchmark_type="e2e",
        prompt=prompt,
        model=model or "default",
        iterations=len(all_times),
        cold_start_seconds=cold_time,
        warm_mean_seconds=mean(warm_times),
        warm_median_seconds=median(warm_times),
        warm_min_seconds=min(warm_times),
        warm_max_seconds=max(warm_times),
        warm_stdev_seconds=stdev(warm_times) if len(warm_times) > 1 else None,
        all_times=all_times,
        timestamp=datetime.utcnow().isoformat(),
        git_sha=git_sha,
        git_branch=git_branch,
    )


def run_combined_benchmark(
    prompt: str = "hello, please reply with the word hello",
    startup_iterations: int = 5,
    e2e_iterations: int = 3,
    model: str | None = None,
) -> BenchmarkSummary:
    """
    Run both startup and e2e benchmarks and combine results.

    Returns e2e summary with startup metrics included for comparison.
    """
    print("=" * 50, file=sys.stderr)
    print("Phase 1: Startup benchmark (deterministic)", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    startup = run_startup_benchmark(iterations=startup_iterations)

    print("\n" + "=" * 50, file=sys.stderr)
    print("Phase 2: E2E benchmark (startup + LLM)", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    e2e = run_e2e_benchmark(prompt=prompt, iterations=e2e_iterations, model=model)

    # Combine results - e2e as primary with startup info included
    return BenchmarkSummary(
        benchmark_type="combined",
        prompt=e2e.prompt,
        model=e2e.model,
        iterations=e2e.iterations,
        cold_start_seconds=e2e.cold_start_seconds,
        warm_mean_seconds=e2e.warm_mean_seconds,
        warm_median_seconds=e2e.warm_median_seconds,
        warm_min_seconds=e2e.warm_min_seconds,
        warm_max_seconds=e2e.warm_max_seconds,
        warm_stdev_seconds=e2e.warm_stdev_seconds,
        all_times=e2e.all_times,
        timestamp=e2e.timestamp,
        git_sha=e2e.git_sha,
        git_branch=e2e.git_branch,
        startup_cold_seconds=startup.cold_start_seconds,
        startup_warm_mean_seconds=startup.warm_mean_seconds,
    )


def compare_results(current: BenchmarkSummary, baseline: BenchmarkSummary) -> dict:
    """Compare current results against baseline."""

    def calc_diff(curr: float, base: float) -> tuple[float, float]:
        diff = curr - base
        pct = (diff / base) * 100 if base else 0
        return diff, pct

    # Cold start comparison
    cold_diff, cold_pct = calc_diff(current.cold_start_seconds, baseline.cold_start_seconds)

    # Warm mean comparison
    warm_diff, warm_pct = calc_diff(current.warm_mean_seconds, baseline.warm_mean_seconds)

    result = {
        "benchmark_type": current.benchmark_type,
        # Cold start
        "current_cold": current.cold_start_seconds,
        "baseline_cold": baseline.cold_start_seconds,
        "cold_diff_seconds": cold_diff,
        "cold_diff_percent": cold_pct,
        # Warm start
        "current_warm_mean": current.warm_mean_seconds,
        "baseline_warm_mean": baseline.warm_mean_seconds,
        "warm_diff_seconds": warm_diff,
        "warm_diff_percent": warm_pct,
        # Min/max
        "current_warm_min": current.warm_min_seconds,
        "baseline_warm_min": baseline.warm_min_seconds,
        "current_warm_max": current.warm_max_seconds,
        "baseline_warm_max": baseline.warm_max_seconds,
        # Metadata
        "current_sha": current.git_sha,
        "baseline_sha": baseline.git_sha,
    }

    # Determine status based on warm mean (more stable metric)
    # For startup benchmarks, use tighter thresholds
    if current.benchmark_type == "startup":
        if warm_pct < -10:
            status, emoji = "improved", "🟢"
        elif warm_pct > 20:
            status, emoji = "regression", "🔴"
        else:
            status, emoji = "neutral", "🟡"
    else:
        if warm_pct < -5:
            status, emoji = "improved", "🟢"
        elif warm_pct > 10:
            status, emoji = "regression", "🔴"
        else:
            status, emoji = "neutral", "🟡"

    result["status"] = status
    result["emoji"] = emoji

    # Cold start status (separate - can regress independently)
    if cold_pct > 20:
        result["cold_status"] = "regression"
        result["cold_emoji"] = "🔴"
    elif cold_pct < -10:
        result["cold_status"] = "improved"
        result["cold_emoji"] = "🟢"
    else:
        result["cold_status"] = "neutral"
        result["cold_emoji"] = "🟡"

    # Compare startup times if available (combined benchmark)
    if current.startup_cold_seconds is not None and baseline.startup_cold_seconds is not None:
        startup_cold_diff, startup_cold_pct = calc_diff(
            current.startup_cold_seconds, baseline.startup_cold_seconds
        )
        startup_warm_diff, startup_warm_pct = calc_diff(
            current.startup_warm_mean_seconds or 0, baseline.startup_warm_mean_seconds or 0
        )
        result["startup_current_cold"] = current.startup_cold_seconds
        result["startup_baseline_cold"] = baseline.startup_cold_seconds
        result["startup_cold_diff_percent"] = startup_cold_pct
        result["startup_current_warm_mean"] = current.startup_warm_mean_seconds
        result["startup_baseline_warm_mean"] = baseline.startup_warm_mean_seconds
        result["startup_warm_diff_percent"] = startup_warm_pct

        if startup_warm_pct > 20:
            result["startup_status"] = "regression"
            result["startup_emoji"] = "🔴"
        elif startup_warm_pct < -10:
            result["startup_status"] = "improved"
            result["startup_emoji"] = "🟢"
        else:
            result["startup_status"] = "neutral"
            result["startup_emoji"] = "🟡"

    return result


def format_comparison_report(comparison: dict, current: BenchmarkSummary, baseline: BenchmarkSummary) -> str:
    """Format comparison as markdown report."""
    emoji = comparison["emoji"]
    status = comparison["status"].upper()
    bench_type = comparison.get("benchmark_type", "e2e").upper()
    cold_emoji = comparison.get("cold_emoji", "🟡")

    report = f"""## {emoji} CLI Performance Benchmark ({bench_type}): {status}

### {cold_emoji} Cold Start (first run, no caches)

| Metric | Current | Baseline | Change |
|--------|---------|----------|--------|
| Cold Start | {comparison['current_cold']:.3f}s | {comparison['baseline_cold']:.3f}s | {comparison['cold_diff_percent']:+.1f}% |

### Warm Start (subsequent runs, caches populated)

| Metric | Current | Baseline | Change |
|--------|---------|----------|--------|
| Mean | {comparison['current_warm_mean']:.3f}s | {comparison['baseline_warm_mean']:.3f}s | {comparison['warm_diff_percent']:+.1f}% |
| Min | {comparison['current_warm_min']:.3f}s | {comparison['baseline_warm_min']:.3f}s | |
| Max | {comparison['current_warm_max']:.3f}s | {comparison['baseline_warm_max']:.3f}s | |
"""

    # Add startup section if this is a combined benchmark
    if "startup_current_cold" in comparison:
        startup_emoji = comparison.get("startup_emoji", "🟡")
        startup_status = comparison.get("startup_status", "neutral").upper()
        report += f"""
### {startup_emoji} Startup Only (deterministic): {startup_status}

| Metric | Current | Baseline | Change |
|--------|---------|----------|--------|
| Cold | {comparison['startup_current_cold']:.3f}s | {comparison['startup_baseline_cold']:.3f}s | {comparison['startup_cold_diff_percent']:+.1f}% |
| Warm Mean | {comparison['startup_current_warm_mean']:.3f}s | {comparison['startup_baseline_warm_mean']:.3f}s | {comparison['startup_warm_diff_percent']:+.1f}% |
"""

    report += f"""
**Current commit**: `{current.git_sha}` ({current.git_branch})
**Baseline commit**: `{baseline.git_sha}` ({baseline.git_branch})
**Iterations**: {current.iterations}
"""

    if current.prompt:
        report += f"**Prompt**: `{current.prompt}`\n"

    if comparison["status"] == "regression":
        report += "\n⚠️ **Performance regression detected!**\n"
    elif comparison["status"] == "improved":
        report += "\n✨ **Performance improvement!**\n"

    if comparison.get("cold_status") == "regression":
        report += "\n🥶 **Cold start regression!** First-run experience is slower.\n"

    if comparison.get("startup_status") == "regression":
        report += "\n🚨 **Startup time regression!** Check for new imports or initialization overhead.\n"

    return report


def main():
    parser = argparse.ArgumentParser(
        description="CLI Performance Benchmark for HolmesGPT",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Measure startup time only (deterministic, no API key needed)
  python scripts/cli_performance_benchmark.py --startup-only

  # Full e2e benchmark (requires API key)
  python scripts/cli_performance_benchmark.py --e2e-only

  # Combined benchmark (both startup and e2e)
  python scripts/cli_performance_benchmark.py

  # Compare against baseline
  python scripts/cli_performance_benchmark.py --compare baseline.json
        """
    )

    # Benchmark mode
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--startup-only",
        action="store_true",
        help="Only measure startup time (deterministic, no LLM call)"
    )
    mode_group.add_argument(
        "--e2e-only",
        action="store_true",
        help="Only measure end-to-end time (startup + LLM call)"
    )

    parser.add_argument(
        "--iterations", "-n",
        type=int,
        default=None,
        help="Number of benchmark iterations (default: 5 for startup, 3 for e2e)"
    )
    parser.add_argument(
        "--prompt", "-p",
        type=str,
        default="hello, please reply with the word hello",
        help="Prompt to use for e2e benchmarking"
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help="Model to use for e2e benchmark (default: uses config/env default)"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output file for results JSON"
    )
    parser.add_argument(
        "--compare", "-c",
        type=str,
        default=None,
        help="Baseline JSON file to compare against"
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit with code 1 if regression detected"
    )

    args = parser.parse_args()

    # Determine benchmark mode and run
    if args.startup_only:
        iterations = args.iterations or 5
        print(f"Running startup-only benchmark ({iterations} iterations)...", file=sys.stderr)
        summary = run_startup_benchmark(iterations=iterations)
    elif args.e2e_only:
        iterations = args.iterations or 3
        print(f"Running e2e-only benchmark ({iterations} iterations)...", file=sys.stderr)
        summary = run_e2e_benchmark(
            prompt=args.prompt,
            iterations=iterations,
            model=args.model,
        )
    else:
        # Combined benchmark (default)
        startup_iterations = args.iterations or 5
        e2e_iterations = args.iterations or 3
        print("Running combined benchmark (startup + e2e)...", file=sys.stderr)
        summary = run_combined_benchmark(
            prompt=args.prompt,
            startup_iterations=startup_iterations,
            e2e_iterations=e2e_iterations,
            model=args.model,
        )

    # Output results
    result_dict = asdict(summary)

    if args.output:
        Path(args.output).write_text(json.dumps(result_dict, indent=2))
        print(f"\nResults saved to {args.output}", file=sys.stderr)

    # Compare with baseline if provided
    if args.compare:
        baseline_path = Path(args.compare)
        if baseline_path.exists():
            baseline_data = json.loads(baseline_path.read_text())
            baseline = BenchmarkSummary(**baseline_data)
            comparison = compare_results(summary, baseline)

            report = format_comparison_report(comparison, summary, baseline)
            print(report)

            # Store comparison in result
            result_dict["comparison"] = comparison

            # Check for regression
            regression = comparison["status"] == "regression"
            cold_regression = comparison.get("cold_status") == "regression"
            startup_regression = comparison.get("startup_status") == "regression"

            if args.fail_on_regression and (regression or startup_regression):
                if startup_regression:
                    print("❌ Failing due to startup time regression", file=sys.stderr)
                elif cold_regression:
                    print("❌ Failing due to cold start regression", file=sys.stderr)
                else:
                    print("❌ Failing due to performance regression", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Warning: baseline file {args.compare} not found", file=sys.stderr)
    else:
        # Just print summary
        print(f"\n{'=' * 50}", file=sys.stderr)
        print(f"📊 Benchmark Summary ({summary.benchmark_type.upper()})", file=sys.stderr)
        print(f"{'=' * 50}", file=sys.stderr)

        if summary.startup_cold_seconds is not None:
            print(f"\n⚡ Startup (deterministic):", file=sys.stderr)
            print(f"   Cold:      {summary.startup_cold_seconds:.3f}s", file=sys.stderr)
            print(f"   Warm Mean: {summary.startup_warm_mean_seconds:.3f}s", file=sys.stderr)
            print(f"\n🔄 E2E (startup + LLM):", file=sys.stderr)

        print(f"\n🥶 Cold Start: {summary.cold_start_seconds:.3f}s", file=sys.stderr)
        print(f"\n🔥 Warm Start:", file=sys.stderr)
        print(f"   Mean:   {summary.warm_mean_seconds:.3f}s", file=sys.stderr)
        print(f"   Median: {summary.warm_median_seconds:.3f}s", file=sys.stderr)
        print(f"   Min:    {summary.warm_min_seconds:.3f}s", file=sys.stderr)
        print(f"   Max:    {summary.warm_max_seconds:.3f}s", file=sys.stderr)
        if summary.warm_stdev_seconds:
            print(f"   StdDev: {summary.warm_stdev_seconds:.3f}s", file=sys.stderr)

    # Print JSON to stdout for piping
    print(json.dumps(result_dict, indent=2))


if __name__ == "__main__":
    main()

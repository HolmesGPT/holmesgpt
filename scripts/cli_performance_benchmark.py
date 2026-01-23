#!/usr/bin/env python3
"""
CLI Performance Benchmark for HolmesGPT

Measures CLI performance with focus on deterministic startup overhead.
Independent of the eval framework - pure black-box CLI timing.

Two benchmark modes:
1. Startup-only: Times `holmes --version` (pure import/init overhead)
2. End-to-end: Times `holmes ask` with simple prompt (startup + LLM call)

Usage:
    # Measure startup time only (deterministic)
    python scripts/cli_performance_benchmark.py --startup-only

    # Full e2e benchmark (startup + LLM)
    python scripts/cli_performance_benchmark.py

    # Compare against baseline
    python scripts/cli_performance_benchmark.py --compare baseline.json
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
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
    mean_seconds: float
    median_seconds: float
    min_seconds: float
    max_seconds: float
    stdev_seconds: float | None
    timestamp: str
    git_sha: str
    git_branch: str
    all_times: list[float]
    # Optional fields for e2e benchmarks
    prompt: str = ""
    model: str = ""
    # For combined benchmarks
    startup_mean: float | None = None
    startup_median: float | None = None


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


def run_startup_benchmark(iterations: int = 5, warmup: bool = True) -> BenchmarkSummary:
    """
    Benchmark CLI startup time only (deterministic).

    This is the key metric for tracking import/initialization overhead.
    """
    git_sha, git_branch = get_git_info()

    # Warmup run (not counted)
    if warmup:
        print("Running startup warmup...", file=sys.stderr)
        run_holmes_startup()

    # Actual benchmark runs
    times: list[float] = []
    for i in range(iterations):
        print(f"Startup iteration {i + 1}/{iterations}...", file=sys.stderr)
        result = run_holmes_startup()

        if result.exit_code != 0:
            print(f"Warning: iteration {i + 1} failed with exit code {result.exit_code}", file=sys.stderr)
            continue

        times.append(result.wall_time_seconds)
        print(f"  Startup time: {result.wall_time_seconds:.3f}s", file=sys.stderr)

    if not times:
        raise RuntimeError("All startup benchmark iterations failed")

    return BenchmarkSummary(
        benchmark_type="startup",
        iterations=len(times),
        mean_seconds=mean(times),
        median_seconds=median(times),
        min_seconds=min(times),
        max_seconds=max(times),
        stdev_seconds=stdev(times) if len(times) > 1 else None,
        timestamp=datetime.utcnow().isoformat(),
        git_sha=git_sha,
        git_branch=git_branch,
        all_times=times,
    )


def run_e2e_benchmark(
    prompt: str = "hello, please reply with the word hello",
    iterations: int = 3,
    model: str | None = None,
    warmup: bool = True,
) -> BenchmarkSummary:
    """
    Benchmark full end-to-end CLI execution (startup + LLM).

    Serves as sanity check that CLI commands work.
    """
    git_sha, git_branch = get_git_info()

    # Warmup run (not counted)
    if warmup:
        print("Running e2e warmup...", file=sys.stderr)
        run_holmes_ask(prompt, model)

    # Actual benchmark runs
    times: list[float] = []
    for i in range(iterations):
        print(f"E2E iteration {i + 1}/{iterations}...", file=sys.stderr)
        result = run_holmes_ask(prompt, model)

        if result.exit_code != 0:
            print(f"Warning: iteration {i + 1} failed with exit code {result.exit_code}", file=sys.stderr)
            continue

        times.append(result.wall_time_seconds)
        print(f"  E2E time: {result.wall_time_seconds:.2f}s", file=sys.stderr)

    if not times:
        raise RuntimeError("All e2e benchmark iterations failed")

    return BenchmarkSummary(
        benchmark_type="e2e",
        prompt=prompt,
        model=model or "default",
        iterations=len(times),
        mean_seconds=mean(times),
        median_seconds=median(times),
        min_seconds=min(times),
        max_seconds=max(times),
        stdev_seconds=stdev(times) if len(times) > 1 else None,
        timestamp=datetime.utcnow().isoformat(),
        git_sha=git_sha,
        git_branch=git_branch,
        all_times=times,
    )


def run_combined_benchmark(
    prompt: str = "hello, please reply with the word hello",
    startup_iterations: int = 5,
    e2e_iterations: int = 3,
    model: str | None = None,
    warmup: bool = True,
) -> BenchmarkSummary:
    """
    Run both startup and e2e benchmarks and combine results.

    Returns e2e summary with startup metrics included for comparison.
    """
    print("=" * 50, file=sys.stderr)
    print("Phase 1: Startup benchmark (deterministic)", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    startup = run_startup_benchmark(iterations=startup_iterations, warmup=warmup)

    print("\n" + "=" * 50, file=sys.stderr)
    print("Phase 2: E2E benchmark (startup + LLM)", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    e2e = run_e2e_benchmark(prompt=prompt, iterations=e2e_iterations, model=model, warmup=warmup)

    # Combine results - e2e as primary with startup info included
    return BenchmarkSummary(
        benchmark_type="combined",
        prompt=e2e.prompt,
        model=e2e.model,
        iterations=e2e.iterations,
        mean_seconds=e2e.mean_seconds,
        median_seconds=e2e.median_seconds,
        min_seconds=e2e.min_seconds,
        max_seconds=e2e.max_seconds,
        stdev_seconds=e2e.stdev_seconds,
        timestamp=e2e.timestamp,
        git_sha=e2e.git_sha,
        git_branch=e2e.git_branch,
        all_times=e2e.all_times,
        startup_mean=startup.mean_seconds,
        startup_median=startup.median_seconds,
    )


def compare_results(current: BenchmarkSummary, baseline: BenchmarkSummary) -> dict:
    """Compare current results against baseline."""
    mean_diff = current.mean_seconds - baseline.mean_seconds
    mean_pct = (mean_diff / baseline.mean_seconds) * 100

    median_diff = current.median_seconds - baseline.median_seconds
    median_pct = (median_diff / baseline.median_seconds) * 100

    result = {
        "benchmark_type": current.benchmark_type,
        "current_mean": current.mean_seconds,
        "baseline_mean": baseline.mean_seconds,
        "mean_diff_seconds": mean_diff,
        "mean_diff_percent": mean_pct,
        "current_median": current.median_seconds,
        "baseline_median": baseline.median_seconds,
        "median_diff_seconds": median_diff,
        "median_diff_percent": median_pct,
        "current_sha": current.git_sha,
        "baseline_sha": baseline.git_sha,
    }

    # For startup benchmarks, use tighter thresholds (more deterministic)
    if current.benchmark_type == "startup":
        # Startup should be very consistent - flag >20% regression
        if mean_pct < -10:
            status, emoji = "improved", "🟢"
        elif mean_pct > 20:
            status, emoji = "regression", "🔴"
        else:
            status, emoji = "neutral", "🟡"
    else:
        # E2E includes LLM variance - use wider thresholds
        if mean_pct < -5:
            status, emoji = "improved", "🟢"
        elif mean_pct > 10:
            status, emoji = "regression", "🔴"
        else:
            status, emoji = "neutral", "🟡"

    result["status"] = status
    result["emoji"] = emoji

    # Compare startup times if available (combined benchmark)
    if current.startup_mean is not None and baseline.startup_mean is not None:
        startup_diff_pct = ((current.startup_mean - baseline.startup_mean) / baseline.startup_mean) * 100
        result["startup_current_mean"] = current.startup_mean
        result["startup_baseline_mean"] = baseline.startup_mean
        result["startup_diff_percent"] = startup_diff_pct

        # Flag startup regression separately
        if startup_diff_pct > 20:
            result["startup_status"] = "regression"
            result["startup_emoji"] = "🔴"
        elif startup_diff_pct < -10:
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

    report = f"""## {emoji} CLI Performance Benchmark ({bench_type}): {status}

| Metric | Current | Baseline | Change |
|--------|---------|----------|--------|
| Mean | {comparison['current_mean']:.3f}s | {comparison['baseline_mean']:.3f}s | {comparison['mean_diff_percent']:+.1f}% |
| Median | {comparison['current_median']:.3f}s | {comparison['baseline_median']:.3f}s | {comparison['median_diff_percent']:+.1f}% |
| Min | {current.min_seconds:.3f}s | {baseline.min_seconds:.3f}s | |
| Max | {current.max_seconds:.3f}s | {baseline.max_seconds:.3f}s | |
"""

    # Add startup section if this is a combined benchmark
    if "startup_current_mean" in comparison:
        startup_emoji = comparison.get("startup_emoji", "🟡")
        startup_status = comparison.get("startup_status", "neutral").upper()
        report += f"""
### {startup_emoji} Startup Time (deterministic): {startup_status}

| Metric | Current | Baseline | Change |
|--------|---------|----------|--------|
| Startup Mean | {comparison['startup_current_mean']:.3f}s | {comparison['startup_baseline_mean']:.3f}s | {comparison['startup_diff_percent']:+.1f}% |
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

    # Add startup-specific warning
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
        "--no-warmup",
        action="store_true",
        help="Skip warmup iteration"
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit with code 1 if regression detected"
    )

    args = parser.parse_args()

    # Determine benchmark mode and run
    warmup = not args.no_warmup

    if args.startup_only:
        iterations = args.iterations or 5
        print(f"Running startup-only benchmark ({iterations} iterations)...", file=sys.stderr)
        summary = run_startup_benchmark(iterations=iterations, warmup=warmup)
    elif args.e2e_only:
        iterations = args.iterations or 3
        print(f"Running e2e-only benchmark ({iterations} iterations)...", file=sys.stderr)
        summary = run_e2e_benchmark(
            prompt=args.prompt,
            iterations=iterations,
            model=args.model,
            warmup=warmup,
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
            warmup=warmup,
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
            startup_regression = comparison.get("startup_status") == "regression"

            if args.fail_on_regression and (regression or startup_regression):
                if startup_regression:
                    print("❌ Failing due to startup time regression", file=sys.stderr)
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

        if summary.startup_mean is not None:
            print(f"\n⚡ Startup (deterministic):", file=sys.stderr)
            print(f"   Mean:   {summary.startup_mean:.3f}s", file=sys.stderr)
            print(f"   Median: {summary.startup_median:.3f}s", file=sys.stderr)
            print(f"\n🔄 E2E (startup + LLM):", file=sys.stderr)

        print(f"   Mean:   {summary.mean_seconds:.3f}s", file=sys.stderr)
        print(f"   Median: {summary.median_seconds:.3f}s", file=sys.stderr)
        print(f"   Min:    {summary.min_seconds:.3f}s", file=sys.stderr)
        print(f"   Max:    {summary.max_seconds:.3f}s", file=sys.stderr)
        if summary.stdev_seconds:
            print(f"   StdDev: {summary.stdev_seconds:.3f}s", file=sys.stderr)

    # Print JSON to stdout for piping
    print(json.dumps(result_dict, indent=2))


if __name__ == "__main__":
    main()

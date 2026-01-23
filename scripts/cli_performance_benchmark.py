#!/usr/bin/env python3
"""
CLI Performance Benchmark for HolmesGPT

Measures wall time of running `holmes ask` with a simple prompt.
Independent of the eval framework - pure black-box CLI timing.

Usage:
    python scripts/cli_performance_benchmark.py
    python scripts/cli_performance_benchmark.py --iterations 5
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
    model: str
    prompt: str


@dataclass
class BenchmarkSummary:
    """Summary statistics from multiple benchmark runs."""
    prompt: str
    model: str
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


def run_holmes_ask(prompt: str, model: str | None = None) -> BenchmarkResult:
    """Run holmes ask and measure wall time."""
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
        model=model or "default",
        prompt=prompt,
    )


def run_benchmark(
    prompt: str = "hello, please reply with the word hello",
    iterations: int = 3,
    model: str | None = None,
    warmup: bool = True,
) -> BenchmarkSummary:
    """Run the benchmark multiple times and return summary statistics."""
    git_sha, git_branch = get_git_info()

    # Warmup run (not counted)
    if warmup:
        print("Running warmup iteration...", file=sys.stderr)
        run_holmes_ask(prompt, model)

    # Actual benchmark runs
    times: list[float] = []
    for i in range(iterations):
        print(f"Running iteration {i + 1}/{iterations}...", file=sys.stderr)
        result = run_holmes_ask(prompt, model)

        if result.exit_code != 0:
            print(f"Warning: iteration {i + 1} failed with exit code {result.exit_code}", file=sys.stderr)
            continue

        times.append(result.wall_time_seconds)
        print(f"  Time: {result.wall_time_seconds:.2f}s", file=sys.stderr)

    if not times:
        raise RuntimeError("All benchmark iterations failed")

    return BenchmarkSummary(
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


def compare_results(current: BenchmarkSummary, baseline: BenchmarkSummary) -> dict:
    """Compare current results against baseline."""
    mean_diff = current.mean_seconds - baseline.mean_seconds
    mean_pct = (mean_diff / baseline.mean_seconds) * 100

    median_diff = current.median_seconds - baseline.median_seconds
    median_pct = (median_diff / baseline.median_seconds) * 100

    # Determine status
    if mean_pct < -5:
        status = "improved"
        emoji = "🟢"
    elif mean_pct > 10:
        status = "regression"
        emoji = "🔴"
    else:
        status = "neutral"
        emoji = "🟡"

    return {
        "status": status,
        "emoji": emoji,
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


def format_comparison_report(comparison: dict, current: BenchmarkSummary, baseline: BenchmarkSummary) -> str:
    """Format comparison as markdown report."""
    emoji = comparison["emoji"]
    status = comparison["status"].upper()

    report = f"""## {emoji} CLI Performance Benchmark: {status}

| Metric | Current | Baseline | Change |
|--------|---------|----------|--------|
| Mean | {comparison['current_mean']:.2f}s | {comparison['baseline_mean']:.2f}s | {comparison['mean_diff_percent']:+.1f}% |
| Median | {comparison['current_median']:.2f}s | {comparison['baseline_median']:.2f}s | {comparison['median_diff_percent']:+.1f}% |
| Min | {current.min_seconds:.2f}s | {baseline.min_seconds:.2f}s | |
| Max | {current.max_seconds:.2f}s | {baseline.max_seconds:.2f}s | |

**Current commit**: `{current.git_sha}` ({current.git_branch})
**Baseline commit**: `{baseline.git_sha}` ({baseline.git_branch})
**Iterations**: {current.iterations}
**Prompt**: `{current.prompt}`
"""

    if comparison["status"] == "regression":
        report += "\n⚠️ **Performance regression detected!** Mean time increased by more than 10%.\n"
    elif comparison["status"] == "improved":
        report += "\n✨ **Performance improvement!** Mean time decreased by more than 5%.\n"

    return report


def main():
    parser = argparse.ArgumentParser(description="CLI Performance Benchmark for HolmesGPT")
    parser.add_argument(
        "--iterations", "-n",
        type=int,
        default=3,
        help="Number of benchmark iterations (default: 3)"
    )
    parser.add_argument(
        "--prompt", "-p",
        type=str,
        default="hello, please reply with the word hello",
        help="Prompt to use for benchmarking"
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help="Model to use (default: uses config/env default)"
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

    # Run benchmark
    print(f"Running CLI performance benchmark ({args.iterations} iterations)...", file=sys.stderr)
    summary = run_benchmark(
        prompt=args.prompt,
        iterations=args.iterations,
        model=args.model,
        warmup=not args.no_warmup,
    )

    # Output results
    result_dict = asdict(summary)

    if args.output:
        Path(args.output).write_text(json.dumps(result_dict, indent=2))
        print(f"Results saved to {args.output}", file=sys.stderr)

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

            if args.fail_on_regression and comparison["status"] == "regression":
                print("❌ Failing due to performance regression", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Warning: baseline file {args.compare} not found", file=sys.stderr)
    else:
        # Just print summary
        print(f"\n📊 Benchmark Summary:", file=sys.stderr)
        print(f"  Mean:   {summary.mean_seconds:.2f}s", file=sys.stderr)
        print(f"  Median: {summary.median_seconds:.2f}s", file=sys.stderr)
        print(f"  Min:    {summary.min_seconds:.2f}s", file=sys.stderr)
        print(f"  Max:    {summary.max_seconds:.2f}s", file=sys.stderr)
        if summary.stdev_seconds:
            print(f"  StdDev: {summary.stdev_seconds:.2f}s", file=sys.stderr)

    # Print JSON to stdout for piping
    print(json.dumps(result_dict, indent=2))


if __name__ == "__main__":
    main()

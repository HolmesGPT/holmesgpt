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
    python scripts/cli_performance_benchmark.py --e2e-only --model "openrouter/anthropic/claude-haiku-4.5"
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
    stdout: str = ""
    stderr: str = ""


@dataclass
class BenchmarkSummary:
    """Summary statistics from multiple benchmark runs."""

    benchmark_type: str  # "startup" or "e2e"
    iterations: int
    cold_start_seconds: float
    warm_mean_seconds: float
    warm_median_seconds: float
    warm_min_seconds: float
    warm_max_seconds: float
    warm_stdev_seconds: float | None
    all_times: list[float]
    timestamp: str
    git_sha: str
    git_branch: str
    prompt: str = ""
    model: str = ""


def get_git_info() -> tuple[str, str]:
    """Get current git SHA and branch."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()[:8]
    except Exception:
        sha = "unknown"

    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        branch = "unknown"

    return sha, branch


def run_command(cmd: list[str], benchmark_type: str, model: str = "", prompt: str = "") -> BenchmarkResult:
    """Run a command and measure wall time."""
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
        timestamp=datetime.now(timezone.utc).isoformat(),
        benchmark_type=benchmark_type,
        model=model,
        prompt=prompt,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def run_benchmark(
    cmd: list[str],
    benchmark_type: str,
    iterations: int,
    model: str = "",
    prompt: str = "",
) -> BenchmarkSummary:
    """Run benchmark iterations and collect statistics."""
    git_sha, git_branch = get_git_info()
    all_times: list[float] = []

    for i in range(iterations):
        run_type = "cold" if i == 0 else "warm"
        print(f"{benchmark_type.upper()} iteration {i + 1}/{iterations} ({run_type})...", file=sys.stderr)

        result = run_command(cmd, benchmark_type, model, prompt)

        if result.exit_code != 0:
            print(f"Warning: iteration {i + 1} failed with exit code {result.exit_code}", file=sys.stderr)
            if result.stderr:
                print(f"STDERR:\n{result.stderr}", file=sys.stderr)
            if result.stdout:
                print(f"STDOUT:\n{result.stdout}", file=sys.stderr)
            if i == 0:
                raise RuntimeError(f"Cold start {benchmark_type} benchmark failed")
            continue

        all_times.append(result.wall_time_seconds)
        print(f"  Time: {result.wall_time_seconds:.2f}s ({run_type})", file=sys.stderr)

    if len(all_times) < 2:
        raise RuntimeError("Need at least 2 successful iterations (1 cold + 1 warm)")

    cold_time = all_times[0]
    warm_times = all_times[1:]

    return BenchmarkSummary(
        benchmark_type=benchmark_type,
        iterations=len(all_times),
        cold_start_seconds=cold_time,
        warm_mean_seconds=mean(warm_times),
        warm_median_seconds=median(warm_times),
        warm_min_seconds=min(warm_times),
        warm_max_seconds=max(warm_times),
        warm_stdev_seconds=stdev(warm_times) if len(warm_times) > 1 else None,
        all_times=all_times,
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_sha=git_sha,
        git_branch=git_branch,
        prompt=prompt,
        model=model,
    )


def run_startup_benchmark(iterations: int = 5) -> BenchmarkSummary:
    """Benchmark CLI startup time using `holmes version`."""
    cmd = ["poetry", "run", "holmes", "version"]
    return run_benchmark(cmd, "startup", iterations)


def run_e2e_benchmark(
    prompt: str = "hello, please reply with the word hello",
    iterations: int = 3,
    model: str | None = None,
) -> BenchmarkSummary:
    """Benchmark full end-to-end CLI execution using `holmes ask`."""
    cmd = ["poetry", "run", "holmes", "ask", prompt, "--no-interactive", "--no-echo"]
    if model:
        cmd.extend(["--model", model])
    return run_benchmark(cmd, "e2e", iterations, model=model or "default", prompt=prompt)


def main():
    parser = argparse.ArgumentParser(
        description="CLI Performance Benchmark for HolmesGPT",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--startup-only",
        action="store_true",
        help="Only measure startup time (deterministic, no LLM call)",
    )
    mode_group.add_argument(
        "--e2e-only",
        action="store_true",
        help="Only measure end-to-end time (startup + LLM call)",
    )

    parser.add_argument(
        "--iterations",
        "-n",
        type=int,
        default=None,
        help="Number of benchmark iterations (default: 5 for startup, 3 for e2e)",
    )
    parser.add_argument(
        "--prompt",
        "-p",
        type=str,
        default="hello, please reply with the word hello",
        help="Prompt to use for e2e benchmarking",
    )
    parser.add_argument(
        "--model",
        "-m",
        type=str,
        default=None,
        help="Model to use for e2e benchmark",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output file for results JSON",
    )

    args = parser.parse_args()

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
        parser.error("Please specify --startup-only or --e2e-only")

    result_dict = asdict(summary)

    if args.output:
        Path(args.output).write_text(json.dumps(result_dict, indent=2))
        print(f"\nResults saved to {args.output}", file=sys.stderr)

    # Print summary
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"📊 Benchmark Summary ({summary.benchmark_type.upper()})", file=sys.stderr)
    print(f"{'=' * 50}", file=sys.stderr)
    print(f"🥶 Cold Start: {summary.cold_start_seconds:.2f}s", file=sys.stderr)
    print(f"🔥 Warm Start: {summary.warm_mean_seconds:.2f}s (mean)", file=sys.stderr)
    print(f"   Min: {summary.warm_min_seconds:.2f}s | Max: {summary.warm_max_seconds:.2f}s", file=sys.stderr)

    # Print JSON to stdout for piping
    print(json.dumps(result_dict, indent=2))


if __name__ == "__main__":
    main()

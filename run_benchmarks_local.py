#!/usr/bin/env python3
"""
Local benchmark runner for HolmesGPT evaluations.

This script provides a clean interface for running LLM evaluation benchmarks locally,
mirroring the behavior of the CI/CD workflow.
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Benchmark type definitions
BENCHMARK_TYPES = {
    "fast-benchmark": "regression or benchmark",
    "full-benchmark": "easy or medium or hard or regression or benchmark",
}


class BenchmarkRunner:
    """Manages local benchmark execution for HolmesGPT evaluations."""

    def __init__(
        self,
        models: List[str],
        markers: str = "regression or benchmark",
        iterations: int = 1,
        filter_tests: Optional[str] = None,
        parallel_workers: Optional[str] = "auto",
        strict_setup: bool = True,
        no_braintrust: bool = False,
        benchmark_type: Optional[str] = None,
    ):
        self.models = models
        self.markers = markers
        self.iterations = min(iterations, 10)  # Cap at 10
        self.filter_tests = filter_tests
        self.parallel_workers = parallel_workers
        self.strict_setup = strict_setup
        self.no_braintrust = no_braintrust
        self.benchmark_type = benchmark_type
        self.experiment_id = os.environ.get(
            "EXPERIMENT_ID",
            f"local-benchmark-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        )

    def check_environment(self) -> None:
        """Check environment setup and display status."""
        print("=" * 50)
        print("🧪 Running Local Benchmarks")
        print("=" * 50)
        if self.benchmark_type:
            print(f"Benchmark:    {self.benchmark_type}")
        print(f"Models:       {', '.join(self.models)}")
        print(f"Markers:      llm and ({self.markers})")
        print(f"Iterations:   {self.iterations}")

        if self.filter_tests:
            print(f"Filter:       {self.filter_tests}")
        if self.parallel_workers:
            workers_display = (
                "auto-detect"
                if self.parallel_workers == "auto"
                else f"{self.parallel_workers} workers"
            )
            print(f"Parallel:     {workers_display}")

        print(f"Strict Setup: {self.strict_setup}")
        print("=" * 50)
        print()

        # Check Kubernetes cluster
        try:
            subprocess.run(
                ["kubectl", "cluster-info"], check=True, capture_output=True, text=True
            )
            print("✅ Kubernetes cluster is accessible")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("⚠️  No Kubernetes cluster found. Some tests may require a cluster.")

        # Check API keys
        print("\nChecking API keys:")
        api_keys = {
            "OPENAI_API_KEY": "OpenAI",
            "ANTHROPIC_API_KEY": "Anthropic",
            "AZURE_API_BASE": "Azure API Base",
            "AZURE_API_KEY": "Azure",
        }

        for key, name in api_keys.items():
            if os.environ.get(key):
                print(f"  ✓ {key} set")
            else:
                print(f"  ✗ {key} not set")

        # Special handling for Braintrust
        if os.environ.get("BRAINTRUST_API_KEY"):
            print("  ✓ BRAINTRUST_API_KEY set")
        else:
            if self.no_braintrust:
                print("  ⚠️  BRAINTRUST_API_KEY not set (running without Braintrust)")
            else:
                print(
                    "  ✗ BRAINTRUST_API_KEY not set (REQUIRED - use --no-braintrust to skip)"
                )
                print("\n❌ ERROR: Braintrust API key is required for benchmarks")
                print(
                    "   Set BRAINTRUST_API_KEY environment variable or use --no-braintrust flag"
                )
                sys.exit(1)
        print()

    def setup_environment(self) -> None:
        """Set up environment variables for the test run."""
        # Export models and iterations
        os.environ["MODEL"] = ",".join(self.models)
        os.environ["ITERATIONS"] = str(self.iterations)
        os.environ["RUN_LIVE"] = os.environ.get("RUN_LIVE", "true")
        os.environ["CLASSIFIER_MODEL"] = os.environ.get("CLASSIFIER_MODEL", "gpt-4.1")
        os.environ["EXPERIMENT_ID"] = self.experiment_id
        # Set UPLOAD_DATASET based on Braintrust configuration
        if self.no_braintrust:
            os.environ["UPLOAD_DATASET"] = "false"
        else:
            os.environ["UPLOAD_DATASET"] = "true"

        print("Environment setup:")
        print(f"  MODEL={os.environ['MODEL']}")
        print(f"  ITERATIONS={self.iterations}")
        print(f"  RUN_LIVE={os.environ['RUN_LIVE']}")
        print(f"  CLASSIFIER_MODEL={os.environ['CLASSIFIER_MODEL']}")
        print(f"  EXPERIMENT_ID={self.experiment_id}")
        print(f"  UPLOAD_DATASET={os.environ.get('UPLOAD_DATASET', 'false')}")
        print()

    def build_pytest_command(self) -> List[str]:
        """Build the pytest command with all necessary arguments."""
        cmd = [
            "poetry",
            "run",
            "pytest",
            "tests/llm/test_ask_holmes.py",
            "-m",
            f"llm and ({self.markers})",
        ]

        if self.filter_tests:
            cmd.extend(["-k", self.filter_tests])

        if self.parallel_workers:
            cmd.extend(["-n", self.parallel_workers])

        # Add strict setup mode handling
        if self.strict_setup:
            cmd.extend(
                [
                    "--strict-setup-mode=true",
                    "--strict-setup-exceptions=22_high_latency_dbi_down",  # Known flaky
                ]
            )
        else:
            cmd.append("--strict-setup-mode=false")

        # Add reporting flags
        cmd.extend(
            [
                "--no-cov",
                "--tb=short",
                "-v",
                "-s",
                "--json-report",
                "--json-report-file=eval_results.json",
            ]
        )

        return cmd

    def run_tests(self) -> int:
        """Execute the pytest command and return the exit code."""
        cmd = self.build_pytest_command()

        print("Running pytest command:")
        print("  " + " ".join(cmd))
        print()
        print("=" * 50)
        print()

        # Run tests (don't fail on test failures, like the bash script)
        result = subprocess.run(cmd)
        return result.returncode

    def generate_report(self) -> None:
        """Generate benchmark reports from test results."""
        print()
        print("=" * 50)
        print("Generating benchmark report...")

        report_script = Path("tests/generate_eval_report.py")
        if not report_script.exists():
            print(
                "⚠️  Report generation script not found: tests/generate_eval_report.py"
            )
            return

        # Create output directories based on benchmark type
        docs_dir = Path("docs/development/evaluations")
        history_dir = docs_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        # Determine output file based on benchmark type
        # History files always use results_TIMESTAMP.md format (⚡ in title distinguishes fast)
        if self.benchmark_type == "fast-benchmark":
            main_output = docs_dir / "fast-benchmark-results.md"
        elif self.benchmark_type == "full-benchmark":
            main_output = docs_dir / "full-benchmark-results.md"
        else:
            # Custom markers - use full benchmark output location
            main_output = docs_dir / "full-benchmark-results.md"

        # Build base command
        cmd = [
            "poetry",
            "run",
            "python",
            str(report_script),
            "--json-file",
            "eval_results.json",
            "--output-file",
            str(main_output),
            "--models",
            ",".join(self.models),
        ]

        # Add benchmark type if specified
        if self.benchmark_type:
            cmd.extend(["--benchmark-type", self.benchmark_type])

        # Set up environment with PYTHONPATH to ensure tests module is importable
        env = os.environ.copy()
        project_root = str(Path.cwd())
        env["PYTHONPATH"] = f"{project_root}:{env.get('PYTHONPATH', '')}"

        try:
            subprocess.run(cmd, check=True, env=env)
            print(f"✅ Report generated: {main_output}")

            # Generate historical copy first (⚡ in title distinguishes fast benchmarks)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            history_output = history_dir / f"results_{timestamp}.md"

            # Update command for historical output
            cmd_history = cmd.copy()
            output_idx = cmd_history.index("--output-file") + 1
            cmd_history[output_idx] = str(history_output)
            subprocess.run(cmd_history, check=True, env=env)
            print(f"📁 Saved historical copy: {history_output}")

            # Create redirect page for latest-results.md pointing to the history file
            latest_output = docs_dir / "latest-results.md"
            history_relative = (
                f"../history/{history_output.name.replace('.md', '/')}".rstrip("/")
                + "/"
            )
            redirect_content = f"""# Latest Results

Redirecting to the latest benchmark results...

<script>
window.location.href = "{history_relative}";
</script>

If you are not redirected automatically, [click here]({history_relative}).
"""
            latest_output.write_text(redirect_content)
            print(f"📋 Updated redirect: {latest_output} -> {history_relative}")

        except subprocess.CalledProcessError as e:
            print(f"❌ Report generation failed: {e}")

    def show_summary(self) -> None:
        """Display test execution summary and next steps."""
        print()
        print("=" * 50)
        print("Test Execution Summary")
        print("=" * 50)
        if self.benchmark_type:
            print(f"Benchmark: {self.benchmark_type}")
        print(f"Models: {', '.join(self.models)}")
        print(f"Markers: llm and ({self.markers})")
        print(f"Iterations: {self.iterations}")

        if self.filter_tests:
            print(f"Filter: {self.filter_tests}")
        if self.parallel_workers:
            workers_display = (
                "auto-detect"
                if self.parallel_workers == "auto"
                else f"{self.parallel_workers} workers"
            )
            print(f"Parallel: {workers_display}")

        print()
        print("Generated files:")

        # Determine which result file to check based on benchmark type
        if self.benchmark_type == "fast-benchmark":
            result_file = "docs/development/evaluations/fast-benchmark-results.md"
        else:
            result_file = "docs/development/evaluations/full-benchmark-results.md"

        files_to_check = [
            ("eval_results.json", "JSON results"),
            ("evals_report.md", "Evaluation report"),
            (result_file, "Benchmark results"),
            ("docs/development/evaluations/latest-results.md", "Latest results"),
        ]

        for filename, description in files_to_check:
            path = Path(filename)
            if path.exists():
                line_count = sum(1 for _ in path.open())
                print(f"  ✓ {filename} ({line_count} lines)")

        print()
        print("=" * 50)
        print("✅ Benchmark run complete!")
        print()
        print("To commit results (like CI/CD would on main):")
        print(f"  git add {result_file}")
        print("  git add docs/development/evaluations/latest-results.md")
        print("  git commit -m 'Update benchmark results [skip ci]'")
        print("=" * 50)

    def run(self) -> int:
        """Run the complete benchmark workflow."""
        self.check_environment()
        self.setup_environment()
        exit_code = self.run_tests()
        self.generate_report()
        self.show_summary()
        return exit_code


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run HolmesGPT evaluation benchmarks locally",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Benchmark Types:
  fast-benchmark  - Quick regression tests (markers: regression or benchmark)
  full-benchmark  - Comprehensive tests (markers: easy or medium or hard or regression or benchmark)

Examples:
  %(prog)s                                    # Default: fast-benchmark with default models
  %(prog)s --benchmark-type fast-benchmark    # Explicit fast benchmark
  %(prog)s --benchmark-type full-benchmark    # Full comprehensive benchmark
  %(prog)s --models gpt-4o                    # Test only gpt-4o (fast-benchmark)
  %(prog)s --markers easy --iterations 3      # Custom markers (cannot combine with --benchmark-type)
  %(prog)s --filter 01_how_many_pods          # Run specific test
  %(prog)s --parallel 6                       # Run with 6 parallel workers
  %(prog)s --parallel 1                       # Run sequentially (no parallelism)
  %(prog)s --no-strict-setup                  # Disable strict setup mode
  %(prog)s --no-braintrust                    # Run without Braintrust (for development only)

Environment variables:
  OPENAI_API_KEY, ANTHROPIC_API_KEY    - LLM API keys
  AZURE_API_BASE, AZURE_API_KEY        - Azure OpenAI configuration
  CLASSIFIER_MODEL                     - Model for scoring (default: gpt-4.1)
  EXPERIMENT_ID                        - Custom experiment name
  BRAINTRUST_API_KEY                   - Required for benchmark tracking (unless --no-braintrust)
        """,
    )

    parser.add_argument(
        "--models",
        type=str,
        default="gpt-5.1,gpt-5,sonnet-4.5,haiku-4.5,deepseek-3.1",
        help="Comma-separated list of models to test (default: %(default)s)",
    )

    parser.add_argument(
        "--benchmark-type",
        type=str,
        choices=list(BENCHMARK_TYPES.keys()),
        default=None,
        help="Type of benchmark to run (default: fast-benchmark). Cannot be combined with --markers.",
    )

    parser.add_argument(
        "--markers",
        type=str,
        default=None,
        help="Custom pytest markers for test selection (combined with 'llm'). Cannot be combined with --benchmark-type.",
    )

    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of iterations per test, max: 10 (default: %(default)s)",
    )

    parser.add_argument(
        "--filter",
        type=str,
        dest="filter_tests",
        help="Filter tests by name pattern (pytest -k equivalent)",
    )

    parser.add_argument(
        "--parallel",
        type=str,
        default="auto",
        dest="parallel_workers",
        help="Number of parallel workers or 'auto' for automatic detection (pytest -n equivalent) (default: %(default)s)",
    )

    parser.add_argument(
        "--no-strict-setup",
        action="store_false",
        dest="strict_setup",
        help="Disable strict setup mode (allow setup failures) (default: strict setup enabled)",
    )

    parser.add_argument(
        "--no-braintrust",
        action="store_true",
        help="Run without Braintrust integration (not recommended for benchmarks)",
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Validate that --benchmark-type and --markers are not both provided
    if args.markers is not None and args.benchmark_type is not None:
        print("❌ ERROR: Cannot combine --benchmark-type with --markers")
        print("   Use either --benchmark-type OR --markers, not both.")
        sys.exit(1)

    # Determine markers and benchmark_type
    if args.markers is not None:
        # Custom markers provided - no benchmark type
        markers = args.markers
        benchmark_type = None
    elif args.benchmark_type is not None:
        # Explicit benchmark type
        benchmark_type = args.benchmark_type
        markers = BENCHMARK_TYPES[benchmark_type]
    else:
        # Default: fast-benchmark
        benchmark_type = "fast-benchmark"
        markers = BENCHMARK_TYPES[benchmark_type]

    # Parse models from comma-separated string
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    runner = BenchmarkRunner(
        models=models,
        markers=markers,
        iterations=args.iterations,
        filter_tests=args.filter_tests,
        parallel_workers=args.parallel_workers,
        strict_setup=args.strict_setup,
        no_braintrust=args.no_braintrust,
        benchmark_type=benchmark_type,
    )

    # Ignore the exit code from tests to match bash script behavior
    runner.run()

    # Exit with 0 even if tests failed (matching bash script behavior)
    sys.exit(0)


if __name__ == "__main__":
    main()

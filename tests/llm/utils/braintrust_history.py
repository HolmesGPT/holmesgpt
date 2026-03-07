"""Fetch the latest weekly benchmark results from Braintrust for comparison.

Compares current eval results against the most recent ci-benchmark experiment
(the weekly scheduled benchmark run on master). This requires only 2-3 API calls
total: one to find the benchmark experiment, and 1-2 to paginate its eval spans.
"""

import logging
import os
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests  # type: ignore[import-untyped]

from holmes.core.tracing import BRAINTRUST_ORG, BRAINTRUST_PROJECT

# Braintrust API base URL
BRAINTRUST_API_URL = "https://api.braintrust.dev/v1"

# CI benchmark experiment name prefix (set by eval-benchmarks.yaml workflow)
BENCHMARK_EXPERIMENT_PREFIX = "ci-benchmark-"

__all__ = [
    "BRAINTRUST_ORG",
    "BRAINTRUST_PROJECT",
    "BenchmarkMetrics",
    "HistoricalComparison",
    "HistoricalComparisonDetails",
    "ExperimentInfo",
    "get_benchmark_baseline",
    "compare_with_benchmark",
]


def _get_api_key() -> Optional[str]:
    """Get the Braintrust API key from environment.

    Checks BRAINTRUST_API_KEY first, then falls back to BRAINTRUST_SERVICE_TOKEN.
    """
    return os.environ.get("BRAINTRUST_API_KEY") or os.environ.get(
        "BRAINTRUST_SERVICE_TOKEN"
    )


@dataclass
class BenchmarkMetrics:
    """Metrics for a single test case from the benchmark run."""

    test_id: str
    model: str
    passed: bool = False
    duration: Optional[float] = None
    cost: Optional[float] = None
    tool_call_count: Optional[int] = None
    total_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None



@dataclass
class HistoricalComparison:
    """Comparison data between current and benchmark metrics."""

    test_id: str
    model: str
    current_duration: Optional[float] = None
    historical_avg_duration: Optional[float] = None
    duration_diff_pct: Optional[float] = None  # Positive = slower, negative = faster
    current_cost: Optional[float] = None
    historical_avg_cost: Optional[float] = None
    cost_diff_pct: Optional[float] = None
    current_passed: Optional[bool] = None
    benchmark_passed: Optional[bool] = None
    sample_count: int = 1


@dataclass
class ExperimentInfo:
    """Information about a benchmark experiment."""

    id: str
    name: str
    branch: str
    created: Optional[str] = None


@dataclass
class HistoricalComparisonDetails:
    """Details about the benchmark comparison for transparency."""

    experiments: List[ExperimentInfo] = field(default_factory=list)
    filter_description: str = ""
    status: str = ""  # Empty = success, otherwise explains why data is missing
    errors: List[str] = field(default_factory=list)
    project_id: Optional[str] = None
    metrics_count: int = 0


def _make_api_request(
    endpoint: str,
    method: str = "GET",
    params: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Make an authenticated request to the Braintrust API."""
    api_key = _get_api_key()
    if not api_key:
        return None

    url = f"{BRAINTRUST_API_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        if method == "GET":
            response = requests.get(url, headers=headers, params=params, timeout=30)
        elif method == "POST":
            response = requests.post(
                url, headers=headers, params=params, json=json_data, timeout=30
            )
        else:
            return None

        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.warning(f"Braintrust API request failed: {e}")
        return None


def _get_project_id() -> Optional[str]:
    """Get the Braintrust project ID for the configured project."""
    result = _make_api_request("/project", params={"org_name": BRAINTRUST_ORG})
    if not result or "objects" not in result:
        return None

    for project in result.get("objects", []):
        if project.get("name") == BRAINTRUST_PROJECT:
            return project.get("id")
    return None


# GitHub repo for the benchmark workflow (used to find latest run ID)
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "robusta-dev/holmesgpt")
BENCHMARK_WORKFLOW = "eval-benchmarks.yaml"


def _find_latest_benchmark_run_id() -> Optional[int]:
    """Query GitHub Actions API for the latest successful benchmark workflow run.

    Returns the run_id which maps to the Braintrust experiment name
    'ci-benchmark-{run_id}'.
    """
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{BENCHMARK_WORKFLOW}/runs"
    try:
        response = requests.get(
            url,
            params={"status": "completed", "conclusion": "success", "per_page": 1},
            timeout=15,
        )
        if response.status_code != 200:
            logging.warning(
                f"GitHub Actions API returned {response.status_code}: {response.text[:200]}"
            )
            return None

        runs = response.json().get("workflow_runs", [])
        if not runs:
            return None

        return runs[0]["id"]
    except requests.exceptions.RequestException as e:
        logging.warning(f"GitHub Actions API request failed: {e}")
        return None


def _find_latest_benchmark_experiment(
    project_id: str,
) -> Optional[Dict[str, Any]]:
    """Find the most recent ci-benchmark root experiment.

    Queries GitHub Actions API for the latest successful benchmark workflow run ID,
    then does an exact name lookup in Braintrust via experiment_name filter.
    Total: 1 GitHub API call + 1 Braintrust API call.
    """
    run_id = _find_latest_benchmark_run_id()
    if not run_id:
        return None

    experiment_name = f"{BENCHMARK_EXPERIMENT_PREFIX}{run_id}"
    result = _make_api_request(
        "/experiment",
        params={
            "project_id": project_id,
            "experiment_name": experiment_name,
        },
    )
    if not result:
        return None

    objects = result.get("objects", [])
    if objects:
        logging.info(f"Found benchmark experiment: {experiment_name}")
        return objects[0]

    logging.warning(f"Benchmark experiment '{experiment_name}' not found in Braintrust")
    return None


def _fetch_all_eval_spans(experiment_id: str) -> List[Dict[str, Any]]:
    """Fetch all eval-type spans from an experiment, handling pagination."""
    all_eval_spans: List[Dict[str, Any]] = []
    cursor = None

    for _ in range(20):  # Safety limit on pagination
        body: Dict[str, Any] = {"limit": 100}
        if cursor:
            body["cursor"] = cursor

        result = _make_api_request(
            f"/experiment/{experiment_id}/fetch",
            method="POST",
            json_data=body,
        )
        if not result:
            break

        events = result.get("events", [])
        if not events:
            break

        # Filter for eval spans client-side (more reliable than server-side filter)
        for event in events:
            span_attrs = event.get("span_attributes") or {}
            if span_attrs.get("type") == "eval":
                all_eval_spans.append(event)

        cursor = result.get("cursor")
        if not cursor:
            break

    return all_eval_spans


def _extract_metrics(span: Dict[str, Any]) -> Optional[BenchmarkMetrics]:
    """Extract metrics from an eval span."""
    metadata = span.get("metadata") or {}
    scores = span.get("scores") or {}
    metrics = span.get("metrics") or {}

    test_id = metadata.get("eval_id") or metadata.get("test_id", "")
    model = metadata.get("model", "")

    if not test_id or not model:
        return None

    duration = metadata.get("holmes_duration")
    tool_calls = metadata.get("tool_call_count")
    correctness = scores.get("correctness")
    passed = int(correctness) == 1 if correctness is not None else False

    # Cost from metrics (logged by Braintrust SDK) or metadata (logged by us)
    cost = metrics.get("cost") or metadata.get("cost")

    # Token data from metadata (logged by us via eval span)
    total_tokens = metadata.get("total_tokens")
    cached_tokens = metadata.get("cached_tokens")

    return BenchmarkMetrics(
        test_id=test_id,
        model=model,
        passed=passed,
        duration=float(duration) if duration is not None else None,
        cost=float(cost) if cost is not None else None,
        tool_call_count=int(tool_calls) if tool_calls is not None else None,
        total_tokens=int(total_tokens) if total_tokens is not None else None,
        cached_tokens=int(cached_tokens) if cached_tokens is not None else None,
    )


def get_benchmark_baseline() -> (
    Tuple[Dict[str, BenchmarkMetrics], HistoricalComparisonDetails]
):
    """Fetch metrics from the latest weekly benchmark run.

    Returns:
        Tuple of (metrics_dict, details)
        - metrics_dict: Maps "test_id:model" to BenchmarkMetrics
        - details: HistoricalComparisonDetails with experiment info
    """
    details = HistoricalComparisonDetails(
        filter_description="latest ci-benchmark experiment on master"
    )

    try:
        api_key = _get_api_key()
        if not api_key:
            details.status = "No Braintrust API key (BRAINTRUST_API_KEY or BRAINTRUST_SERVICE_TOKEN)"
            return {}, details

        project_id = _get_project_id()
        if not project_id:
            details.status = f"Braintrust project '{BRAINTRUST_PROJECT}' not found"
            return {}, details
        details.project_id = project_id

        benchmark_exp = _find_latest_benchmark_experiment(project_id)
        if not benchmark_exp:
            details.status = "No ci-benchmark experiments found"
            return {}, details

        exp_metadata = benchmark_exp.get("metadata") or {}
        exp_info = ExperimentInfo(
            id=benchmark_exp.get("id", ""),
            name=benchmark_exp.get("name", ""),
            branch=exp_metadata.get("branch", "unknown"),
            created=benchmark_exp.get("created"),
        )
        details.experiments.append(exp_info)

        logging.info(
            f"Using benchmark baseline: {exp_info.name} (created {exp_info.created})"
        )

        # Fetch all eval spans from this experiment
        eval_spans = _fetch_all_eval_spans(benchmark_exp["id"])
        if not eval_spans:
            details.status = f"No eval spans found in experiment '{exp_info.name}'"
            return {}, details

        # Build metrics map
        metrics_map: Dict[str, BenchmarkMetrics] = {}
        for span in eval_spans:
            metrics = _extract_metrics(span)
            if metrics is None:
                continue
            key = f"{metrics.test_id}:{metrics.model}"
            metrics_map[key] = metrics

        details.metrics_count = len(metrics_map)
        logging.info(
            f"Loaded {len(metrics_map)} test/model results from benchmark '{exp_info.name}'"
        )
        return metrics_map, details

    except Exception as e:
        tb = traceback.format_exc()
        logging.error(f"Error fetching benchmark baseline: {e}\n{tb}")
        details.status = f"Error: {e}"
        details.errors.append(f"{e}\n{tb}")
        return {}, details


def compare_with_benchmark(
    current_results: List[Dict[str, Any]],
    benchmark: Dict[str, BenchmarkMetrics],
) -> Dict[str, HistoricalComparison]:
    """Compare current test results with benchmark baseline.

    Args:
        current_results: List of current test result dictionaries
        benchmark: Benchmark metrics from get_benchmark_baseline()

    Returns:
        Dict mapping "test_id:model" to HistoricalComparison
    """
    comparisons: Dict[str, HistoricalComparison] = {}

    for result in current_results:
        if result is None:
            continue
        test_id = result.get("test_case_name", result.get("clean_test_case_id", ""))
        model = result.get("model", "")

        if not test_id or not model:
            continue

        key = f"{test_id}:{model}"
        baseline = benchmark.get(key)

        comparison = HistoricalComparison(
            test_id=test_id,
            model=model,
            current_duration=result.get("holmes_duration"),
            current_cost=result.get("cost"),
            current_passed=result.get("passed"),
        )

        if baseline:
            comparison.benchmark_passed = baseline.passed
            comparison.historical_avg_duration = baseline.duration
            comparison.historical_avg_cost = baseline.cost

            # Calculate percentage differences (only for passing tests)
            if comparison.current_duration and baseline.duration:
                comparison.duration_diff_pct = (
                    (comparison.current_duration - baseline.duration)
                    / baseline.duration
                    * 100
                )

            if comparison.current_cost and baseline.cost:
                comparison.cost_diff_pct = (
                    (comparison.current_cost - baseline.cost) / baseline.cost * 100
                )

        comparisons[key] = comparison

    return comparisons

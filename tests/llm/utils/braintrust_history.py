"""Fetch historical data from Braintrust for comparison with current eval results."""

import logging
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests  # type: ignore[import-untyped]

from holmes.core.tracing import (
    BRAINTRUST_API_KEY,
    BRAINTRUST_ORG,
    BRAINTRUST_PROJECT,
    get_active_branch_name,
)

# Braintrust API base URL
BRAINTRUST_API_URL = "https://api.braintrust.dev/v1"

# Number of historical runs to fetch for comparison
DEFAULT_HISTORY_LIMIT = 10


@dataclass
class HistoricalMetrics:
    """Historical metrics for a specific test case."""

    test_id: str
    model: str
    avg_duration: Optional[float] = None
    avg_cost: Optional[float] = None
    avg_turns: Optional[float] = None
    avg_tools: Optional[float] = None
    sample_count: int = 0
    durations: List[float] = field(default_factory=list)
    costs: List[float] = field(default_factory=list)


@dataclass
class HistoricalComparison:
    """Comparison data between current and historical metrics."""

    test_id: str
    model: str
    current_duration: Optional[float] = None
    historical_avg_duration: Optional[float] = None
    duration_diff_pct: Optional[float] = None  # Positive = slower, negative = faster
    current_cost: Optional[float] = None
    historical_avg_cost: Optional[float] = None
    cost_diff_pct: Optional[float] = None
    sample_count: int = 0


@dataclass
class ExperimentInfo:
    """Information about an experiment used for historical comparison."""

    id: str
    name: str
    branch: str
    created: Optional[str] = None


@dataclass
class HistoricalComparisonDetails:
    """Detailed information about the historical comparison."""

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
    """Make an authenticated request to the Braintrust API.

    Args:
        endpoint: API endpoint path (e.g., "/project")
        method: HTTP method
        params: Query parameters
        json_data: JSON body for POST requests

    Returns:
        JSON response or None if request failed
    """
    if not BRAINTRUST_API_KEY:
        logging.debug("Braintrust API key not configured")
        return None

    url = f"{BRAINTRUST_API_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {BRAINTRUST_API_KEY}",
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
            logging.error(f"Unsupported HTTP method: {method}")
            return None

        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.warning(f"Braintrust API request failed: {e}")
        return None


def get_project_id() -> Optional[str]:
    """Get the Braintrust project ID for the configured project.

    Returns:
        Project ID string or None if not found
    """
    # List projects and find the one matching BRAINTRUST_PROJECT
    result = _make_api_request("/project", params={"org_name": BRAINTRUST_ORG})
    if not result or "objects" not in result:
        return None

    for project in result.get("objects", []):
        if project.get("name") == BRAINTRUST_PROJECT:
            return project.get("id")

    return None


def list_historical_experiments(
    project_id: str, limit: int = DEFAULT_HISTORY_LIMIT
) -> tuple[List[Dict[str, Any]], str]:
    """List recent experiments, excluding the current branch.

    Args:
        project_id: Braintrust project ID
        limit: Maximum number of experiments to return

    Returns:
        Tuple of (list of experiment objects, filter description for display)
    """
    current_branch = get_active_branch_name()
    filter_desc = f"excluding branch '{current_branch}'"

    # Fetch experiments for the project
    result = _make_api_request(
        "/experiment",
        params={
            "project_id": project_id,
            "limit": limit * 3,  # Fetch more to filter by branch
        },
    )

    if not result or "objects" not in result:
        return [], filter_desc

    # Filter to exclude current branch
    # Include "Unknown" branches as valid historical data (from before branch tracking fix)
    filtered_experiments = []
    for exp in result.get("objects", []):
        metadata = exp.get("metadata", {})
        branch = metadata.get("branch", "")
        # Exclude experiments from the current branch, but include Unknown branches
        if branch == "Unknown" or (branch and branch != current_branch):
            filtered_experiments.append(exp)
            if len(filtered_experiments) >= limit:
                break

    return filtered_experiments, filter_desc


def fetch_experiment_spans(
    experiment_id: str, limit: int = 1000
) -> List[Dict[str, Any]]:
    """Fetch spans from a specific experiment.

    Args:
        experiment_id: Braintrust experiment ID
        limit: Maximum number of spans to fetch

    Returns:
        List of span objects with metrics
    """
    result = _make_api_request(
        f"/experiment/{experiment_id}/fetch",
        method="POST",
        json_data={
            "limit": limit,
            "filters": [
                # Only fetch top-level eval spans (not nested LLM calls)
                {
                    "type": "span_type",
                    "path": ["span_attributes", "type"],
                    "value": "eval",
                }
            ],
        },
    )

    if not result or "events" not in result:
        return []

    return result.get("events", [])


def extract_span_metrics(span: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract relevant metrics from a span.

    Args:
        span: Braintrust span object

    Returns:
        Dictionary with extracted metrics, or None if span is invalid
    """
    if span is None:
        return None
    metadata = span.get("metadata") or {}
    metrics = span.get("metrics") or {}

    # Extract test_id from metadata or span name
    test_id = metadata.get("eval_id") or metadata.get("test_id", "")
    if not test_id:
        # Try to extract from span name (format: "test_id[model]")
        span_attrs = span.get("span_attributes") or {}
        name = span_attrs.get("name", "")
        if "[" in name:
            test_id = name.split("[")[0]

    scores = span.get("scores") or {}
    return {
        "test_id": test_id,
        "model": metadata.get("model", ""),
        "holmes_duration": metadata.get("holmes_duration"),
        "cost": metrics.get("cost"),
        "tool_call_count": metadata.get("tool_call_count"),
        "passed": int(scores.get("correctness", 0)) == 1,
    }


def build_historical_metrics(
    experiments: List[Dict[str, Any]],
) -> Dict[str, HistoricalMetrics]:
    """Build historical metrics from a list of experiments.

    Args:
        experiments: List of experiment objects

    Returns:
        Dictionary mapping "test_id:model" to HistoricalMetrics
    """
    metrics_map: Dict[str, HistoricalMetrics] = {}

    for exp in experiments:
        exp_id = exp.get("id")
        if not exp_id:
            continue

        spans = fetch_experiment_spans(exp_id)
        for span in spans:
            span_metrics = extract_span_metrics(span)
            if span_metrics is None:
                continue
            test_id = span_metrics.get("test_id", "")
            model = span_metrics.get("model", "")

            if not test_id or not model:
                continue

            # Only include passing tests for fair comparison
            if not span_metrics.get("passed", False):
                continue

            key = f"{test_id}:{model}"
            if key not in metrics_map:
                metrics_map[key] = HistoricalMetrics(test_id=test_id, model=model)

            hist = metrics_map[key]

            # Collect duration
            duration = span_metrics.get("holmes_duration")
            if duration and duration > 0:
                hist.durations.append(duration)

            # Collect cost
            cost = span_metrics.get("cost")
            if cost and cost > 0:
                hist.costs.append(cost)

            hist.sample_count += 1

    # Calculate averages
    for hist in metrics_map.values():
        if hist.durations:
            hist.avg_duration = sum(hist.durations) / len(hist.durations)
        if hist.costs:
            hist.avg_cost = sum(hist.costs) / len(hist.costs)

    return metrics_map


def get_historical_metrics(
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> tuple[Dict[str, HistoricalMetrics], HistoricalComparisonDetails]:
    """Fetch historical metrics from recent experiments (excluding current branch).

    Args:
        limit: Number of recent experiments to analyze

    Returns:
        Tuple of (metrics_dict, details)
        - metrics_dict: Dictionary mapping "test_id:model" to HistoricalMetrics
        - details: HistoricalComparisonDetails with experiment info and status
    """
    details = HistoricalComparisonDetails()

    try:
        if not BRAINTRUST_API_KEY:
            details.status = "BRAINTRUST_API_KEY not configured"
            return {}, details

        project_id = get_project_id()
        if not project_id:
            details.status = f"Braintrust project '{BRAINTRUST_PROJECT}' not found"
            return {}, details

        details.project_id = project_id
        experiments, filter_desc = list_historical_experiments(project_id, limit=limit)
        details.filter_description = filter_desc

        if not experiments:
            details.status = f"No experiments found ({filter_desc})"
            return {}, details

        # Extract experiment info for transparency
        for exp in experiments:
            metadata = exp.get("metadata") or {}
            details.experiments.append(
                ExperimentInfo(
                    id=exp.get("id", ""),
                    name=exp.get("name", ""),
                    branch=metadata.get("branch", "Unknown"),
                    created=exp.get("created"),
                )
            )

        logging.info(
            f"Fetching historical metrics from {len(experiments)} experiments ({filter_desc})"
        )
        metrics = build_historical_metrics(experiments)

        if not metrics:
            details.status = f"No historical metrics found (no passing tests with duration data, {filter_desc})"
            return {}, details

        details.metrics_count = len(metrics)
        return metrics, details
    except Exception as e:
        # Get the full traceback to identify exact location
        tb = traceback.format_exc()
        logging.error(f"Error in get_historical_metrics: {e}\n{tb}")
        details.status = f"Error: {e}"
        details.errors.append(f"{e}\n{tb}")
        return {}, details


def compare_with_historical(
    current_results: List[Dict[str, Any]],
    historical: Dict[str, HistoricalMetrics],
) -> Dict[str, HistoricalComparison]:
    """Compare current test results with historical metrics.

    Args:
        current_results: List of current test result dictionaries
        historical: Historical metrics from get_historical_metrics()

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
        hist = historical.get(key)

        comparison = HistoricalComparison(
            test_id=test_id,
            model=model,
            current_duration=result.get("holmes_duration"),
            current_cost=result.get("cost"),
        )

        if hist and hist.sample_count > 0:
            comparison.historical_avg_duration = hist.avg_duration
            comparison.historical_avg_cost = hist.avg_cost
            comparison.sample_count = hist.sample_count

            # Calculate percentage differences
            if comparison.current_duration and hist.avg_duration:
                comparison.duration_diff_pct = (
                    (comparison.current_duration - hist.avg_duration)
                    / hist.avg_duration
                    * 100
                )

            if comparison.current_cost and hist.avg_cost:
                comparison.cost_diff_pct = (
                    (comparison.current_cost - hist.avg_cost) / hist.avg_cost * 100
                )

        comparisons[key] = comparison

    return comparisons

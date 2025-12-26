#!/usr/bin/env python3
"""Generate markdown report from eval results."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, DefaultDict, Set, Optional
from collections import defaultdict
from urllib.parse import quote
from llm.utils.test_env_vars import (
    CLASSIFIER_MODEL,
    BRAINTRUST_API_KEY,
    BRAINTRUST_ORG,
    BRAINTRUST_PROJECT,
    EXPERIMENT_ID,
    GITHUB_REF_NAME,
    BUILDKITE_BRANCH,
)


def get_model_sort_key(model: str) -> str:
    """Get sort key for a model name.

    Args:
        model: Full model name

    Returns:
        Sort key string for alphabetical sorting
    """
    # Create sorting alias for special cases
    # For gpt-4o, use gpt-4.0-o so it sorts before gpt-4.1 but after gpt-4
    if "gpt-4o" in model.lower():
        sort_alias = model.replace("gpt-4o", "gpt-4.0-o").replace("gpt-4O", "gpt-4.0-o")
    else:
        # Use display name as sort key if no specific sort alias
        # This ensures consistent sorting based on how models are displayed
        sort_alias = get_model_display_name(model)

    return sort_alias.lower()  # Case-insensitive sorting


def get_model_display_name(model_name: str) -> str:
    """Get clean display name for a model.

    Args:
        model_name: Full model name potentially with provider prefix

    Returns:
        Clean display name for the model
    """
    # First strip provider prefixes
    for prefix in ["anthropic/", "openai/", "azure/", "bedrock/", "vertex_ai/"]:
        if model_name.startswith(prefix):
            model_name = model_name[len(prefix) :]

    # Apply display aliasing for specific models
    # Remove redundant "claude-" prefix from Anthropic models
    if model_name.startswith("claude-sonnet-"):
        return model_name.replace("claude-sonnet-", "sonnet-")
    elif model_name.startswith("claude-opus-"):
        return model_name.replace("claude-opus-", "opus-")
    elif model_name.startswith("claude-haiku-"):
        return model_name.replace("claude-haiku-", "haiku-")

    return model_name


def calculate_success_rate(
    passed: int,
    total: int,
    skipped: int = 0,
    setup_failures: int = 0,
    mock_failures: int = 0,
) -> float:
    """Calculate success rate using consistent formula across all metrics.

    Args:
        passed: Number of tests passed
        total: Total number of tests
        skipped: Number of skipped tests
        setup_failures: Number of setup failures
        mock_failures: Number of mock failures

    Returns:
        Success rate percentage (0-100), or 0 if no valid tests
    """
    valid_tests = total - skipped - setup_failures - mock_failures
    if valid_tests > 0:
        return (passed / valid_tests) * 100
    return 0


def get_rate_emoji(rate: float) -> str:
    """Get emoji indicator based on success rate.

    Args:
        rate: Success rate percentage (0-100)

    Returns:
        Emoji string: 🟢 for 100%, 🔴 for 0%, 🟡 for anything in between
    """
    if rate == 100:
        return "🟢"
    elif rate == 0:
        return "🔴"
    else:
        return "🟡"


def format_test_cell(
    passed: int,
    total: int,
    skipped: int = 0,
    setup_failures: int = 0,
    mock_failures: int = 0,
) -> str:
    """Format a test result cell with emoji and percentage.

    Args:
        passed: Number of tests passed
        total: Total number of tests
        skipped: Number of skipped tests
        setup_failures: Number of setup failures
        mock_failures: Number of mock failures

    Returns:
        Formatted string for the table cell
    """
    if total == 0:
        return "N/A"

    # Use the DRY function for consistent calculation
    rate = calculate_success_rate(passed, total, skipped, setup_failures, mock_failures)
    valid_tests = total - skipped - setup_failures - mock_failures

    if valid_tests > 0:
        # Add emoji based on rate
        emoji = get_rate_emoji(rate)

        # Build the main cell: emoji percentage (passed/valid)
        return f"{emoji} {rate:.0f}% ({passed}/{valid_tests})"
    else:
        # All tests were skipped/failed setup
        return "⚪️ -"


def get_test_status_emoji(
    stats: Dict[str, Any],
    total: int,
    passed: int,
    skipped: int,
    setup_failures: int,
) -> str:
    """Determine the appropriate emoji for a test based on its status.

    Args:
        stats: Test statistics dictionary containing tests and failure counts
        total: Total number of tests
        passed: Number of passed tests
        skipped: Number of skipped tests
        setup_failures: Number of setup failures

    Returns:
        Emoji string representing the test status
    """
    # Check for specific error types in user properties
    is_timeout = False
    is_throttled = False
    for test in stats.get("tests", []):
        user_props = test.get("user_properties", [])
        for prop in user_props:
            if isinstance(prop, dict):
                # Check for timeout errors
                error_type = prop.get("error_type", "")
                if "Timeout" in error_type:
                    is_timeout = True
                # Check for throttled status (terminal failure)
                if prop.get("failed_due_to_throttling", False):
                    is_throttled = True
        if is_timeout or is_throttled:
            break

    # Determine emoji based on status priority
    mock_failures = stats.get("mock_failures", 0)
    if mock_failures == total:
        return "🔧"  # All runs had mock data failures
    elif setup_failures == total:
        return "⚠️"  # All runs had setup failures
    elif skipped == total:
        return "⏭️"  # All runs were skipped
    elif (is_throttled or is_timeout) and passed == 0 and total > 0:
        return "⏱️"  # Timeout/throttled error (exceeded time or rate limit)
    else:
        # Calculate success rate using the consistent formula
        rate = calculate_success_rate(
            passed, total, skipped, setup_failures, mock_failures
        )
        return get_rate_emoji(rate)


def extract_experiment_name_from_results(results: Dict[str, Any]) -> Optional[str]:
    """Extract experiment name from test results if available.

    Args:
        results: Test results dictionary

    Returns:
        Experiment name string, or None if not found
    """
    for test in results.get("tests", []):
        user_props = test.get("user_properties", [])
        for prop in user_props:
            if isinstance(prop, dict) and "braintrust_experiment" in prop:
                return prop["braintrust_experiment"]
    return None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate markdown report from eval results"
    )
    parser.add_argument("--json-file", required=True, help="Path to JSON results file")
    parser.add_argument(
        "--output-file", required=True, help="Path to output markdown file"
    )
    parser.add_argument(
        "--models",
        help="Comma-separated list of models tested (auto-detected if not provided)",
    )
    return parser.parse_args()


def _get_braintrust_base_url(
    experiment_name: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    """Get base Braintrust URL components.

    Returns:
        Tuple of (base_url, experiment_name) or None if Braintrust not configured
    """
    if not BRAINTRUST_API_KEY:
        return None

    if not experiment_name:
        branch = GITHUB_REF_NAME or BUILDKITE_BRANCH or "unknown"
        experiment_name = EXPERIMENT_ID or f"holmes-benchmark-{branch}"

    encoded_experiment_name = quote(experiment_name, safe="")
    base_url = f"https://www.braintrust.dev/app/{BRAINTRUST_ORG}/p/{BRAINTRUST_PROJECT}/experiments/{encoded_experiment_name}"

    return base_url, experiment_name


def _encode_braintrust_filter(filter_obj: dict) -> str:
    """Double-encode a filter object for Braintrust URLs.

    Args:
        filter_obj: Filter object with 'filter' key containing array of filter specs

    Returns:
        URL-encoded filter string
    """
    # First encode the inner text and label fields
    filter_obj["filter"][0]["text"] = quote(filter_obj["filter"][0]["text"], safe="")
    filter_obj["filter"][0]["label"] = quote(filter_obj["filter"][0]["label"], safe="")

    # Then encode the whole JSON
    filter_json = json.dumps(filter_obj)
    return quote(filter_json, safe="")


def get_braintrust_filter_url(
    eval_case: str, model: str, experiment_name: Optional[str] = None
) -> Optional[str]:
    """Generate Braintrust URL with search filter for a specific eval/model combination.

    Args:
        eval_case: The test case ID (e.g., "01_how_many_pods")
        model: The model name (e.g., "gpt-4o")
        experiment_name: Optional experiment name, will be auto-detected if not provided

    Returns:
        Braintrust URL string with search filter, or None if Braintrust not configured
    """
    base_result = _get_braintrust_base_url(experiment_name)
    if not base_result:
        return None

    base_url, _ = base_result

    # Create the filter string for span_attributes.name
    # The name format in Braintrust is typically: "test_case[model]"
    span_name = f"{eval_case}[{model}]"

    # Build the filter JSON structure
    filter_obj = {
        "filter": [
            {
                "text": f'span_attributes.name = "{span_name}"',
                "label": f"Name equals {span_name}",
                "originType": "form",
            }
        ]
    }

    encoded_filter = _encode_braintrust_filter(filter_obj)
    return f"{base_url}?c=&search={encoded_filter}"


def get_braintrust_tag_filter_url(
    tag: str, experiment_name: Optional[str] = None
) -> Optional[str]:
    """Generate Braintrust URL with search filter for all tests with a specific tag.

    Args:
        tag: The tag name (e.g., "logs", "easy")
        experiment_name: Optional experiment name, will be auto-detected if not provided

    Returns:
        Braintrust URL string with search filter, or None if Braintrust not configured
    """
    base_result = _get_braintrust_base_url(experiment_name)
    if not base_result:
        return None

    base_url, _ = base_result

    # Build the filter JSON structure for tags
    filter_obj = {
        "filter": [
            {
                "text": f'tags includes ["{tag}"]',
                "label": f"Tags includes {tag}",
                "originType": "form",
            }
        ]
    }

    encoded_filter = _encode_braintrust_filter(filter_obj)
    return f"{base_url}?c=&search={encoded_filter}"


def get_braintrust_eval_filter_url(
    eval_id: str, experiment_name: Optional[str] = None
) -> Optional[str]:
    """Generate Braintrust URL with search filter for all tests of a specific eval.

    Args:
        eval_id: The eval/test case ID (e.g., "01_how_many_pods")
        experiment_name: Optional experiment name, will be auto-detected if not provided

    Returns:
        Braintrust URL string with search filter, or None if Braintrust not configured
    """
    base_result = _get_braintrust_base_url(experiment_name)
    if not base_result:
        return None

    base_url, _ = base_result

    # Build the filter JSON structure for metadata.eval_id
    filter_obj = {
        "filter": [
            {
                "text": f'metadata.eval_id = "{eval_id}"',
                "label": f"metadata.eval_id equals {eval_id}",
                "originType": "form",
            }
        ]
    }

    encoded_filter = _encode_braintrust_filter(filter_obj)
    return f"{base_url}?c=&search={encoded_filter}"


def get_braintrust_model_filter_url(
    model: str, experiment_name: Optional[str] = None
) -> Optional[str]:
    """Generate Braintrust URL with search filter for all tests of a specific model.

    Args:
        model: The model name (e.g., "gpt-4o")
        experiment_name: Optional experiment name, will be auto-detected if not provided

    Returns:
        Braintrust URL string with search filter, or None if Braintrust not configured
    """
    base_result = _get_braintrust_base_url(experiment_name)
    if not base_result:
        return None

    base_url, _ = base_result

    # Build the filter JSON structure for metadata.model
    filter_obj = {
        "filter": [
            {
                "text": f'metadata.model = "{model}"',
                "label": f"metadata.model equals {model}",
                "originType": "form",
            }
        ]
    }

    encoded_filter = _encode_braintrust_filter(filter_obj)
    return f"{base_url}?c=&search={encoded_filter}"


def load_results(json_file: Path) -> Dict[str, Any]:
    """Load results from pytest JSON report or custom format."""
    with open(json_file, "r") as f:
        data = json.load(f)

    # Handle different JSON formats
    if "tests" in data:
        # pytest-json-report format
        return parse_pytest_json(data)
    else:
        # Custom format from our reporter
        return data


def extract_models_from_results(results: Dict[str, Any]) -> List[str]:
    """Extract unique model names from test results."""
    models = set()

    for test in results.get("tests", []):
        # Skip deselected tests
        if test.get("outcome") == "deselected":
            continue

        # Try to get model from user_properties
        user_props = test.get("user_properties", [])
        for prop in user_props:
            if isinstance(prop, dict) and "model" in prop:
                models.add(prop["model"])
                break
        else:
            # Fallback to extracting from nodeid
            nodeid = test.get("nodeid", "")
            if "[" in nodeid and "-" in nodeid:
                # Extract model from pattern like [test_case-model]
                params = nodeid.split("[")[1].split("]")[0]
                parts = params.split("-")
                if len(parts) >= 2:
                    model = "-".join(parts[1:])
                    models.add(model)

    return sorted(list(models))


def parse_pytest_json(data: Dict) -> Dict[str, Any]:
    """Parse pytest-json-report format."""
    results = {
        "summary": {
            "total": data["summary"]["total"],
            "passed": data["summary"].get("passed", 0),
            "failed": data["summary"].get("failed", 0),
            "skipped": data["summary"].get("skipped", 0),
        },
        "tests": [],
        "duration": data.get("duration", 0),
    }

    for test in data.get("tests", []):
        test_result = {
            "nodeid": test["nodeid"],
            "outcome": test["outcome"],
            "duration": test.get("duration", 0),
            "user_properties": test.get(
                "user_properties", []
            ),  # Include user_properties
            "call": test.get("call"),  # Include call for duration info
        }

        # Extract test case name and model from nodeid
        if "[" in test["nodeid"] and "]" in test["nodeid"]:
            params = test["nodeid"].split("[")[1].split("]")[0]
            # Parse out test case and model
            parts = params.split("-")
            if len(parts) >= 2:
                test_result["test_case"] = parts[0]
                test_result["model"] = "-".join(parts[1:])
            else:
                test_result["test_case"] = params
                test_result["model"] = "unknown"

        results["tests"].append(test_result)

    return results


def generate_summary_table(results: Dict[str, Any]) -> str:
    """Generate summary table by model."""
    model_stats: DefaultDict[str, Dict[str, int]] = defaultdict(
        lambda: {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "setup_failures": 0,
            "mock_failures": 0,
        }
    )

    for test in results.get("tests", []):
        # Skip deselected tests
        if test.get("outcome") == "deselected":
            continue

        # Extract model and failure types from user_properties
        model = None
        is_setup_failure = False
        is_mock_failure = False

        user_props = test.get("user_properties", [])
        for prop in user_props:
            if isinstance(prop, dict):
                if "model" in prop:
                    model = prop["model"]
                if prop.get("is_setup_failure"):
                    is_setup_failure = True
                if prop.get("mock_data_failure"):
                    is_mock_failure = True

        # Fallback to test dict if not in user_properties
        if not model:
            model = test.get("model", "unknown")

        outcome = test.get("outcome", "unknown")

        model_stats[model]["total"] += 1
        if outcome == "passed":
            model_stats[model]["passed"] += 1
        elif outcome == "failed":
            model_stats[model]["failed"] += 1
        elif outcome == "skipped":
            model_stats[model]["skipped"] += 1

        # Track setup and mock failures
        if is_setup_failure:
            model_stats[model]["setup_failures"] += 1
        if is_mock_failure:
            model_stats[model]["mock_failures"] += 1

    # Build markdown table
    lines = []
    lines.append("| Model | Pass | Fail | Skip/Error | Total | Success Rate |")
    lines.append("|-------|------|------|------------|-------|--------------|")

    for model in sorted(model_stats.keys(), key=get_model_sort_key):
        stats = model_stats[model]
        total = stats["total"]
        passed = stats["passed"]
        failed = stats["failed"]
        skipped = stats["skipped"]
        setup_failures = stats.get("setup_failures", 0)
        mock_failures = stats.get("mock_failures", 0)

        # Calculate real failures (failures that are not setup or mock failures)
        real_failures = failed - setup_failures - mock_failures

        # Calculate total skip/error (skipped + setup failures + mock failures)
        skip_error_total = skipped + setup_failures + mock_failures

        # Calculate success rate using consistent formula
        # Valid tests = total - skipped - setup_failures - mock_failures
        success_rate = calculate_success_rate(
            passed, total, skipped, setup_failures, mock_failures
        )

        # Get clean display name
        display_model = get_model_display_name(model)

        # Add emoji indicator based on success rate
        indicator = get_rate_emoji(success_rate)

        # Calculate valid tests for display
        valid_tests = total - skip_error_total

        lines.append(
            f"| {display_model} | {passed} | {real_failures} | {skip_error_total} | {total} | "
            f"{indicator} {success_rate:.0f}% ({passed}/{valid_tests}) |"
        )

    return "\n".join(lines)


def generate_eval_dashboard_heatmap(results: Dict[str, Any]) -> str:
    """Generate a heatmap dashboard showing each eval x model with color-coded pass rates."""
    # Collect data by eval test and model, including test references for Braintrust links
    eval_model_stats: DefaultDict[str, DefaultDict[str, Dict[str, Any]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "total": 0,
                "passed": 0,
                "tests": [],
                "total_duration": 0,
                "total_cost": 0,
                "setup_failures": 0,
                "skipped": 0,
            }
        )
    )

    all_evals: Set[str] = set()
    all_models: Set[str] = set()

    for test in results.get("tests", []):
        # Skip deselected tests
        if test.get("outcome") == "deselected":
            continue

        # Extract eval test case and model
        eval_case = None
        model = None

        # Try to get from user_properties first
        user_props = test.get("user_properties", [])
        for prop in user_props:
            if isinstance(prop, dict):
                if "clean_test_case_id" in prop:
                    eval_case = prop["clean_test_case_id"]
                if "model" in prop:
                    model = prop["model"]

        # Fallback to nodeid parsing
        if not eval_case or not model:
            nodeid = test.get("nodeid", "")
            if "[" in nodeid and "]" in nodeid:
                params = nodeid.split("[")[1].split("]")[0]
                parts = params.split("-")
                if not eval_case and len(parts) >= 1:
                    eval_case = parts[0]
                if not model and len(parts) >= 2:
                    model = "-".join(parts[1:])

        if eval_case and model:
            all_evals.add(eval_case)
            all_models.add(model)
            outcome = test.get("outcome", "unknown")

            eval_model_stats[eval_case][model]["total"] += 1
            if outcome == "passed":
                eval_model_stats[eval_case][model]["passed"] += 1
            elif outcome == "skipped":
                eval_model_stats[eval_case][model]["skipped"] += 1

            # Check for setup failures and mock failures in user_properties
            is_setup_failure = False
            is_mock_failure = False
            for prop in user_props:
                if isinstance(prop, dict):
                    if prop.get("is_setup_failure"):
                        is_setup_failure = True
                    if prop.get("mock_data_failure"):
                        is_mock_failure = True
            if is_setup_failure:
                eval_model_stats[eval_case][model]["setup_failures"] += 1
            if is_mock_failure:
                if "mock_failures" not in eval_model_stats[eval_case][model]:
                    eval_model_stats[eval_case][model]["mock_failures"] = 0
                eval_model_stats[eval_case][model]["mock_failures"] += 1

            # Add duration if available
            duration = (
                test.get("call", {}).get("duration", 0) if test.get("call") else 0
            )
            if duration > 0:
                eval_model_stats[eval_case][model]["total_duration"] += duration

            # Add cost if available (from user_properties)
            cost = 0
            for prop in user_props:
                if isinstance(prop, dict) and "cost" in prop:
                    cost = prop["cost"]
                    break
            if cost > 0:
                eval_model_stats[eval_case][model]["total_cost"] += cost

            # Store the test data for potential Braintrust link
            eval_model_stats[eval_case][model]["tests"].append(test)

    if not all_evals or not all_models:
        return ""

    # Build the dashboard heatmap
    lines = []
    lines.append("## Raw Results")
    lines.append("")
    lines.append("Status of all evaluations across models. Color coding:")
    lines.append("")
    lines.append("- 🟢 Passing 100% (stable)")
    lines.append("- 🟡 Passing 1-99%")
    lines.append("- 🔴 Passing 0% (failing)")
    lines.append("- 🔧 Mock data failure (missing or invalid test data)")
    lines.append("- ⚠️ Setup failure (environment/infrastructure issue)")
    lines.append("- ⏱️ Timeout or rate limit error")
    lines.append("- ⏭️ Test skipped (e.g., known issue or precondition not met)")
    lines.append("")

    # Sort evals alphabetically by name
    sorted_evals = sorted(all_evals)

    # Sort models using shared sorting function
    sorted_models = sorted(all_models, key=get_model_sort_key)

    # Extract experiment name from any test that has it (needed for URLs)
    # We need to extract from the actual test data stored in eval_model_stats
    experiment_name = None
    for eval_case in sorted_evals:
        for model in sorted_models:
            stats = eval_model_stats[eval_case][model]
            if stats.get("tests"):
                for test in stats["tests"]:
                    user_props = test.get("user_properties", [])
                    for prop in user_props:
                        if isinstance(prop, dict) and "braintrust_experiment" in prop:
                            experiment_name = prop["braintrust_experiment"]
                            break
                    if experiment_name:
                        break
            if experiment_name:
                break
        if experiment_name:
            break

    # Format model names for display (strip provider prefix) and create links
    display_model_headers = []
    for model in sorted_models:
        # Get clean display name
        display_model = get_model_display_name(model)

        # Create link to filter by this model
        model_filter_url = get_braintrust_model_filter_url(model, experiment_name)
        if model_filter_url:
            display_model_headers.append(f"[{display_model}]({model_filter_url})")
        else:
            display_model_headers.append(display_model)

    # Create table header
    header = "| Eval ID | " + " | ".join(display_model_headers) + " |"
    separator = "|---------|" + "|".join(["-------"] * len(sorted_models)) + "|"

    lines.append(header)
    lines.append(separator)

    # Data rows (one per eval)
    for eval_case in sorted_evals:
        # Create absolute GitHub URL to test_case.yaml file
        github_url = f"https://github.com/HolmesGPT/holmesgpt/blob/master/tests/llm/fixtures/test_ask_holmes/{eval_case}/test_case.yaml"

        # Create link for Braintrust
        eval_filter_url = get_braintrust_eval_filter_url(eval_case, experiment_name)

        # Create cell with both GitHub link and Braintrust link
        if eval_filter_url:
            row = [f"[**{eval_case}**]({github_url}) [🔗]({eval_filter_url})"]
        else:
            row = [f"[**{eval_case}**]({github_url})"]

        for model in sorted_models:
            stats = eval_model_stats[eval_case][model]
            if stats["total"] > 0:
                passed = stats["passed"]
                total = stats["total"]
                skipped = stats["skipped"]
                setup_failures = stats["setup_failures"]

                # Calculate rate using consistent formula
                mock_failures = stats.get("mock_failures", 0)
                rate = calculate_success_rate(
                    passed, total, skipped, setup_failures, mock_failures
                )
                valid_tests = total - skipped - setup_failures - mock_failures

                # Get Braintrust filter URL for this eval/model combination
                braintrust_url = get_braintrust_filter_url(
                    eval_case, model, experiment_name
                )

                # Determine emoji based on status
                emoji = get_test_status_emoji(
                    stats, total, passed, skipped, setup_failures
                )

                # Create cell with link if available
                if braintrust_url:
                    cell = f"[{emoji}]({braintrust_url})"
                else:
                    cell = emoji
            else:
                cell = "⬜"  # No data

            row.append(cell)

        lines.append("| " + " | ".join(row) + " |")

    # Add summary row (no separator needed in markdown tables)
    summary_row = ["**SUMMARY**"]

    for model in sorted_models:
        total_passed = 0
        total_tests = 0
        total_skipped = 0
        total_setup_failures = 0
        for eval_case in sorted_evals:
            stats = eval_model_stats[eval_case][model]
            total_passed += stats["passed"]
            total_tests += stats["total"]
            total_skipped += stats.get("skipped", 0)
            total_setup_failures += stats.get("setup_failures", 0)

        if total_tests > 0:
            # Calculate rate using consistent formula (include mock failures)
            total_mock_failures = 0
            for eval_case in sorted_evals:
                total_mock_failures += eval_model_stats[eval_case][model].get(
                    "mock_failures", 0
                )

            overall_rate = calculate_success_rate(
                total_passed,
                total_tests,
                total_skipped,
                total_setup_failures,
                total_mock_failures,
            )

            # Calculate valid tests for the count
            valid_tests = (
                total_tests - total_skipped - total_setup_failures - total_mock_failures
            )

            # Create summary text with emoji and count
            emoji = get_rate_emoji(overall_rate)
            cell = f"{emoji} {overall_rate:.0f}% ({total_passed}/{valid_tests})"
        else:
            cell = "N/A"

        summary_row.append(cell)

    lines.append("| " + " | ".join(summary_row) + " |")

    # Add detailed breakdown for reference
    lines.append("")
    lines.append("## Detailed Raw Results")
    lines.append("")
    # Create display model names for the detailed table
    display_models_detail = []
    for model in sorted_models:
        display_model = get_model_display_name(model)
        display_models_detail.append(display_model)

    lines.append("| Eval ID | " + " | ".join(display_models_detail) + " |")
    lines.append("|---------|" + "|".join(["-------"] * len(sorted_models)) + "|")

    for eval_case in sorted_evals:
        # Create absolute GitHub URL to test_case.yaml file
        github_url = f"https://github.com/HolmesGPT/holmesgpt/blob/master/tests/llm/fixtures/test_ask_holmes/{eval_case}/test_case.yaml"

        # Create link for Braintrust in detailed breakdown
        eval_filter_url = get_braintrust_eval_filter_url(eval_case, experiment_name)

        # Create cell with both GitHub link and Braintrust link
        if eval_filter_url:
            row = [f"[{eval_case}]({github_url}) [🔗]({eval_filter_url})"]
        else:
            row = [f"[{eval_case}]({github_url})"]

        for model in sorted_models:
            stats = eval_model_stats[eval_case][model]
            if stats["total"] > 0:
                passed = stats["passed"]
                total = stats["total"]
                skipped = stats.get("skipped", 0)
                setup_failures = stats.get("setup_failures", 0)

                # Calculate rate using consistent formula
                mock_failures = stats.get("mock_failures", 0)
                rate = calculate_success_rate(
                    passed, total, skipped, setup_failures, mock_failures
                )
                valid_tests = total - skipped - setup_failures - mock_failures

                # Calculate average duration and cost if available
                avg_duration = (
                    stats["total_duration"] / valid_tests
                    if stats["total_duration"] > 0 and valid_tests > 0
                    else 0
                )
                avg_cost = (
                    stats["total_cost"] / valid_tests
                    if stats.get("total_cost", 0) > 0 and valid_tests > 0
                    else 0
                )

                # Get Braintrust filter URL for this eval/model combination
                braintrust_url = get_braintrust_filter_url(
                    eval_case, model, experiment_name
                )

                # Determine emoji based on status - should match the main table
                emoji = get_test_status_emoji(
                    stats, total, passed, skipped, setup_failures
                )

                # Build the cell parts with simplified format
                if valid_tests > 0:
                    # Show percentage with passed/valid in parentheses
                    status_line = f"{emoji} {rate:.0f}% ({passed}/{valid_tests})"
                else:
                    # All tests were skipped/failed setup
                    status_line = "⚪️ -"

                # Add Braintrust link if available
                if braintrust_url:
                    cell_parts = [f"[{status_line}]({braintrust_url})"]
                else:
                    cell_parts = [status_line]
                if avg_duration > 0:
                    cell_parts.append(f"⏱️ {avg_duration:.1f}s")
                if avg_cost > 0:
                    cell_parts.append(f"💰 ${avg_cost:.2f}")

                cell = " / ".join(cell_parts)
            else:
                cell = "-"

            row.append(cell)

        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def generate_model_by_tag_table(results: Dict[str, Any]) -> str:
    """Generate a table showing model performance by tag."""
    # Collect data by model and tag
    model_tag_stats: DefaultDict[str, DefaultDict[str, Dict[str, Any]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "total": 0,
                "passed": 0,
                "skipped": 0,
                "setup_failures": 0,
                "mock_failures": 0,
            }
        )
    )

    # Track unique tests per model for deduplication in Overall row
    model_unique_tests: DefaultDict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)

    all_tags: Set[str] = set()
    all_models: Set[str] = set()

    for test in results.get("tests", []):
        # Skip deselected tests
        if test.get("outcome") == "deselected":
            continue

        # Extract model and tags from user_properties
        model = None
        tags = []
        is_setup_failure = False
        is_mock_failure = False

        user_props = test.get("user_properties", [])

        for prop in user_props:
            if isinstance(prop, dict):
                if "model" in prop:
                    model = prop["model"]
                if "tags" in prop:
                    tags = prop["tags"]
                if prop.get("is_setup_failure"):
                    is_setup_failure = True
                if prop.get("mock_data_failure"):
                    is_mock_failure = True

        if not model:
            # Fallback: try to extract from nodeid
            nodeid = test.get("nodeid", "")
            if "-" in nodeid:
                model = nodeid.split("-")[-1].rstrip("]")

        if model and tags:
            all_models.add(model)
            outcome = test.get("outcome", "unknown")

            # Generate a unique test identifier (using nodeid as unique key)
            test_id = test.get("nodeid", "")

            # Store test info for deduplication
            if test_id not in model_unique_tests[model]:
                model_unique_tests[model][test_id] = {
                    "outcome": outcome,
                    "is_setup_failure": is_setup_failure,
                    "is_mock_failure": is_mock_failure,
                }

            for tag in tags:
                all_tags.add(tag)
                model_tag_stats[model][tag]["total"] += 1
                if outcome == "passed":
                    model_tag_stats[model][tag]["passed"] += 1
                elif outcome == "skipped":
                    model_tag_stats[model][tag]["skipped"] += 1

                if is_setup_failure:
                    model_tag_stats[model][tag]["setup_failures"] += 1
                if is_mock_failure:
                    model_tag_stats[model][tag]["mock_failures"] += 1

    if not all_tags or not all_models:
        return ""

    # Extract experiment name from any test that has it (needed for URLs)
    experiment_name = extract_experiment_name_from_results(results)

    # Build the table with tags as rows and models as columns
    lines = []
    lines.append("## Performance by Tag")
    lines.append("")
    lines.append("Success rate by test category and model:")
    lines.append("")

    # Header with models as columns
    sorted_models = sorted(all_models, key=get_model_sort_key)
    sorted_tags = sorted(all_tags)

    # Strip provider prefixes for cleaner display
    display_models = []
    for model in sorted_models:
        display_model = get_model_display_name(model)
        display_models.append(display_model)

    header = "| Tag | " + " | ".join(display_models) + " | Warnings |"
    separator = "|-----|" + "|".join(["-------"] * len(sorted_models)) + "|----------|"

    lines.append(header)
    lines.append(separator)

    # Data rows (one per tag)
    for tag in sorted_tags:
        # Create tag link if available
        tag_filter_url = get_braintrust_tag_filter_url(tag, experiment_name)
        if tag_filter_url:
            row = [f"[{tag}]({tag_filter_url})"]
        else:
            row = [tag]

        # Collect totals for warnings across all models
        total_skipped_all = 0

        for model in sorted_models:
            stats = model_tag_stats[model][tag]
            cell = format_test_cell(
                passed=stats["passed"],
                total=stats["total"],
                skipped=stats.get("skipped", 0),
                setup_failures=stats.get("setup_failures", 0),
                mock_failures=stats.get("mock_failures", 0),
            )
            row.append(cell)

            # Accumulate all types of skipped tests
            total_skipped_all += (
                stats.get("skipped", 0)
                + stats.get("setup_failures", 0)
                + stats.get("mock_failures", 0)
            )

        # Build simplified warnings cell
        warnings_cell = (
            f"⚠️ {total_skipped_all} skipped" if total_skipped_all > 0 else ""
        )
        row.append(warnings_cell)

        lines.append("| " + " | ".join(row) + " |")

    # Add Overall row - use deduplicated counts from unique tests
    overall_row = ["**Overall**"]
    grand_total_skipped_all = 0

    for model in sorted_models:
        # Calculate from unique tests to avoid double-counting
        total_passed = 0
        total_tests = 0
        total_skipped = 0
        total_setup_failures = 0
        total_mock_failures = 0

        # Count unique tests per model
        for test_id, test_info in model_unique_tests[model].items():
            total_tests += 1
            if test_info["outcome"] == "passed":
                total_passed += 1
            elif test_info["outcome"] == "skipped":
                total_skipped += 1

            if test_info["is_setup_failure"]:
                total_setup_failures += 1
            if test_info["is_mock_failure"]:
                total_mock_failures += 1

        cell = format_test_cell(
            passed=total_passed,
            total=total_tests,
            skipped=total_skipped,
            setup_failures=total_setup_failures,
            mock_failures=total_mock_failures,
        )
        overall_row.append(cell)

        # Accumulate all types of skipped tests
        grand_total_skipped_all += (
            total_skipped + total_setup_failures + total_mock_failures
        )

    # Build simplified warnings for overall row
    warnings_cell = (
        f"⚠️ {grand_total_skipped_all} skipped" if grand_total_skipped_all > 0 else ""
    )
    overall_row.append(warnings_cell)

    lines.append("| " + " | ".join(overall_row) + " |")

    return "\n".join(lines)


def generate_cost_comparison_table(results: Dict[str, Any]) -> str:
    """Generate model cost comparison table."""
    model_costs: DefaultDict[str, List[float]] = defaultdict(list)

    for test in results.get("tests", []):
        # Skip deselected tests
        if test.get("outcome") == "deselected":
            continue

        # Extract model and cost from user_properties
        model = None
        cost = None

        user_props = test.get("user_properties", [])
        for prop in user_props:
            if isinstance(prop, dict):
                if "model" in prop:
                    model = prop["model"]
                if "cost" in prop:
                    cost = prop["cost"]

        # Fallback: try to extract model from nodeid if needed
        if not model:
            nodeid = test.get("nodeid", "")
            if "-" in nodeid:
                model = nodeid.split("-")[-1].rstrip("]")

        if model and cost is not None and cost > 0:
            model_costs[model].append(cost)

    if not model_costs:
        return ""

    # Build markdown table
    lines = []
    lines.append("## Model Cost Comparison")
    lines.append("")
    lines.append("| Model | Tests | Avg Cost | Min Cost | Max Cost | Total Cost |")
    lines.append("|-------|-------|----------|----------|----------|------------|")

    for model in sorted(model_costs.keys(), key=get_model_sort_key):
        costs = model_costs[model]
        if not costs:
            continue

        avg_cost = sum(costs) / len(costs)
        min_cost = min(costs)
        max_cost = max(costs)
        total_cost = sum(costs)
        num_tests = len(costs)

        # Get clean display name
        display_model = get_model_display_name(model)

        lines.append(
            f"| {display_model} | {num_tests} | ${avg_cost:.2f} | ${min_cost:.2f} | "
            f"${max_cost:.2f} | ${total_cost:.2f} |"
        )

    return "\n".join(lines)


def generate_latency_comparison_table(results: Dict[str, Any]) -> str:
    """Generate model latency comparison table."""
    model_timings: DefaultDict[str, List[float]] = defaultdict(list)
    model_excluded_counts: DefaultDict[str, int] = defaultdict(
        int
    )  # Track excluded tests per model

    for test in results.get("tests", []):
        # Skip deselected tests
        if test.get("outcome") == "deselected":
            continue

        # Skip tests that weren't actually executed (skipped, setup failures)
        outcome = test.get("outcome", "")
        if outcome in ["skipped"]:
            continue

        # Extract model and check for various failure types
        model = None
        is_setup_failure = False
        encountered_throttling = False  # Use new flag for ANY throttling
        has_timeout_error = False

        user_props = test.get("user_properties", [])
        for prop in user_props:
            if isinstance(prop, dict):
                if "model" in prop:
                    model = prop["model"]
                if prop.get("is_setup_failure", False):
                    is_setup_failure = True
                # Check for ANY throttling during execution
                if prop.get("encountered_throttling", False):
                    encountered_throttling = True
                # Also check terminal failure flag
                if prop.get("failed_due_to_throttling", False):
                    encountered_throttling = True
                # Check for timeout errors
                error_type = prop.get("error_type", "")
                if "Timeout" in error_type:
                    has_timeout_error = True

        # Skip if this was a setup failure, had ANY throttling (even if succeeded), or had timeout errors
        # This ensures accurate latency measurements exclude tests with retry delays
        if is_setup_failure or encountered_throttling or has_timeout_error:
            if model and (encountered_throttling or has_timeout_error):
                model_excluded_counts[model] += 1
            continue

        # Extract duration from test data
        duration = test.get("call", {}).get("duration", 0) if test.get("call") else 0

        if not model:
            # Fallback: try to extract from nodeid
            nodeid = test.get("nodeid", "")
            if "-" in nodeid:
                model = nodeid.split("-")[-1].rstrip("]")

        # Only include tests with valid duration (> 0)
        if model and duration > 0:
            model_timings[model].append(duration)

    if not model_timings:
        return ""

    # Build markdown table
    lines = []
    lines.append("## Model Latency Comparison")
    lines.append("")
    lines.append("| Model | Avg (s) | Min (s) | Max (s) | P50 (s) | P95 (s) |")
    lines.append("|-------|---------|---------|---------|---------|---------|")

    for model in sorted(model_timings.keys(), key=get_model_sort_key):
        timings = sorted(model_timings[model])
        if not timings:
            continue

        avg_time = sum(timings) / len(timings)
        min_time = timings[0]
        max_time = timings[-1]

        # Calculate percentiles
        p50_idx = int(len(timings) * 0.5)
        p95_idx = int(len(timings) * 0.95)
        p50 = timings[p50_idx] if p50_idx < len(timings) else timings[-1]
        p95 = timings[p95_idx] if p95_idx < len(timings) else timings[-1]

        # Get clean display name
        display_model = get_model_display_name(model)

        lines.append(
            f"| {display_model} | {avg_time:.1f} | {min_time:.1f} | "
            f"{max_time:.1f} | {p50:.1f} | {p95:.1f} |"
        )

    # Add warning about excluded tests if any - after the table
    total_excluded = sum(model_excluded_counts.values())
    if total_excluded > 0:
        excluded_details = []
        for model in sorted(model_excluded_counts.keys(), key=get_model_sort_key):
            if model_excluded_counts[model] > 0:
                display_model = get_model_display_name(model)
                excluded_details.append(
                    f"{display_model}: {model_excluded_counts[model]}"
                )

        lines.append("")
        lines.append(
            f"⚠️ **Note:** {total_excluded} test(s) excluded from latency calculations due to throttling/timeout errors ({', '.join(excluded_details)})"
        )

    return "\n".join(lines)


def main():
    args = parse_args()

    # Load results
    results = load_results(Path(args.json_file))

    # Parse models - auto-detect from results if not provided
    if args.models:
        models = args.models.split(",")
    else:
        models = extract_models_from_results(results)
        if models:
            print(f"Auto-detected models: {', '.join(models)}")

    # Generate report sections
    report_lines = []

    # Header - check if output file is in history format
    output_filename = Path(args.output_file).name
    import re

    match = re.match(
        r"results_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\.md", output_filename
    )
    if match:
        # Extract date components and format as title
        year, month, day, hour, minute, second = match.groups()
        date_obj = datetime(int(year), int(month), int(day))
        # Format as "Month Day, Year" (no time)
        title = date_obj.strftime("%B %d, %Y")
        report_lines.append(f"# {title}")
    else:
        # Default title for latest-results.md
        report_lines.append("# HolmesGPT LLM Evaluation Benchmark Results")
    report_lines.append("")
    # Format duration nicely
    duration_seconds = results.get("duration", 0)
    hours = int(duration_seconds // 3600)
    minutes = int((duration_seconds % 3600) // 60)
    seconds = int(duration_seconds % 60)

    if hours > 0:
        pretty_duration = f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        pretty_duration = f"{minutes}m {seconds}s"
    else:
        pretty_duration = f"{seconds}s"

    # Get classifier model from environment or default
    classifier_model = CLASSIFIER_MODEL or "gpt-4o"

    # Calculate number of iterations
    test_case_iterations = defaultdict(set)
    for test in results.get("tests", []):
        # Skip deselected tests
        if test.get("outcome") == "deselected":
            continue

        # Extract test case name and iteration from nodeid
        nodeid = test.get("nodeid", "")
        if "[" in nodeid and "]" in nodeid:
            # Extract the parametrized part between brackets
            param_part = nodeid.split("[")[1].split("]")[0]

            # Parse test case and iteration number
            # Format is: test_case_nameN-model_name
            # where N is the iteration number appended directly to the test case name
            # Example: 01_how_many_pods0-gpt-4.1 or 01_how_many_pods1-anthropic/claude-sonnet-4-20250514

            # Find the first dash followed by a known model prefix
            parts = param_part.split("-")
            test_and_iter = None
            for i, part in enumerate(parts):
                if i > 0 and (
                    part.startswith("gpt")
                    or part.startswith("anthropic")
                    or part.startswith("claude")
                    or part.startswith("azure")
                    or part in ["o1", "o3"]
                ):
                    # Found the model start, everything before is test_and_iter
                    test_and_iter = "-".join(parts[:i])
                    break

            if test_and_iter:
                # Extract iteration number from end of test case name
                iteration = 0
                if test_and_iter and test_and_iter[-1].isdigit():
                    # Find where the iteration number starts
                    i = len(test_and_iter) - 1
                    while i >= 0 and test_and_iter[i].isdigit():
                        i -= 1
                    test_case = test_and_iter[: i + 1]
                    iteration = int(test_and_iter[i + 1 :])
                else:
                    test_case = test_and_iter

                test_case_iterations[test_case].add(iteration)

    # Calculate maximum iterations across all test cases
    num_iterations = max(
        (max(iterations) + 1 for iterations in test_case_iterations.values()), default=1
    )

    report_lines.append(
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}  "
    )
    report_lines.append(f"**Total Duration**: {pretty_duration}  ")
    report_lines.append(f"**Iterations**: {num_iterations}  ")
    report_lines.append(f"**Judge (classifier) model**: {classifier_model}")
    report_lines.append("")

    # About this benchmark
    report_lines.append("## About this Benchmark")
    report_lines.append("")
    report_lines.append(
        "HolmesGPT is continuously evaluated against real-world "
        "Kubernetes and cloud troubleshooting scenarios."
    )
    report_lines.append("")
    report_lines.append(
        "If you find scenarios that HolmesGPT does not perform "
        "well on, please consider adding them as evals to the benchmark."
    )
    report_lines.append("")

    # Model accuracy comparison table - show first for quick overview
    report_lines.append("## Model Accuracy Comparison")
    report_lines.append("")
    report_lines.append(generate_summary_table(results))
    report_lines.append("")

    # Model cost comparison table
    cost_table = generate_cost_comparison_table(results)
    if cost_table:
        report_lines.append(cost_table)
        report_lines.append("")

    # Model latency comparison table
    latency_table = generate_latency_comparison_table(results)
    if latency_table:
        report_lines.append(latency_table)
        report_lines.append("")

    # Model by tag performance
    model_tag_table = generate_model_by_tag_table(results)
    if model_tag_table:
        report_lines.append(model_tag_table)
        report_lines.append("")

    # Dashboard heatmap - show after aggregate tables for full detail
    dashboard = generate_eval_dashboard_heatmap(results)
    if dashboard:
        report_lines.append(dashboard)
        report_lines.append("")

    # Footer
    report_lines.append("---")

    # Try to get experiment URL from any test that has Braintrust data
    experiment_url = None
    experiment_name = extract_experiment_name_from_results(results)

    if experiment_name:
        base_result = _get_braintrust_base_url(experiment_name)
        if base_result:
            experiment_url, _ = base_result

    # Generate footer text with specific experiment link if available
    if experiment_url and experiment_name:
        report_lines.append(
            f"*Results are automatically generated and updated weekly. "
            f"View full traces and detailed analysis in [Braintrust experiment: {experiment_name}]({experiment_url}).*"
        )
    else:
        report_lines.append(
            "*Results are automatically generated and updated weekly. "
            "For detailed traces and analysis, see our [Braintrust dashboard](https://braintrust.dev).*"
        )

    # Write report
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(report_lines))
    print(f"Report generated: {output_path}")


if __name__ == "__main__":
    main()

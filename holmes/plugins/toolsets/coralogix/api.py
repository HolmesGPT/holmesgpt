import json
import logging
from enum import Enum
from typing import Any, Optional, Tuple
from urllib.parse import urljoin

import requests  # type: ignore

from holmes.plugins.toolsets.coralogix.utils import parse_json_lines


class CoralogixTier(str, Enum):
    FREQUENT_SEARCH = "TIER_FREQUENT_SEARCH"
    ARCHIVE = "TIER_ARCHIVE"


def get_dataprime_base_url(domain: str) -> str:
    return f"https://ng-api-http.{domain}"


def _get_auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def execute_coralogix_query(
    domain: str, api_key: str, query: dict[str, Any]
) -> Tuple[requests.Response, str]:
    base_url = get_dataprime_base_url(domain).rstrip("/") + "/"
    url = urljoin(base_url, "api/v1/dataprime/query")
    response = requests.post(
        url,
        headers=_get_auth_headers(api_key),
        json=query,
        timeout=(10, 120),
    )
    return response, url


def _parse_ndjson_response(response_text: str) -> Optional[Any]:
    """Parse NDJSON response from Coralogix API."""
    json_objects = parse_json_lines(response_text)
    if not json_objects:
        return None

    results: list[Any] = []

    for obj in json_objects:
        if not isinstance(obj, dict):
            continue

        if any(k in obj for k in ("result", "results", "batches", "records")):
            results.append(obj)

    if not results:
        return None

    return results


def _build_query_dict(
    dataprime_query: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    tier: Optional[CoralogixTier] = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"syntax": "QUERY_SYNTAX_DATAPRIME"}
    if start_date:
        metadata["startDate"] = start_date
    if end_date:
        metadata["endDate"] = end_date
    if tier:
        metadata["tier"] = tier.value

    return {"query": dataprime_query, "metadata": metadata}


def _get_error_body(response: requests.Response) -> str:
    """Extract error body from response."""
    try:
        return (response.text or "").strip()
    except Exception:
        return ""


def _cleanup_coralogix_results(parsed: list[Any]) -> Any:
    """Clean up and normalize parsed Coralogix results structure."""
    # Extract nested results if present
    if len(parsed) == 1 and isinstance(parsed[0], dict) and "result" in parsed[0]:
        nested_result = parsed[0]["result"]
        if isinstance(nested_result, dict) and "results" in nested_result:
            parsed = nested_result["results"]

    # Replace items with userData JSON if present
    # userData has additional data that is missing in the main result object along with the actual result
    for i, item in enumerate(parsed):
        if isinstance(item, dict) and "userData" in item:
            try:
                parsed[i] = json.loads(item["userData"])
            except (json.JSONDecodeError, TypeError):
                # If parsing fails, keep the original item
                pass

    return parsed


def execute_dataprime_query(
    domain: str,
    api_key: str,
    dataprime_query: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    tier: Optional[CoralogixTier] = None,
    max_poll_attempts: int = 60,
    poll_interval_seconds: float = 1.0,
) -> Tuple[Optional[Any], Optional[str]]:
    try:
        query_dict = _build_query_dict(dataprime_query, start_date, end_date, tier)
        response, submit_url = execute_coralogix_query(domain, api_key, query_dict)

        if response.status_code != 200:
            body = _get_error_body(response)
            if "Compiler error" in body or "Compilation errors" in body:
                return (
                    None,
                    f"Compilation errors: {body}\nUse lucene instead of filter and verify that all labels are present before using them.",
                )
            return (
                None,
                f"Failed to submit query: status_code={response.status_code}, {body}\nURL: {submit_url}",
            )

        raw = response.text.strip()
        if not raw:
            return None, f"Empty 200 response from query submission\nURL: {submit_url}"

        parsed = _parse_ndjson_response(raw)

        # Usually if someone ran query that returns no results
        if not parsed and response.status_code in [200, 204]:
            return [], None

        if not parsed:
            return None, (
                f"Query submission:\n"
                f"URL: {submit_url}\n"
                f"Response status: {response.status_code}\n"
                f"Response body (first 2000 chars): {raw[:2000]}\n\n"
            )

        cleaned_results = _cleanup_coralogix_results(parsed)
        return cleaned_results, None

    except Exception as e:
        logging.error("Failed to execute DataPrime query", exc_info=True)
        return None, str(e)


def health_check(domain: str, api_key: str) -> Tuple[bool, str]:
    query_dict = _build_query_dict("source logs | limit 1")
    response, submit_url = execute_coralogix_query(
        domain=domain, api_key=api_key, query=query_dict
    )

    if response.status_code != 200:
        body = _get_error_body(response)
        return (
            False,
            f"Failed with status_code={response.status_code}. {body}\nURL: {submit_url}",
        )
    return True, ""

"""Classify eval runs where the model provider failed, not the model.

A run that dies on a provider error never produced an answer, so it says
nothing about the model's accuracy. Reports show it as ⚠️ (provider failure)
or ⏱️ (timeout / rate limit) and leave it out of the success rate.
"""

from enum import Enum
from typing import Any, Dict, Optional


class InfraError(str, Enum):
    PROVIDER = "provider"  # ⚠️
    TIMEOUT = "timeout"  # ⏱️ (also rate limits, as the report legend groups them)

    @property
    def emoji(self) -> str:
        return "⏱️" if self is InfraError.TIMEOUT else "⚠️"


# litellm exception class names whose cause is on the provider's side.
_PROVIDER_ERROR_TYPES = {
    "NotFoundError",
    "ServiceUnavailableError",
    "APIConnectionError",
    "InternalServerError",
    "BadGatewayError",
}
_RATE_LIMIT_ERROR_TYPES = {"RateLimitError", "ThrottledError"}
# A BadRequestError is usually a malformed request from our side; only these
# markers show the provider's upstream broke.
_BAD_REQUEST_UPSTREAM_MARKERS = ("OpenrouterException", "Upstream error")


def classify_infra_error(
    error_type: Optional[str],
    error_message: Optional[str],
    failed_due_to_throttling: bool = False,
) -> Optional[InfraError]:
    """Return the infra failure kind for a run's error, or None for a model failure.

    `error_type` / `error_message` are the class name and str() of the
    exception that `handle_test_error` stored in the test's user_properties.
    """
    error_type = error_type or ""
    error_message = error_message or ""

    if (
        failed_due_to_throttling
        or "Timeout" in error_type
        or error_type in _RATE_LIMIT_ERROR_TYPES
    ):
        return InfraError.TIMEOUT

    # Class names alone are ambiguous (other libraries define NotFoundError);
    # litellm prefixes every message with "litellm.<ClassName>:".
    if "litellm." not in error_message:
        return None
    if error_type in _PROVIDER_ERROR_TYPES:
        return InfraError.PROVIDER
    if error_type == "BadRequestError" and any(
        marker in error_message for marker in _BAD_REQUEST_UPSTREAM_MARKERS
    ):
        return InfraError.PROVIDER
    return None


def classify_result_infra_error(props: Dict[str, Any]) -> Optional[InfraError]:
    """Classify from a flat dict of a test's user_properties (or a result row)."""
    if props.get("is_setup_failure"):
        return None  # already reported as a setup failure
    return classify_infra_error(
        props.get("error_type"),
        props.get("error_message"),
        bool(props.get("failed_due_to_throttling")),
    )

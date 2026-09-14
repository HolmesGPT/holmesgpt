"""Unit tests for Datadog time-field preprocessing.

Covers the v1-endpoint UNIX-timestamp conversion added in
fix/datadog-v1-events-unix-timestamps. Datadog v1 endpoints
(e.g. /api/v1/events) require start/end as UNIX integer timestamps,
while v2 endpoints accept RFC3339 strings.
"""

import pytest
from freezegun import freeze_time

from holmes.plugins.toolsets.datadog.datadog_api import (
    _resolve_to_unix_timestamp,
    preprocess_time_fields,
)

# 2021-01-01T00:00:00Z == 1609459200 (frozen "now" for every test below)
FROZEN_NOW = "2021-01-01T00:00:00Z"
NOW_TS = 1609459200
ONE_DAY = 86400


@freeze_time(FROZEN_NOW)
@pytest.mark.parametrize(
    "value, expected",
    [
        # Relative unit strings -> absolute UNIX ints
        ("-24h", NOW_TS - ONE_DAY),
        ("-7d", NOW_TS - 7 * ONE_DAY),
        ("now", NOW_TS),
        # Negative numeric offsets (seconds) -> relative to now
        (-ONE_DAY, NOW_TS - ONE_DAY),
        ("-86400", NOW_TS - ONE_DAY),
        # Explicit RFC3339 strings -> UNIX ints (second-commit fix)
        ("2020-12-31T00:00:00Z", NOW_TS - ONE_DAY),
        # Already-absolute UNIX timestamps -> None (left unchanged by caller)
        (NOW_TS, None),
        (str(NOW_TS), None),
        # Inclusive lower bound of the valid UNIX range (second-commit fix)
        (1_000_000_000, None),
    ],
)
def test_resolve_to_unix_timestamp(value, expected):
    assert _resolve_to_unix_timestamp(value) == expected


@freeze_time(FROZEN_NOW)
@pytest.mark.parametrize("endpoint", ["api/v1/events", "/api/v1/events"])
def test_v1_events_converts_relative_times_to_unix_ints(endpoint):
    """v1 endpoints get UNIX ints, with or without a leading slash."""
    result = preprocess_time_fields({"start": "-24h", "end": "now"}, endpoint)

    assert result == {"start": NOW_TS - ONE_DAY, "end": NOW_TS}
    assert isinstance(result["start"], int)
    assert isinstance(result["end"], int)


@freeze_time(FROZEN_NOW)
@pytest.mark.parametrize("endpoint", ["api/v1/metrics", "api/v1/query"])
def test_other_v1_unix_endpoints_use_from_to(endpoint):
    """api/v1/metrics and api/v1/query also convert from/to to UNIX ints."""
    result = preprocess_time_fields({"from": "-7d", "to": "now"}, endpoint)

    assert result == {"from": NOW_TS - 7 * ONE_DAY, "to": NOW_TS}


@freeze_time(FROZEN_NOW)
def test_v1_already_absolute_unix_left_unchanged():
    """Absolute UNIX timestamps already in the payload are not re-converted."""
    payload = {"start": NOW_TS - ONE_DAY, "end": NOW_TS}
    result = preprocess_time_fields(payload, "api/v1/events")

    assert result == payload


@freeze_time(FROZEN_NOW)
def test_v2_endpoint_keeps_rfc3339_strings():
    """v2 endpoints retain the original RFC3339 behavior (no regression)."""
    result = preprocess_time_fields(
        {"filter": {"from": "-24h", "to": "now"}},
        "api/v2/logs/events/search",
    )

    assert result == {
        "filter": {
            "from": "2020-12-31T00:00:00Z",
            "to": "2021-01-01T00:00:00Z",
        }
    }


@freeze_time(FROZEN_NOW)
def test_none_and_missing_time_fields_are_skipped():
    """A present-but-None value and an absent key both hit the skip path:
    neither is converted, and an absent key is never injected."""
    # `start` present-but-None is left as-is; non-time fields are preserved.
    result = preprocess_time_fields(
        {"start": None, "end": "-24h", "query": "service:web"},
        "api/v1/events",
    )

    assert result == {
        "start": None,
        "end": NOW_TS - ONE_DAY,
        "query": "service:web",
    }

    # `start` entirely absent: it must not be materialized in the output.
    result = preprocess_time_fields({"end": "-24h"}, "api/v1/events")

    assert result == {"end": NOW_TS - ONE_DAY}
    assert "start" not in result


@freeze_time(FROZEN_NOW)
def test_original_payload_is_not_mutated():
    """preprocess_time_fields deep-copies and must not mutate its input."""
    payload = {"start": "-24h", "end": "now"}
    preprocess_time_fields(payload, "api/v1/events")

    assert payload == {"start": "-24h", "end": "now"}

import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from holmes.core.request_counters import RequestCounters, bind_request_counters
from holmes.plugins.toolsets.datadog import datadog_api
from holmes.plugins.toolsets.datadog.datadog_api import (
    DataDogRateLimitWaitExceeded,
    DataDogRequestError,
    _rate_limit_key,
    _RateLimitGate,
    execute_datadog_http_request,
    rate_limit_error_message,
)

SITE = "https://api.datadoghq.com"
LOGS_URL = f"{SITE}/api/v2/logs/events/search"
METRICS_URL = f"{SITE}/api/v1/query"
HEADERS = {"DD-API-KEY": "k", "DD-APPLICATION-KEY": "a"}
PAYLOAD = {"filter": {"query": "service:payment-api", "from": "now-1h", "to": "now"}}


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    monkeypatch.setattr(datadog_api, "_rate_limit_gate", _RateLimitGate())
    monkeypatch.setattr(
        datadog_api, "_datadog_request_semaphore", threading.BoundedSemaphore(4)
    )
    monkeypatch.setattr(datadog_api, "RETRY_JITTER_SECONDS", 0.0)
    monkeypatch.setattr(datadog_api, "START_RETRY_DELAY", 0.05)
    monkeypatch.setattr(datadog_api, "INCREMENT_RETRY_DELAY", 0.05)


class ConcurrencyTracker:
    def __init__(self, delay: float):
        self.delay = delay
        self.lock = threading.Lock()
        self.inflight = 0
        self.peak = 0

    def __call__(self, request):
        with self.lock:
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
        time.sleep(self.delay)
        with self.lock:
            self.inflight -= 1
        return 200, {}, '{"data": []}'


class Throttled:
    """Answers 429 for the first `throttled_calls` requests, then 200."""

    def __init__(self, reset_seconds: str, throttled_calls: int = 1):
        self.reset_seconds = reset_seconds
        self.throttled_calls = throttled_calls
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, request):
        with self._lock:
            self.calls += 1
            throttle = self.calls <= self.throttled_calls
        if throttle:
            return (
                429,
                {"X-RateLimit-Reset": self.reset_seconds},
                '{"errors": ["Rate limit exceeded"]}',
            )
        return 200, {}, '{"data": ["ok"]}'


def start_throttled_logs_call(url=LOGS_URL):
    """Starts a logs call in the background and returns once its 429 closed the gate."""
    thread = threading.Thread(target=call, args=(url,))
    thread.start()
    gate_key = _rate_limit_key(url, HEADERS)
    for _ in range(500):
        if datadog_api._rate_limit_gate.remaining(gate_key) > 0:
            return thread
        time.sleep(0.01)
    raise AssertionError("the throttled call never closed the gate")


def call(url, method="POST", headers=HEADERS):
    return execute_datadog_http_request(url, headers, PAYLOAD, 10, method)


def call_timed(url, method="POST", headers=HEADERS):
    started = time.monotonic()
    result = call(url, method, headers)
    return result, time.monotonic() - started


def key(url, headers=HEADERS):
    return _rate_limit_key(url, headers)


def test_parallel_requests_overlap(responses):
    tracker = ConcurrencyTracker(delay=0.5)
    responses.add_callback("POST", LOGS_URL, callback=tracker)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: call(LOGS_URL), range(4)))

    assert tracker.peak == 4


def test_concurrency_is_capped_by_semaphore(responses, monkeypatch):
    monkeypatch.setattr(
        datadog_api, "_datadog_request_semaphore", threading.BoundedSemaphore(2)
    )
    tracker = ConcurrencyTracker(delay=0.3)
    responses.add_callback("POST", LOGS_URL, callback=tracker)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: call(LOGS_URL), range(6)))

    assert tracker.peak == 2


def test_429_blocks_same_family_until_reset(responses, caplog):
    throttled = Throttled("1")
    responses.add_callback("POST", LOGS_URL, callback=throttled)

    with caplog.at_level(logging.WARNING):
        first = start_throttled_logs_call()
        result, elapsed = call_timed(LOGS_URL)
        first.join()

    assert result == {"data": ["ok"]}
    # Waited for the reset announced by the first caller's 429, then resumed.
    assert elapsed > 0.5
    assert throttled.calls == 3
    activations = [r for r in caplog.records if "'v2/logs' requests wait" in r.message]
    assert len(activations) == 1
    assert "resets in 1s" in activations[0].message


def test_other_family_proceeds_while_logs_throttled(responses):
    throttled = Throttled("2")
    responses.add_callback("POST", LOGS_URL, callback=throttled)
    responses.add("GET", METRICS_URL, json={"series": []}, status=200)

    logs = start_throttled_logs_call()
    result, elapsed = call_timed(METRICS_URL, method="GET")
    still_waiting = logs.is_alive()
    logs.join()

    assert result == {"series": []}
    assert elapsed < 1
    assert still_waiting


def test_other_family_proceeds_with_url_built_from_config(responses):
    # str(AnyUrl) ends with "/", so toolsets produce "//api/..." paths.
    base_url = str(
        datadog_api.DatadogBaseConfig(api_key="k", app_key="a", api_url=SITE).api_url
    )
    logs_url, metrics_url = (
        f"{base_url}/api/v2/logs/events/search",
        f"{base_url}/api/v1/query",
    )
    throttled = Throttled("2")
    responses.add_callback("POST", logs_url, callback=throttled)
    responses.add("GET", metrics_url, json={"series": []}, status=200)

    logs = start_throttled_logs_call(logs_url)
    _, elapsed = call_timed(metrics_url, method="GET")
    logs.join()

    assert elapsed < 1


def test_same_family_on_other_site_is_not_blocked(responses):
    eu_logs = "https://api.datadoghq.eu/api/v2/logs/events/search"
    throttled = Throttled("2")
    responses.add_callback("POST", LOGS_URL, callback=throttled)
    responses.add("POST", eu_logs, json={"data": []}, status=200)

    logs = start_throttled_logs_call()
    _, elapsed = call_timed(eu_logs)
    logs.join()

    assert elapsed < 1


def test_same_family_of_other_org_is_not_blocked(responses):
    throttled = Throttled("2")
    responses.add_callback("POST", LOGS_URL, callback=throttled)

    logs = start_throttled_logs_call()
    other_org = {"DD-API-KEY": "other-org", "DD-APPLICATION-KEY": "a"}
    result, elapsed = call_timed(LOGS_URL, headers=other_org)
    logs.join()

    assert result == {"data": ["ok"]}
    assert elapsed < 1


def test_semaphore_is_released_during_backoff_sleep(responses, monkeypatch):
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(datadog_api, "_datadog_request_semaphore", semaphore)
    free_slots_during_sleep = []
    real_sleep = datadog_api._sleep_between_retries

    def observing_sleep(seconds):
        free_slots_during_sleep.append(semaphore._value)  # type: ignore[attr-defined]
        real_sleep(seconds)

    monkeypatch.setattr(datadog_api, "_sleep_between_retries", observing_sleep)
    throttled = Throttled("2")
    responses.add_callback("POST", LOGS_URL, callback=throttled)
    responses.add("GET", METRICS_URL, json={"series": []}, status=200)

    logs = start_throttled_logs_call()
    _, metrics_elapsed = call_timed(METRICS_URL, method="GET")
    logs.join()

    assert free_slots_during_sleep == [1]
    # With a single slot, the metrics call only completes during the logs
    # backoff because the slot is free.
    assert metrics_elapsed < 1


def test_remaining_zero_on_success_closes_gate(responses):
    responses.add(
        "POST",
        LOGS_URL,
        json={"data": [1]},
        status=200,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1"},
    )

    assert call(LOGS_URL) == {"data": [1]}
    _, elapsed = call_timed(LOGS_URL)

    assert elapsed > 0.8


@pytest.mark.parametrize(
    "headers",
    [
        {"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": "10"},
        {"X-RateLimit-Reset": "10"},
        {"X-RateLimit-Remaining": "0"},
        {},
    ],
)
def test_success_without_exhausted_quota_does_not_close_gate(responses, headers):
    responses.add("POST", LOGS_URL, json={"data": []}, status=200, headers=headers)

    call(LOGS_URL)

    assert datadog_api._rate_limit_gate.remaining(key(LOGS_URL)) <= 0


def test_429_without_reset_header_uses_fallback_backoff_and_leaves_gate_open(
    responses, monkeypatch
):
    sleeps = []
    monkeypatch.setattr(datadog_api, "_sleep_between_retries", sleeps.append)
    throttled = Throttled("", throttled_calls=2)
    responses.add_callback("POST", LOGS_URL, callback=throttled)

    assert call(LOGS_URL) == {"data": ["ok"]}
    assert throttled.calls == 3
    assert sleeps == pytest.approx([0.05, 0.10])
    assert datadog_api._rate_limit_gate.remaining(key(LOGS_URL)) <= 0


@pytest.mark.parametrize("reset", ["soon", "inf", "nan"])
def test_invalid_reset_header_is_logged_and_falls_back(
    responses, monkeypatch, caplog, reset
):
    sleeps = []
    monkeypatch.setattr(datadog_api, "_sleep_between_retries", sleeps.append)
    responses.add_callback("POST", LOGS_URL, callback=Throttled(reset))

    with caplog.at_level(logging.WARNING):
        assert call(LOGS_URL) == {"data": ["ok"]}

    assert sleeps == pytest.approx([0.05])
    assert (
        f"invalid X-RateLimit-Reset header value from datadog: {reset}" in caplog.text
    )
    assert datadog_api._rate_limit_gate.remaining(key(LOGS_URL)) <= 0


def test_retry_waits_for_reset_plus_jitter(responses, monkeypatch):
    sleeps = []
    monkeypatch.setattr(datadog_api, "_sleep_between_retries", sleeps.append)
    monkeypatch.setattr(datadog_api, "RETRY_JITTER_SECONDS", 2.0)
    # tenacity's wait_random draws from random.random()
    monkeypatch.setattr(datadog_api.random, "random", lambda: 1.0)
    monkeypatch.setattr(datadog_api, "_rate_limit_gate", _NeverClosedGate())
    responses.add_callback("POST", LOGS_URL, callback=Throttled("2"))

    call(LOGS_URL)

    assert sleeps == pytest.approx([2.1 + 2.0])


class _NeverClosedGate(_RateLimitGate):
    def close(self, key, seconds):
        return True


def test_exhausted_retries_raise_with_status_and_body(responses, monkeypatch):
    monkeypatch.setattr(datadog_api, "_sleep_between_retries", lambda s: None)
    monkeypatch.setattr(datadog_api, "_rate_limit_gate", _NeverClosedGate())
    throttled = Throttled("1", throttled_calls=100)
    responses.add_callback("POST", LOGS_URL, callback=throttled)
    counters = RequestCounters()

    with bind_request_counters(counters), pytest.raises(DataDogRequestError) as err:
        call(LOGS_URL)

    assert throttled.calls == datadog_api.MAX_RETRY_COUNT_ON_RATE_LIMIT
    assert err.value.status_code == 429
    assert "Rate limit exceeded" in err.value.response_text
    assert err.value.payload == PAYLOAD
    assert counters.snapshot()["datadog_429s"] == 5
    assert counters.snapshot()["datadog_calls"] == 5


def test_non_429_error_is_not_retried(responses):
    responses.add("POST", LOGS_URL, body='{"errors": ["bad query"]}', status=400)

    with pytest.raises(DataDogRequestError) as err:
        call(LOGS_URL)

    assert err.value.status_code == 400
    assert "bad query" in str(err.value)
    assert len(responses.calls) == 1


def test_transport_error_releases_semaphore(responses, monkeypatch):
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(datadog_api, "_datadog_request_semaphore", semaphore)
    responses.add("POST", LOGS_URL, body=requests.ConnectionError("refused"))

    with pytest.raises(requests.ConnectionError):
        call(LOGS_URL)

    assert semaphore._value == 1  # type: ignore[attr-defined]


def test_get_sends_params(responses):
    responses.add("GET", METRICS_URL, json={"series": []}, status=200)

    execute_datadog_http_request(
        METRICS_URL, HEADERS, {"query": "avg:cpu{*}"}, 10, "GET"
    )

    assert "query=avg%3Acpu%7B%2A%7D" in responses.calls[0].request.url


def test_counters_track_calls_waits_and_429s(responses):
    responses.add_callback("POST", LOGS_URL, callback=Throttled("1"))
    responses.add("GET", METRICS_URL, json={"series": []}, status=200)
    counters = RequestCounters()

    with bind_request_counters(counters):
        call(LOGS_URL)
        call(METRICS_URL, method="GET")

    values = counters.snapshot()
    assert values["datadog_calls"] == 3
    assert values["datadog_429s"] == 1
    assert values["datadog_wait_ms_total"] >= 1000


def test_counters_are_noop_without_bound_request(responses):
    responses.add("POST", LOGS_URL, json={"data": []}, status=200)

    assert call(LOGS_URL) == {"data": []}


@pytest.mark.parametrize(
    "url, expected",
    [
        (LOGS_URL, ("api.datadoghq.com", "v2/logs")),
        (f"{SITE}/api/v2/logs/events", ("api.datadoghq.com", "v2/logs")),
        (f"{SITE}/api/v1/query?query=x", ("api.datadoghq.com", "v1/query")),
        (f"{SITE}/api/v2/query/timeseries", ("api.datadoghq.com", "v2/query")),
        (f"{SITE}/api/v2/spans/events/search", ("api.datadoghq.com", "v2/spans")),
        ("http://localhost:8080/api/v1/validate", ("localhost:8080", "v1/validate")),
        (f"{SITE}//api/v2/logs/events/search", ("api.datadoghq.com", "v2/logs")),
        ("https://proxy/datadog/api/v1/query", ("proxy", "v1/query")),
        ("http://proxy/dd/logs/search", ("proxy", "dd/logs/search")),
        ("http://proxy", ("proxy", "")),
    ],
)
def test_rate_limit_key(url, expected):
    host, _, family = key(url)
    assert (host, family) == expected


def test_rate_limit_key_separates_orgs_without_holding_the_api_key():
    _, org_a, _ = key(LOGS_URL, {"DD-API-KEY": "key-a"})
    _, org_b, _ = key(LOGS_URL, {"DD-API-KEY": "key-b"})

    assert org_a != org_b
    assert "key-a" not in org_a


def test_long_reset_429_is_not_retried_and_later_calls_fail_fast(responses):
    throttled = Throttled("3400", throttled_calls=100)
    responses.add_callback("POST", LOGS_URL, callback=throttled)
    responses.add("GET", METRICS_URL, json={"series": []}, status=200)

    with pytest.raises(DataDogRequestError) as first:
        call(LOGS_URL)
    started = time.monotonic()
    with pytest.raises(DataDogRequestError) as second:
        call(LOGS_URL)
    elapsed = time.monotonic() - started

    assert throttled.calls == 1
    assert "Rate limit exceeded" in first.value.response_text
    assert isinstance(second.value, DataDogRateLimitWaitExceeded)
    assert second.value.status_code == 429
    assert (
        "'v2/logs' API on api.datadoghq.com is exhausted" in second.value.response_text
    )
    assert second.value.payload == PAYLOAD
    assert elapsed < 1
    assert call(METRICS_URL, method="GET") == {"series": []}


def test_remaining_zero_with_hourly_reset_fails_fast(responses):
    responses.add(
        "POST",
        LOGS_URL,
        json={"data": [1]},
        status=200,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3400"},
    )

    assert call(LOGS_URL) == {"data": [1]}
    with pytest.raises(DataDogRequestError) as err:
        call(LOGS_URL)

    assert len(responses.calls) == 1
    assert int(err.value.response_headers["X-RateLimit-Reset"]) > 3000


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_gate_extends_but_never_shortens_and_reports_activation_once(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(datadog_api, "time", clock)
    gate = _RateLimitGate()
    logs = ("site", "org", "v2/logs")

    assert gate.close(logs, 5) is True
    assert gate.close(logs, 2) is False
    assert gate.close(logs, 8) is False
    assert gate.wait(logs, deadline=clock.now + 60) == (True, pytest.approx(8))
    assert gate.close(logs, 1) is True


def test_gate_wait_adds_jitter_and_rechecks(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(datadog_api, "time", clock)
    monkeypatch.setattr(datadog_api, "RETRY_JITTER_SECONDS", 2.0)
    monkeypatch.setattr(datadog_api.random, "uniform", lambda a, b: 0.5)
    gate = _RateLimitGate()
    logs = ("site", "org", "v2/logs")
    gate.close(logs, 3)
    real_sleep = clock.sleep

    def sleep_and_reclose(seconds):
        real_sleep(seconds)
        if len(clock.sleeps) == 1:
            gate.close(logs, 4)

    clock.sleep = sleep_and_reclose  # type: ignore[method-assign]

    opened, waited = gate.wait(logs, deadline=clock.now + 60)

    assert opened
    assert clock.sleeps == pytest.approx([3.5, 4.5])
    assert waited == pytest.approx(8.0)
    assert gate.wait(("site", "org", "v1/query"), deadline=clock.now) == (True, 0)


def test_gate_wait_gives_up_without_sleeping_past_the_deadline(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(datadog_api, "time", clock)
    gate = _RateLimitGate()
    logs = ("site", "org", "v2/logs")
    gate.close(logs, 61)

    assert gate.wait(logs, deadline=clock.now + 60) == (False, 0)
    assert clock.sleeps == []


def test_gate_wait_stops_at_the_deadline_when_the_gate_keeps_reclosing(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(datadog_api, "time", clock)
    monkeypatch.setattr(datadog_api.random, "uniform", lambda a, b: 0.0)
    gate = _RateLimitGate()
    logs = ("site", "org", "v2/logs")
    gate.close(logs, 40)
    real_sleep = clock.sleep

    def sleep_and_reclose(seconds):
        real_sleep(seconds)
        gate.close(logs, 40)

    clock.sleep = sleep_and_reclose  # type: ignore[method-assign]

    assert gate.wait(logs, deadline=clock.now + 60) == (False, pytest.approx(40))


def test_gate_prunes_expired_keys(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(datadog_api, "time", clock)
    gate = _RateLimitGate()
    gate.close(("site", "org", "a"), 1)
    clock.now += 2
    gate.close(("site", "org", "b"), 1)

    assert list(gate._reset_at) == [("site", "org", "b")]


def test_wait_budget_covers_the_whole_call(responses, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(datadog_api, "time", clock)
    monkeypatch.setattr(datadog_api, "MAX_RATE_LIMIT_WAIT_SECONDS", 10.0)
    monkeypatch.setattr(datadog_api, "_rate_limit_gate", _NeverClosedGate())
    throttled = Throttled("4", throttled_calls=100)
    responses.add_callback("POST", LOGS_URL, callback=throttled)

    with pytest.raises(DataDogRequestError):
        call(LOGS_URL)

    # 4s resets fit a 10s budget twice; the third would overrun it.
    assert throttled.calls == 3
    assert clock.sleeps == pytest.approx([4.1, 4.1])


def test_time_waited_before_giving_up_is_counted(monkeypatch):
    monkeypatch.setattr(
        datadog_api._RateLimitGate, "wait", lambda self, key, deadline: (False, 1.5)
    )
    counters = RequestCounters()

    with bind_request_counters(counters), pytest.raises(DataDogRateLimitWaitExceeded):
        call(LOGS_URL)

    assert counters.snapshot() == {"datadog_wait_ms_total": 1500}


@pytest.mark.parametrize(
    "error, expected",
    [
        (
            DataDogRequestError(
                {},
                429,
                '{"errors": ["Rate limit exceeded"]}',
                CaseInsensitiveDict({"X-RateLimit-Reset": "12"}),
            ),
            'Datadog API rate limit exceeded (HTTP 429): {"errors": ["Rate limit exceeded"]} The rate limit resets in 12s.',
        ),
        (
            DataDogRequestError({}, 429, "Rate limit exceeded", CaseInsensitiveDict()),
            "Datadog API rate limit exceeded (HTTP 429): Rate limit exceeded",
        ),
    ],
)
def test_rate_limit_error_message(error, expected):
    assert rate_limit_error_message(error) == expected


@pytest.mark.parametrize("value, expected", [("2", 2), ("0", 1)])
def test_max_concurrent_requests_env_var(value, expected):
    output = subprocess.run(
        [
            sys.executable,
            "-c",
            "from holmes.plugins.toolsets.datadog import datadog_api as d;"
            "print(d._datadog_request_semaphore._value)",
        ],
        env={**os.environ, "DATADOG_MAX_CONCURRENT_REQUESTS": value},
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert output.strip() == str(expected)


def test_concurrent_429s_activate_gate_once(responses, monkeypatch, caplog):
    monkeypatch.setattr(datadog_api, "_sleep_between_retries", lambda s: None)
    barrier = threading.Barrier(2)
    calls = {"n": 0}
    lock = threading.Lock()

    def callback(request):
        with lock:
            calls["n"] += 1
            n = calls["n"]
        if n <= 2:
            barrier.wait(timeout=5)
            return (
                429,
                {"X-RateLimit-Reset": "1"},
                '{"errors": ["Rate limit exceeded"]}',
            )
        return 200, {}, '{"data": []}'

    responses.add_callback("POST", LOGS_URL, callback=callback)

    with caplog.at_level(logging.WARNING), ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: call(LOGS_URL), range(2)))

    activations = [r for r in caplog.records if "'v2/logs' requests wait" in r.message]
    assert len(activations) == 1


def test_paginated_request_returns_data_and_cursor(responses):
    responses.add(
        "POST",
        LOGS_URL,
        json={"data": [{"id": "1"}], "meta": {"page": {"after": "cursor-2"}}},
        status=200,
    )

    data, cursor = datadog_api.execute_paginated_datadog_http_request(
        LOGS_URL, HEADERS, PAYLOAD, 10
    )

    assert data == [{"id": "1"}]
    assert cursor == "cursor-2"

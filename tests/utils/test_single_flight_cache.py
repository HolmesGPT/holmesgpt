import threading
import time
from typing import Optional

import pytest

from holmes.utils.single_flight_cache import (
    SetupTracker,
    SingleFlightTTLCache,
    record_cache_lookup,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class CountingLoader:
    def __init__(
        self, value="v", delay: float = 0.0, error: Optional[Exception] = None
    ):
        self.calls = 0
        self.value = value
        self.delay = delay
        self.error = error
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.value


def _run_concurrently(n, fn):
    barrier = threading.Barrier(n)
    results, errors = [], []

    def worker():
        barrier.wait()
        try:
            results.append(fn())
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return results, errors


def test_second_call_within_ttl_is_served_from_cache():
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    loader = CountingLoader()
    assert cache.get_or_load("k", loader) == "v"
    assert cache.get_or_load("k", loader) == "v"
    assert loader.calls == 1


def test_entry_reloads_after_ttl_expires():
    clock = FakeClock()
    cache = SingleFlightTTLCache(maxsize=4, ttl=60, timer=clock)
    loader = CountingLoader()
    cache.get_or_load("k", loader)
    clock.now += 59
    cache.get_or_load("k", loader)
    assert loader.calls == 1
    clock.now += 2
    cache.get_or_load("k", loader)
    assert loader.calls == 2


@pytest.mark.parametrize("value", [None, [], {}])
def test_empty_results_are_cached(value):
    """An account with no global instructions is the common case and must not re-query."""
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    loader = CountingLoader(value=value)
    assert cache.get_or_load("k", loader) == value
    assert cache.get_or_load("k", loader) == value
    assert loader.calls == 1


def test_keys_are_cached_independently():
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    a, b = CountingLoader("a"), CountingLoader("b")
    assert cache.get_or_load("a", a) == "a"
    assert cache.get_or_load("b", b) == "b"
    assert cache.get_or_load("a", a) == "a"
    assert (a.calls, b.calls) == (1, 1)


def test_twenty_threads_on_cold_cache_make_one_load():
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    loader = CountingLoader(delay=0.2)
    results, errors = _run_concurrently(20, lambda: cache.get_or_load("k", loader))
    assert errors == []
    assert results == ["v"] * 20
    assert loader.calls == 1


def test_twenty_threads_after_expiry_make_one_load():
    clock = FakeClock()
    cache = SingleFlightTTLCache(maxsize=4, ttl=60, timer=clock)
    loader = CountingLoader(delay=0.2)
    cache.get_or_load("k", loader)
    clock.now += 61
    results, errors = _run_concurrently(20, lambda: cache.get_or_load("k", loader))
    assert errors == [] and len(results) == 20
    assert loader.calls == 2


def test_slow_load_of_one_key_does_not_block_another():
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return "slow"

    t = threading.Thread(target=lambda: cache.get_or_load("slow", slow))
    t.start()
    assert started.wait(5)
    t0 = time.monotonic()
    assert cache.get_or_load("fast", lambda: "fast") == "fast"
    assert time.monotonic() - t0 < 1
    release.set()
    t.join(5)


def test_failed_load_is_not_cached_and_is_retried():
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    loader = CountingLoader(error=RuntimeError("supabase down"))
    with pytest.raises(RuntimeError):
        cache.get_or_load("k", loader)
    loader.error = None
    assert cache.get_or_load("k", loader) == "v"
    assert loader.calls == 2


def test_concurrent_waiters_share_the_leaders_failure():
    """During an outage, 20 concurrent turns must not turn into 20 sequential queries."""
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    loader = CountingLoader(delay=0.2, error=RuntimeError("supabase down"))
    results, errors = _run_concurrently(20, lambda: cache.get_or_load("k", loader))
    assert results == []
    assert len(errors) == 20 and all(isinstance(e, RuntimeError) for e in errors)
    assert loader.calls == 1


def test_clear_forces_reload():
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    loader = CountingLoader()
    cache.get_or_load("k", loader)
    cache.clear()
    cache.get_or_load("k", loader)
    assert loader.calls == 2


def test_load_in_flight_during_clear_is_not_stored():
    """Data read before an invalidation must not outlive it."""
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    started = threading.Event()
    release = threading.Event()

    def stale():
        started.set()
        release.wait(5)
        return "stale"

    result = {}
    t = threading.Thread(target=lambda: result.update(v=cache.get_or_load("k", stale)))
    t.start()
    assert started.wait(5)
    cache.clear()
    release.set()
    t.join(5)
    assert result["v"] == "stale"
    assert cache.get_or_load("k", lambda: "fresh") == "fresh"


def test_zero_ttl_disables_caching():
    cache = SingleFlightTTLCache(maxsize=4, ttl=0)
    loader = CountingLoader()
    cache.get_or_load("k", loader)
    cache.get_or_load("k", loader)
    assert loader.calls == 2


def test_maxsize_bounds_entries():
    cache = SingleFlightTTLCache(maxsize=2, ttl=60)
    loaders = {k: CountingLoader(k) for k in "abc"}
    for k in "abc":
        cache.get_or_load(k, loaders[k])
    for k in "abc":
        cache.get_or_load(k, loaders[k])
    assert sum(loader.calls for loader in loaders.values()) > 3


def test_setup_tracker_counts_hits_and_misses_inside_track_only():
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    setup = SetupTracker()
    cache.get_or_load("outside", lambda: 1)
    with setup.track():
        cache.get_or_load("a", lambda: 1)
        cache.get_or_load("a", lambda: 1)
        cache.get_or_load("outside", lambda: 1)
    cache.get_or_load("b", lambda: 1)
    meta = setup.meta()
    assert meta["setup_cache_hits"] == 2
    assert meta["setup_cache_misses"] == 1
    assert isinstance(meta["setup_ms"], int) and meta["setup_ms"] >= 0


def test_setup_tracker_measures_wall_time():
    setup = SetupTracker()
    time.sleep(0.05)
    assert setup.meta()["setup_ms"] >= 50


def test_setup_trackers_on_different_threads_do_not_mix():
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    trackers = [SetupTracker() for _ in range(2)]

    def run(tracker, key):
        with tracker.track():
            cache.get_or_load(key, lambda: 1)

    threads = [
        threading.Thread(target=run, args=(trackers[i], f"k{i}")) for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert [t.stats.misses for t in trackers] == [1, 1]
    assert [t.stats.hits for t in trackers] == [0, 0]


def test_record_cache_lookup_without_tracker_is_a_noop():
    record_cache_lookup(hit=True)


def test_threads_waiting_on_a_load_count_as_misses():
    """They pay the full load latency, so counting them as hits would hide expiry spikes."""
    cache = SingleFlightTTLCache(maxsize=4, ttl=60)
    loader = CountingLoader(delay=0.2)
    trackers = [SetupTracker() for _ in range(10)]
    barrier = threading.Barrier(10)

    def run(tracker):
        with tracker.track():
            barrier.wait()
            cache.get_or_load("k", loader)

    threads = [threading.Thread(target=run, args=(t,)) for t in trackers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert loader.calls == 1
    assert sum(t.stats.misses for t in trackers) == 10
    assert sum(t.stats.hits for t in trackers) == 0

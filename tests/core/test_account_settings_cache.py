import logging
import threading

import pytest

from holmes.core.account_settings_cache import (
    DEFAULT_ACCOUNT_SETTINGS_CACHE_TTL_SEC,
    AccountSettingsCache,
    account_settings_cache_ttl,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Reads:
    """A stand-in for the table read: returns or raises what each call is given."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class WaitCountingLock:
    """A lock that counts the callers blocked on it, so a test can hold a read
    in flight until every competing caller is waiting for it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self.waiting = 0

    def acquire(self, blocking=True):
        if self._lock.acquire(blocking=False):
            return True
        if not blocking:
            return False
        with self._cond:
            self.waiting += 1
            self._cond.notify_all()
        self._lock.acquire()
        with self._cond:
            self.waiting -= 1
        return True

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()

    def wait_for_waiters(self, count, timeout=5):
        with self._cond:
            return self._cond.wait_for(lambda: self.waiting >= count, timeout)


def _cache(*results, ttl=60):
    clock, reads = Clock(), Reads(*results)
    return AccountSettingsCache(reads, ttl=ttl, timer=clock), clock, reads


def test_reads_once_per_ttl():
    cache, clock, reads = _cache({"a": 1}, {"a": 2})
    assert cache.get() == {"a": 1}
    assert cache.get_fresh() == {"a": 1}
    clock.now += 59
    assert cache.get() == {"a": 1}
    assert reads.calls == 1
    clock.now += 1
    assert cache.get() == {"a": 2}
    assert reads.calls == 2


def test_get_serves_the_last_value_after_a_failed_reread_and_retries_once_per_ttl():
    cache, clock, reads = _cache({"a": 1}, Exception("502"), {"a": 3})
    assert cache.get() == {"a": 1}
    clock.now += 60
    assert cache.get() == {"a": 1}
    assert cache.get() == {"a": 1}
    assert reads.calls == 2
    clock.now += 60
    assert cache.get() == {"a": 3}
    assert reads.calls == 3


def test_get_fresh_never_serves_a_value_older_than_the_ttl():
    cache, clock, reads = _cache({"a": 1}, Exception("502"), {"a": 3})
    assert cache.get_fresh() == {"a": 1}
    clock.now += 60
    assert cache.get_fresh() is None
    # get still has the last value; get_fresh still refuses until a read succeeds.
    assert cache.get() == {"a": 1}
    assert cache.get_fresh() is None
    assert reads.calls == 2
    clock.now += 60
    assert cache.get_fresh() == {"a": 3}


def test_a_failure_before_any_read_is_none_and_is_not_retried_every_call():
    cache, clock, reads = _cache(Exception("502"), {"a": 1})
    assert cache.get() is None
    assert cache.get() is None
    assert cache.get_fresh() is None
    assert reads.calls == 1
    clock.now += 60
    assert cache.get() == {"a": 1}


def test_get_after_get_fresh_failed_does_not_read_again():
    cache, clock, reads = _cache({"a": 1}, Exception("502"))
    cache.get()
    clock.now += 60
    assert cache.get_fresh() is None
    assert cache.get() == {"a": 1}
    assert reads.calls == 2


@pytest.mark.parametrize("raw", [None, [1], "x"])
def test_settings_that_are_not_an_object_read_as_empty(raw):
    cache, _, _ = _cache(raw)
    assert cache.get() == {}


def test_clear_forgets_everything():
    cache, _, reads = _cache({"a": 1}, {"a": 2})
    cache.get()
    cache.clear()
    assert cache.get() == {"a": 2}
    assert reads.calls == 2


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, DEFAULT_ACCOUNT_SETTINGS_CACHE_TTL_SEC),
        ({"ACCOUNT_SETTINGS_CACHE_TTL_SEC": "30"}, 30),
        ({"SKILL_HIERARCHY_CACHE_TTL_SEC": "45"}, 45),
        (
            {"ACCOUNT_SETTINGS_CACHE_TTL_SEC": "30", "SKILL_HIERARCHY_CACHE_TTL_SEC": "45"},
            30,
        ),
        ({"ACCOUNT_SETTINGS_CACHE_TTL_SEC": "0"}, DEFAULT_ACCOUNT_SETTINGS_CACHE_TTL_SEC),
        ({"ACCOUNT_SETTINGS_CACHE_TTL_SEC": "soon"}, DEFAULT_ACCOUNT_SETTINGS_CACHE_TTL_SEC),
    ],
)
def test_ttl_from_the_environment(monkeypatch, env, expected):
    monkeypatch.delenv("ACCOUNT_SETTINGS_CACHE_TTL_SEC", raising=False)
    monkeypatch.delenv("SKILL_HIERARCHY_CACHE_TTL_SEC", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert account_settings_cache_ttl() == expected


def test_the_old_ttl_name_warns(monkeypatch, caplog):
    monkeypatch.delenv("ACCOUNT_SETTINGS_CACHE_TTL_SEC", raising=False)
    monkeypatch.setenv("SKILL_HIERARCHY_CACHE_TTL_SEC", "45")
    with caplog.at_level(logging.WARNING):
        account_settings_cache_ttl()
    assert "deprecated" in caplog.text


@pytest.mark.parametrize("accessor", ["get", "get_fresh"])
def test_concurrent_callers_share_one_read(accessor):
    """Callers that find the cache expired wait for the read in flight instead
    of each starting their own."""
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow_read():
        calls.append(1)
        started.set()
        release.wait(5)
        return {"a": len(calls)}

    cache = AccountSettingsCache(slow_read, ttl=60, timer=Clock())
    cache._refresh_lock = lock = WaitCountingLock()
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(getattr(cache, accessor)()))
        for _ in range(5)
    ]
    for thread in threads:
        thread.start()
    assert started.wait(5)
    assert lock.wait_for_waiters(4)
    release.set()
    for thread in threads:
        thread.join(5)

    assert len(calls) == 1
    assert results == [{"a": 1}] * 5


def test_concurrent_callers_share_one_failed_read():
    started, release = threading.Event(), threading.Event()
    calls = []

    def failing_read():
        calls.append(1)
        started.set()
        release.wait(5)
        raise RuntimeError("502")

    cache = AccountSettingsCache(failing_read, ttl=60, timer=Clock())
    cache._refresh_lock = lock = WaitCountingLock()
    threads = [threading.Thread(target=cache.get_fresh) for _ in range(5)]
    for thread in threads:
        thread.start()
    assert started.wait(5)
    assert lock.wait_for_waiters(4)
    release.set()
    for thread in threads:
        thread.join(5)

    assert len(calls) == 1


def test_get_serves_the_retained_value_while_a_refresh_is_in_flight():
    """Only get_fresh() and a caller with nothing to serve wait for a slow read."""
    clock = Clock()
    started, release = threading.Event(), threading.Event()
    calls = []

    def read():
        calls.append(1)
        if len(calls) == 2:
            started.set()
            release.wait(5)
        return {"a": len(calls)}

    cache = AccountSettingsCache(read, ttl=60, timer=clock)
    assert cache.get() == {"a": 1}
    clock.now += 60
    refresher = threading.Thread(target=cache.get)
    refresher.start()
    assert started.wait(5)

    assert cache.get() == {"a": 1}

    release.set()
    refresher.join(5)
    assert cache.get() == {"a": 2}
    assert len(calls) == 2


def test_clear_waits_for_a_read_in_flight():
    """A read that started before clear() must not publish its values after it."""
    started, release = threading.Event(), threading.Event()
    calls = []

    def read():
        calls.append(1)
        if len(calls) == 1:
            started.set()
            release.wait(5)
        return {"a": len(calls)}

    cache = AccountSettingsCache(read, ttl=60, timer=Clock())
    reader = threading.Thread(target=cache.get)
    reader.start()
    assert started.wait(5)
    clearer = threading.Thread(target=cache.clear)
    clearer.start()
    clearer.join(0.2)
    assert clearer.is_alive()

    release.set()
    reader.join(5)
    clearer.join(5)

    assert cache.get() == {"a": 2}
    assert len(calls) == 2

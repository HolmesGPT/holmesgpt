import logging

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

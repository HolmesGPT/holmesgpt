import threading
import time
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Dict, Hashable, Iterator, Optional

from cachetools import TTLCache  # type: ignore

_MISSING = object()


@dataclass
class SetupCacheStats:
    hits: int = 0
    misses: int = 0


_current_stats: ContextVar[Optional[SetupCacheStats]] = ContextVar(
    "setup_cache_stats", default=None
)


def record_cache_lookup(hit: bool) -> None:
    stats = _current_stats.get()
    if stats is None:
        return
    if hit:
        stats.hits += 1
    else:
        stats.misses += 1


class SetupTracker:
    """Per-turn setup metrics: wall time since construction plus the cache hits and misses
    of every lookup made inside `track()`. Written to UsageRecorderState.meta."""

    def __init__(self) -> None:
        self.started_at = time.perf_counter()
        self.stats = SetupCacheStats()

    @contextmanager
    def track(self) -> Iterator[SetupCacheStats]:
        token = _current_stats.set(self.stats)
        try:
            yield self.stats
        finally:
            _current_stats.reset(token)

    def meta(self) -> Dict[str, int]:
        return {
            "setup_ms": int((time.perf_counter() - self.started_at) * 1000),
            "setup_cache_hits": self.stats.hits,
            "setup_cache_misses": self.stats.misses,
        }


class SingleFlightTTLCache:
    """Thread-safe TTL cache where concurrent misses on one key share a single load.

    A load that raises is not cached; its exception reaches every caller waiting on it.
    `ttl <= 0` disables storing, but concurrent loads are still coalesced.
    """

    def __init__(
        self,
        maxsize: int,
        ttl: float,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl = ttl
        self.timer = timer
        self._lock = threading.Lock()
        # The lambda lets tests swap `timer` after construction.
        self._cache: TTLCache = TTLCache(
            maxsize=maxsize, ttl=max(ttl, 0.001), timer=lambda: self.timer()
        )
        self._inflight: Dict[Hashable, Future] = {}
        # Bumped by clear(), so a load that started before it never stores pre-clear data.
        self._generation = 0

    def get_or_load(self, key: Hashable, loader: Callable[[], Any]) -> Any:
        with self._lock:
            value = self._cache.get(key, _MISSING)
            if value is not _MISSING:
                record_cache_lookup(hit=True)
                return value
            future = self._inflight.get(key)
            leader = future is None
            if future is None:
                future = Future()
                self._inflight[key] = future
            generation = self._generation

        if not leader:
            record_cache_lookup(hit=True)
            return future.result()

        record_cache_lookup(hit=False)
        try:
            value = loader()
        except BaseException as e:
            self._finish(key, future)
            future.set_exception(e)
            raise
        with self._lock:
            if self.ttl > 0 and generation == self._generation:
                self._cache[key] = value
        self._finish(key, future)
        future.set_result(value)
        return value

    def _finish(self, key: Hashable, future: Future) -> None:
        with self._lock:
            if self._inflight.get(key) is future:
                del self._inflight[key]

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._inflight.clear()
            self._generation += 1

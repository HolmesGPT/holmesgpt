"""Per-request counters that tool code can bump without a handle to the request.

The usage recorder binds a ``RequestCounters`` for the duration of a request;
``increment`` is a no-op outside one (CLI, toolset health checks).
"""

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, Iterator, Optional, Union

Number = Union[int, float]


class RequestCounters:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: Dict[str, Number] = {}

    def increment(self, key: str, amount: Number = 1) -> None:
        with self._lock:
            self._values[key] = self._values.get(key, 0) + amount

    def snapshot(self) -> Dict[str, Number]:
        with self._lock:
            return dict(self._values)


_current: ContextVar[Optional[RequestCounters]] = ContextVar(
    "holmes_request_counters", default=None
)


@contextmanager
def bind_request_counters(counters: RequestCounters) -> Iterator[None]:
    token = _current.set(counters)
    try:
        yield
    finally:
        _current.reset(token)


def increment(key: str, amount: Number = 1) -> None:
    counters = _current.get()
    if counters is not None:
        counters.increment(key, amount)

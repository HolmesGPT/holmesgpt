"""Logging utilities for Holmes."""

import logging
from typing import Any


class EndpointFilter(logging.Filter):
    """Filter out log records for specific endpoint paths."""

    def __init__(self, path: str, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._path = path

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find(self._path) == -1


class InvalidHttpRequestFilter(logging.Filter):
    """Suppress repetitive 'Invalid HTTP request received.' warnings from Uvicorn.

    These are typically caused by service meshes (Istio mTLS), load balancers,
    or TCP health probes sending non-HTTP traffic to the server. The first
    occurrence is logged at WARNING; subsequent ones are downgraded to DEBUG.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._seen = False

    def filter(self, record: logging.LogRecord) -> bool:
        if "Invalid HTTP request received" not in record.getMessage():
            return True
        if not self._seen:
            self._seen = True
            record.msg = (
                f"{record.msg} This is typically caused by a service mesh, "
                "load balancer, or TCP probe sending non-HTTP traffic. "
                "Further occurrences will be logged at DEBUG level."
            )
            return True
        # Downgrade subsequent occurrences to DEBUG
        record.levelno = logging.DEBUG
        record.levelname = "DEBUG"
        return True

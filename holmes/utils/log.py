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

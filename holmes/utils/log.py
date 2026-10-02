"""Logging utilities for Holmes."""

import logging
from typing import Any

from pythonjsonlogger.json import JsonFormatter

JSON_LOG_FMT = "%(asctime)s %(levelname)s %(name)s %(filename)s %(lineno)d %(funcName)s %(message)s"
JSON_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S"
JSON_LOG_RENAME_FIELDS = {"levelname": "severity"}


class EndpointFilter(logging.Filter):
    """Filter out log records for specific endpoint paths."""

    def __init__(self, path: str, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._path = path

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find(self._path) == -1


def build_json_formatter() -> logging.Formatter:
    """Build the JSON log formatter shared by the server and operator entrypoints."""
    return JsonFormatter(
        fmt=JSON_LOG_FMT,
        datefmt=JSON_LOG_DATEFMT,
        rename_fields=JSON_LOG_RENAME_FIELDS,
    )

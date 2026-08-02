"""Logging utilities for the Holmes operator."""

import logging

from pythonjsonlogger.json import JsonFormatter

JSON_LOG_FMT = "%(asctime)s %(levelname)s %(name)s %(filename)s %(lineno)d %(funcName)s %(message)s"
JSON_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S"
JSON_LOG_RENAME_FIELDS = {"levelname": "severity"}


def build_json_formatter() -> logging.Formatter:
    """Build the JSON log formatter used by the operator entrypoint."""
    return JsonFormatter(
        fmt=JSON_LOG_FMT,
        datefmt=JSON_LOG_DATEFMT,
        rename_fields=JSON_LOG_RENAME_FIELDS,
    )

"""Logging utilities for Holmes."""

import logging
import warnings
from typing import Any

from pythonjsonlogger.json import JsonFormatter
from urllib3.exceptions import InsecureRequestWarning

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


def suppress_noisy_third_party_logs() -> None:
    """Quiet third-party output that repeats on every request or refresh cycle."""
    # httpx logs every HTTP request at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # The MCP client logs the session ID, the negotiated protocol version and
    # each GET-stream reconnect at INFO for every connection, and MCP servers
    # are reconnected on every toolset refresh. Its warnings still come through.
    logging.getLogger("mcp").setLevel(logging.WARNING)
    # urllib3 warns on every request to a toolset configured with
    # verify_ssl: false. Its message names the host, so this warns once per host.
    warnings.filterwarnings("once", category=InsecureRequestWarning)


def build_json_formatter() -> logging.Formatter:
    """Build the JSON log formatter shared by the server and operator entrypoints."""
    return JsonFormatter(
        fmt=JSON_LOG_FMT,
        datefmt=JSON_LOG_DATEFMT,
        rename_fields=JSON_LOG_RENAME_FIELDS,
    )

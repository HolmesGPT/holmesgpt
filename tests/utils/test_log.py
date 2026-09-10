import json
import logging
from io import StringIO

from holmes.utils.log import EndpointFilter, build_json_formatter


def test_build_json_formatter_emits_valid_json():
    buffer = StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(build_json_formatter())

    logger = logging.getLogger("holmes.test.json")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.handlers = [handler]

    logger.info("hello json")

    line = buffer.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["message"] == "hello json"
    # levelname is renamed to severity to match the other Robusta services.
    assert payload["severity"] == "INFO"
    assert payload["name"] == "holmes.test.json"


def test_endpoint_filter_drops_matching_path():
    filt = EndpointFilter(path="/healthz")
    healthz = logging.LogRecord("x", logging.INFO, "x", 0, "GET /healthz 200", None, None)
    other = logging.LogRecord("x", logging.INFO, "x", 0, "GET /api/chat 200", None, None)
    assert filt.filter(healthz) is False
    assert filt.filter(other) is True

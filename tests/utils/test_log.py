import json
import logging
import uuid
import warnings
from io import StringIO

from urllib3.exceptions import InsecureRequestWarning

from holmes.utils.log import (
    EndpointFilter,
    build_json_formatter,
    suppress_noisy_third_party_logs,
)


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


def test_suppress_noisy_third_party_logs_quiets_mcp_client_info():
    mcp_logger = logging.getLogger("mcp")
    httpx_logger = logging.getLogger("httpx")
    saved_levels = (mcp_logger.level, httpx_logger.level)
    try:
        with warnings.catch_warnings():
            suppress_noisy_third_party_logs()

        # "Received session ID" / "Negotiated protocol version" are INFO on this logger.
        streamable_http = logging.getLogger("mcp.client.streamable_http")
        assert streamable_http.getEffectiveLevel() == logging.WARNING
        assert httpx_logger.level == logging.WARNING
    finally:
        mcp_logger.setLevel(saved_levels[0])
        httpx_logger.setLevel(saved_levels[1])


def test_suppress_noisy_third_party_logs_warns_once_per_insecure_host():
    # Python's "once" registry is process-wide, so use hosts no other test warned about.
    host_a, host_b = f"a-{uuid.uuid4().hex}.example", f"b-{uuid.uuid4().hex}.example"

    with warnings.catch_warnings(record=True) as caught:
        # urllib3 registers an "always" filter for these on import.
        warnings.simplefilter("always", InsecureRequestWarning)
        suppress_noisy_third_party_logs()
        for host in [host_a, host_a, host_b, host_a, host_b]:
            warnings.warn(
                f"Unverified HTTPS request is being made to host '{host}'.",
                InsecureRequestWarning,
            )

    assert [str(w.message) for w in caught] == [
        f"Unverified HTTPS request is being made to host '{host_a}'.",
        f"Unverified HTTPS request is being made to host '{host_b}'.",
    ]

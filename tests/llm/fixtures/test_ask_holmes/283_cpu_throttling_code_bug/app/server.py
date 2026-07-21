"""HTTP entrypoint for the quote service.

GET /api/v1/quote?origin=<code>&dest=<code>&weight_kg=<kg> returns the
cheapest carrier rate for a route. Tariff matrices are cached per route
(rate cards refresh daily), so steady-state requests should be served
from cache in well under a millisecond.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tariff_engine import CARRIERS, WEIGHT_BREAKS_KG, TariffEngine

LOG_PATH = os.environ.get("LOG_PATH", "/var/log/quote-service.log")

logger = logging.getLogger("quote_service.http")
engine = TariffEngine()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "pod": os.environ.get("HOSTNAME", "quote-service"),
            }
        )


def setup_logging() -> None:
    handlers = [logging.StreamHandler(), logging.FileHandler(LOG_PATH)]
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=handlers)


class QuoteHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        # Access logging is done in do_GET with timings; silence the default.
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send(200, {"status": "ok"})
            return
        if parsed.path != "/api/v1/quote":
            self._send(404, {"error": "not found"})
            return

        params = parse_qs(parsed.query)
        origin = params.get("origin", [""])[0].strip().upper()
        dest = params.get("dest", [""])[0].strip().upper()
        try:
            weight_kg = float(params.get("weight_kg", ["1"])[0])
        except ValueError:
            self._send(400, {"error": "weight_kg must be a number"})
            return
        if len(origin) != 3 or len(dest) != 3:
            self._send(400, {"error": "origin and dest must be 3-letter codes"})
            return

        started = time.monotonic()
        matrix = engine.get_matrix(origin, dest)
        quote = engine.cheapest(matrix, weight_kg)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "GET /api/v1/quote origin=%s dest=%s weight_kg=%s -> %s %.2f EUR in %dms",
            origin,
            dest,
            weight_kg,
            quote["carrier"],
            quote["rate_eur"],
            elapsed_ms,
        )
        self._send(
            200,
            {
                "origin": origin,
                "dest": dest,
                "weight_kg": weight_kg,
                "quote": quote,
                "took_ms": elapsed_ms,
            },
        )


def main() -> None:
    setup_logging()
    logger.info(
        "quote-service starting on :8080 (%d carriers, %d weight breaks)",
        len(CARRIERS),
        len(WEIGHT_BREAKS_KG),
    )
    ThreadingHTTPServer(("0.0.0.0", 8080), QuoteHandler).serve_forever()


if __name__ == "__main__":
    main()

"""Minimal mock PagerDuty server for LLM eval.

Returns a different incident depending on the service_ids filter so the eval
can prove that project-scoped queries only see their own services.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
import json
import sys

ALPHA_INCIDENT = {
    "id": "P-HOLMES-EVAL-9k4m7x2p",
    "incident_number": 42,
    "title": "Checkout API returning 500 errors",
    "status": "triggered",
    "service": {"id": "PSVC_ALPHA", "summary": "checkout-api"},
    "html_url": "http://localhost/PINC_ALPHA",
}

BETA_INCIDENT = {
    "id": "P-SHOULD-NOT-LEAK-7aaa",
    "incident_number": 99,
    "title": "Inventory DB connection pool exhausted",
    "status": "triggered",
    "service": {"id": "PSVC_BETA", "summary": "inventory-db"},
    "html_url": "http://localhost/PINC_BETA",
}

SERVICES = {
    "PSVC_ALPHA": {"id": "PSVC_ALPHA", "name": "checkout-api", "summary": "checkout-api"},
    "PSVC_BETA": {"id": "PSVC_BETA", "name": "inventory-db", "summary": "inventory-db"},
}


class Handler(BaseHTTPRequestHandler):
    def _json(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        service_ids = qs.get("service_ids[]", [])

        if parsed.path == "/services":
            filtered = [SERVICES[s] for s in service_ids if s in SERVICES] or list(
                SERVICES.values()
            )
            self._json({"services": filtered})
        elif parsed.path == "/incidents":
            if not service_ids:
                incidents = [ALPHA_INCIDENT, BETA_INCIDENT]
            else:
                picks = []
                if "PSVC_ALPHA" in service_ids:
                    picks.append(ALPHA_INCIDENT)
                if "PSVC_BETA" in service_ids:
                    picks.append(BETA_INCIDENT)
                incidents = picks
            self._json({"incidents": incidents})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9501
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()

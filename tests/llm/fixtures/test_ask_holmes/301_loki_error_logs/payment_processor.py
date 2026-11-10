import json
import logging
import os
import random
import time
import urllib.request
import uuid

APP_NAME = "payment-gateway-v7bqm-582kd"
LOKI_PUSH_URL = os.getenv("LOKI_PUSH_URL", "http://loki:3100/loki/api/v1/push")
NAMESPACE = os.getenv("POD_NAMESPACE", "default")
POD_NAME = os.getenv("POD_NAME", APP_NAME)


class LokiLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        payload = {
            "streams": [
                {
                    "stream": {
                        "app": APP_NAME,
                        "service_name": APP_NAME,
                        "namespace": NAMESPACE,
                        "pod": POD_NAME,
                        "container": "payment-gateway",
                        "level": record.levelname.lower(),
                    },
                    "values": [[str(time.time_ns()), log_entry]],
                }
            ]
        }
        try:
            req = urllib.request.Request(
                LOKI_PUSH_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as e:
            print(f"Failed to ship log line: {e}", flush=True)


logger = logging.getLogger("payment_processor")
logger.setLevel(logging.INFO)
handler = LokiLogHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(
    logging.Formatter("%(asctime)s %(name)s %(levelname)s - %(message)s")
)
logger.addHandler(handler)

INFO_MESSAGES = [
    lambda: f"Processed payment for order {random.randint(1000, 9999)} in {random.randint(50, 350)}ms",
    lambda: f"Customer {random.randint(10_000, 99_999)} loyalty points updated",
    lambda: f"Inventory check passed for SKU-{random.randint(10000, 99999)}",
]

WARN_MESSAGES = [
    lambda: f"Cache miss for customer {random.randint(10_000, 99_999)}; retrying",
]


def declined():
    logger.error(
        f"Payment declined by gateway for order {random.randint(1000, 9999)} "
        "(issuer response 61: exceeds withdrawal amount limit)"
    )


if __name__ == "__main__":
    logger.info("payment-gateway started")
    rss = random.randint(2048, 8192)
    batch = uuid.uuid4().hex[:8]
    logger.error(
        f"data refused due to high memory usage (batch_id={batch}, rss_mb={rss})"
    )
    logger.warning(random.choice(WARN_MESSAGES)())
    declined()
    while True:
        logger.info(random.choice(INFO_MESSAGES)())
        time.sleep(random.uniform(1.0, 3.0))
        declined()

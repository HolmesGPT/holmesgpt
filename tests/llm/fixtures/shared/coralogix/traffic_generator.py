import os
import random
import time

import requests

TARGET_URL = os.environ.get("TARGET_URL", "http://payment:8080/payment")
SERVICE_LABEL = os.environ.get("SERVICE_LABEL", "payment")
DURATION_SECONDS = float(os.environ.get("DURATION_SECONDS", "120"))
DELAY_MIN_SECONDS = float(os.environ.get("DELAY_MIN_SECONDS", "0.5"))
DELAY_MAX_SECONDS = float(os.environ.get("DELAY_MAX_SECONDS", "2.0"))


def send_request():
    user_id = f"user-{random.randint(1000, 9999)}"
    amount = round(random.uniform(10.0, 500.0), 2)
    payload = {"user_id": user_id, "amount": amount}
    try:
        resp = requests.post(TARGET_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"✓ {SERVICE_LABEL} request ok: {payload}")
        else:
            print(f"✗ {SERVICE_LABEL} request failed: {resp.status_code} - {payload}")
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Request error: {exc}")


def main():
    print(f"Starting traffic generator for {SERVICE_LABEL}...")
    print(f"Target: {TARGET_URL}")
    end_time = time.time() + DURATION_SECONDS
    count = 0
    while time.time() < end_time:
        send_request()
        count += 1
        time.sleep(random.uniform(DELAY_MIN_SECONDS, DELAY_MAX_SECONDS))
    print(f"\nTraffic generation complete. Sent {count} requests.")


if __name__ == "__main__":
    main()

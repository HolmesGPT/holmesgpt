import sys
import time

CA_BUNDLE = "/etc/db-certs/ca.pem"

print("db-certs-authenticator starting", flush=True)
print(f"loading CA bundle from {CA_BUNDLE}", flush=True)
time.sleep(2)
try:
    with open(CA_BUNDLE) as f:
        f.read()
except FileNotFoundError:
    print(f"FATAL: CA bundle {CA_BUNDLE} not found; cannot verify database client certificates", flush=True)
    sys.exit(1)

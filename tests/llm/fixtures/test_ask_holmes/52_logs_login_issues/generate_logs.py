#!/usr/bin/env python3
import time
import random
from datetime import datetime


def generate_logs():
    """Generate logs with login failure scenarios."""

    login_errors = [
        ("Connection to Redis timed out after 5000ms", "redis"),
        ("SSL certificate verification failed: certificate has expired", "cert"),
        ("OAuth token validation failed: invalid signature", "oauth"),
        ("LDAP bind failed: server unavailable", "ldap"),
    ]

    for _ in range(10000):
        # Generate normal logs
        for _ in range(random.randint(3, 7)):
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            user_id = random.randint(1000, 9999)
            print(f"[{timestamp}] INFO: User {user_id} accessing dashboard")

        # Generate login failures
        if random.random() < 0.4:
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            # Only use redis timeout and cert expired errors
            error_msg, error_type = random.choice([login_errors[0], login_errors[1]])
            print(f"[{timestamp}] ERROR: Login failed: {error_msg}")
            print(f"[{timestamp}] WARN: Authentication service degraded")

        # Generate success logs
        if random.random() < 0.6:
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            user_id = random.randint(1000, 9999)
            print(f"[{timestamp}] INFO: User {user_id} logged in successfully")

    while True:
        time.sleep(1000)


if __name__ == "__main__":
    generate_logs()

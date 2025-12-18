#!/usr/bin/env python3
import time
import random
from datetime import datetime


def generate_logs():
    """Generate logs with various database query errors."""

    error_types = [
        ("SQL syntax error near 'SELCT' at line 1", "syntax"),
        ("Duplicate entry '12345' for key 'users.PRIMARY'", "constraint"),
        ("Table 'mydb.user_sessions' doesn't exist", "missing"),
        ("Column 'user_email' cannot be null", "null_value"),
    ]

    for i in range(1000):
        # Generate normal logs
        for _ in range(random.randint(5, 10)):
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            action = random.choice(
                [
                    "GET /api/users",
                    "POST /api/login",
                    "GET /api/products",
                    "PUT /api/orders",
                ]
            )
            print(f"[{timestamp}] INFO: {action} - 200 OK")

        # Generate database errors
        if random.random() < 0.3:
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            error_msg, error_type = random.choice(
                error_types[:2]
            )  # Only use syntax and constraint errors
            print(f"[{timestamp}] ERROR: Database query failed: {error_msg}")
            print(f"[{timestamp}] ERROR: Query execution aborted")

        # Generate other logs
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] INFO: Health check passed")

    while True:
        time.sleep(1000)


if __name__ == "__main__":
    generate_logs()

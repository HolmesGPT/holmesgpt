#!/usr/bin/env python3
import time
import random
from datetime import datetime


def generate_logs():
    """Generate logs with various error types for summarization."""

    # Define error scenarios without revealing exact messages
    error_scenarios = [
        # Login errors
        (
            "ERROR: Authentication failed: Redis connection timeout (5000ms exceeded)",
            "login_redis",
        ),
        (
            "ERROR: Certificate validation failed: CN=idp.company.com expired on 2024-01-15",
            "login_cert",
        ),
        # Database errors
        (
            "ERROR: Query execution failed: Syntax error at position 23 - unexpected token 'SELCT'",
            "db_syntax",
        ),
        (
            "ERROR: Insert failed: Duplicate key value violates unique constraint 'users_email_key'",
            "db_constraint",
        ),
    ]

    # Generate 50 rounds of logs
    for _ in range(50):
        # Generate normal activity logs
        for _ in range(random.randint(10, 20)):
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            log_type = random.choice(
                [
                    "INFO: Health check completed",
                    "INFO: Request processed successfully",
                    "DEBUG: Cache hit for key user_profile_*",
                    "INFO: Background job executed",
                ]
            )
            print(f"[{timestamp}] {log_type}")

        # Generate errors from all categories
        for error_msg, error_type in error_scenarios:
            if random.random() < 0.3:  # 30% chance for each error
                timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{timestamp}] {error_msg}")

                # Add follow-up log entries
                if "login" in error_type:
                    print(f"[{timestamp}] WARN: User authentication degraded")
                elif "db" in error_type:
                    print(f"[{timestamp}] WARN: Database operation rollback initiated")

    # Keep pod running
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    generate_logs()

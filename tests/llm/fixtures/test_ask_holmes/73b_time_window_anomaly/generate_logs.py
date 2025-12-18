#!/usr/bin/env python3
import json
from datetime import datetime, timedelta
import time
import random


def generate_log_entry(
    timestamp, status="success", message="Job executed successfully", error=None
):
    entry = {
        "timestamp": timestamp.isoformat() + "Z",
        "level": "ERROR" if error else "INFO",
        "service": "scheduler",
        "job": "data-sync",
        "status": status,
        "message": message,
    }

    if error:
        entry["error"] = error

    return json.dumps(entry)


def main():
    # Generate 24 hours of logs, with issues only between 03:00-03:05
    start_time = datetime.utcnow() - timedelta(hours=24)

    # Generate a log every 10 seconds for 24 hours
    current_time = start_time
    log_count = 0

    while current_time < datetime.utcnow():
        # Check if we're in the problematic time window (03:00-03:05)
        hour = current_time.hour
        minute = current_time.minute

        if hour == 3 and minute >= 0 and minute <= 5:
            # Generate error logs during this window
            print(
                generate_log_entry(
                    current_time,
                    status="failed",
                    message="Job failed - Cannot connect to external API",
                    error="ConnectionError: Failed to establish connection to https://api.external-service.com - Connection timed out",
                )
            )

            # Add some diagnostic logs
            if minute == 0 and current_time.second < 10:
                print(
                    json.dumps(
                        {
                            "timestamp": current_time.isoformat() + "Z",
                            "level": "WARN",
                            "service": "scheduler",
                            "message": "Detected repeated failures during 03:00-03:05 window",
                            "note": "This appears to be a recurring pattern - possible maintenance window?",
                        }
                    )
                )
        else:
            # Normal operation
            print(
                generate_log_entry(
                    current_time,
                    status="success",
                    message=f"Job executed successfully in {random.randint(100, 500)}ms",
                )
            )

        # Add some variety with different job types
        if log_count % 100 == 0:
            print(
                json.dumps(
                    {
                        "timestamp": current_time.isoformat() + "Z",
                        "level": "INFO",
                        "service": "scheduler",
                        "job": "health-check",
                        "status": "success",
                        "message": "System health check passed",
                    }
                )
            )

        # Advance time by 10 seconds
        current_time += timedelta(seconds=10)
        log_count += 1

        # Add slight randomness to make it more realistic
        if random.random() < 0.1:
            current_time += timedelta(seconds=random.randint(1, 5))

    # Keep pod running
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()

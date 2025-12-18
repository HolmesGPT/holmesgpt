#!/usr/bin/env python3
import time
from datetime import datetime


def log(level, message):
    """Print log message with timestamp and level"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{timestamp} {level}: {message}", flush=True)


def main():
    """Main application that generates logs without any ERROR level"""
    log("INFO", "Application starting up")
    log("INFO", "Loading configuration")
    log("INFO", "Connecting to database")
    time.sleep(0.5)
    log("INFO", "Database connection established")
    log("INFO", "Running health checks")
    log("INFO", "All systems operational")
    log("INFO", "Ready to serve requests")

    # Simulate some processing
    log("DEBUG", "Processing request batch 1")
    time.sleep(0.3)
    log("INFO", "Request batch 1 completed successfully")

    log("DEBUG", "Processing request batch 2")
    time.sleep(0.3)
    log("INFO", "Request batch 2 completed successfully")

    log("INFO", "Application running smoothly")

    # Keep running with periodic heartbeat
    while True:
        log("INFO", "Heartbeat - application healthy")
        time.sleep(60)


if __name__ == "__main__":
    main()

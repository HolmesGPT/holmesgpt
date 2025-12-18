#!/usr/bin/env python3
import sys


def main():
    """Generate Java application logs with database connection errors."""

    print("Starting Java API Checker v2.3.1")
    print("Loading configuration from application.properties")
    print("Initializing connection pool...")

    # Generate connection errors
    for i in range(4):
        print("Java Network Exception:")
        print("Failed to establish connection to database server")
        print(
            f"Connection attempt {i+1} failed: java.net.ConnectException: Connection refused"
        )
        print("Target host: prod-db, port: 3333")
        print("Connection pool exhausted: max_size=256, active=256, idle=0")

    print("FATAL: Unable to connect to required database")
    print("Shutting down application")

    sys.exit(1)


if __name__ == "__main__":
    main()

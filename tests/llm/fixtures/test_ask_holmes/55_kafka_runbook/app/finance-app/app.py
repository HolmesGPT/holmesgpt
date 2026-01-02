#!/usr/bin/env python3

import json
import random
import time
import uuid
from datetime import datetime

from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable

# Payment methods
PAYMENT_METHODS = [
    "credit_card",
    "debit_card",
    "bank_transfer",
    "paypal",
    "apple_pay",
    "google_pay",
    "stripe",
    "square",
]

# Bank codes
BANK_CODES = [
    "CHASE",
    "WELLS",
    "BOFA",
    "CITI",
    "USB",
    "PNC",
    "TRUIST",
    "CAPITAL",
    "AMEX",
    "DISCOVER",
]

# Payment statuses
PAYMENT_STATUSES = ["pending", "processing", "authorized", "completed"]

CUSTOMERS = [
    "Alice Johnson",
    "Bob Smith",
    "Carol Davis",
    "David Wilson",
    "Emma Brown",
    "Frank Miller",
    "Grace Taylor",
    "Henry Lee",
    "Isabel Garcia",
    "Jack Martinez",
]


def create_fake_payment():
    """Generate a fake payment"""
    return {
        "payment_id": str(uuid.uuid4()),
        "customer_name": random.choice(CUSTOMERS),
        "amount": round(random.uniform(5.0, 2500.0), 2),
        "currency": "USD",
        "payment_method": random.choice(PAYMENT_METHODS),
        "bank_code": random.choice(BANK_CODES),
        "merchant_id": f"MERCH_{random.randint(1000, 9999)}",
        "transaction_ref": f"TXN_{random.randint(100000, 999999)}",
        "timestamp": datetime.utcnow().isoformat(),
        "status": random.choice(PAYMENT_STATUSES),
        "description": f"Payment for order #{random.randint(10000, 99999)}",
    }


def main():
    # Kafka configuration
    kafka_bootstrap_servers = "kafka:9092"
    topic = "payments"

    # Retry logic for Kafka connection
    max_retries = 10
    retry_delay = 5  # seconds
    producer = None

    for attempt in range(max_retries):
        try:
            print(f"Kafka connection attempt {attempt + 1}/{max_retries}")
            # Create Kafka producer with resilient timeout settings
            producer = KafkaProducer(
                bootstrap_servers=[kafka_bootstrap_servers],
                value_serializer=lambda x: json.dumps(x).encode("utf-8"),
                key_serializer=lambda x: x.encode("utf-8") if x else None,
                request_timeout_ms=30000,  # 30 seconds
                retries=5,  # Retry up to 5 times
                retry_backoff_ms=1000,  # 1 second between retries
                acks="all",  # Wait for all replicas
                api_version=(0, 10, 1),  # Specify API version
                metadata_max_age_ms=300000,  # 5 minutes
                connections_max_idle_ms=540000,  # 9 minutes
                max_block_ms=60000,  # 60 seconds for metadata fetch
            )
            print("Successfully connected to Kafka!")
            break
        except NoBrokersAvailable:
            print(f"Kafka not ready yet, waiting {retry_delay}s before retry...")
            time.sleep(retry_delay)
        except Exception as e:
            print(f"Unexpected error connecting to Kafka: {e}")
            time.sleep(retry_delay)
    else:
        print(f"Failed to connect to Kafka after {max_retries} attempts")
        return

    print(f"Starting finance producer for topic: {topic}")
    print(f"Kafka servers: {kafka_bootstrap_servers}")

    try:
        while True:
            # Create fake payment
            payment = create_fake_payment()

            # Send to Kafka
            future = producer.send(topic, key=payment["payment_id"], value=payment)

            # Wait for the message to be sent with retry logic
            try:
                record_metadata = future.get(timeout=30)
            except Exception as e:
                print(f"Failed to send payment {payment['payment_id'][:8]}...: {e}")
                time.sleep(2)  # Wait before next attempt
                continue

            print(
                f"Sent payment {payment['payment_id'][:8]}... to {record_metadata.topic}:{record_metadata.partition}"
            )

            # Wait 1 second before sending next payment
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down finance producer...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        producer.close()


if __name__ == "__main__":
    main()

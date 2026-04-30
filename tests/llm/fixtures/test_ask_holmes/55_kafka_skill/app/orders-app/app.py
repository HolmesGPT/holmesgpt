#!/usr/bin/env python3

import json
import random
import time
import uuid
from datetime import datetime

from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable

# Fake order data
PRODUCTS = [
    "Laptop",
    "Smartphone",
    "Tablet",
    "Headphones",
    "Monitor",
    "Keyboard",
    "Mouse",
    "Webcam",
    "Speaker",
    "Charger",
]

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


def create_fake_order():
    """Generate a fake order"""
    return {
        "order_id": str(uuid.uuid4()),
        "customer_name": random.choice(CUSTOMERS),
        "product": random.choice(PRODUCTS),
        "quantity": random.randint(1, 5),
        "price": round(random.uniform(10.0, 1000.0), 2),
        "timestamp": datetime.utcnow().isoformat(),
        "status": "pending",
    }


def main():
    # Kafka configuration
    kafka_bootstrap_servers = "kafka:9092"
    topic = "finance"

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

    print(f"Starting orders producer for topic: {topic}")
    print(f"Kafka servers: {kafka_bootstrap_servers}")

    try:
        while True:
            # Create fake order
            order = create_fake_order()

            # Send to Kafka
            future = producer.send(topic, key=order["order_id"], value=order)

            # Wait for the message to be sent with retry logic
            try:
                record_metadata = future.get(timeout=30)
            except Exception as e:
                print(f"Failed to send order {order['order_id'][:8]}...: {e}")
                time.sleep(2)  # Wait before next attempt
                continue

            print(
                f"Sent order {order['order_id'][:8]}... to {record_metadata.topic}:{record_metadata.partition}"
            )

            # Wait 100 milliseconds before sending next order
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nShutting down orders producer...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        producer.close()


if __name__ == "__main__":
    main()

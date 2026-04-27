#!/usr/bin/env python3

import json
import random
import time
from datetime import datetime

from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

# Email domains for simulation
EMAIL_DOMAINS = [
    "finance.example.com",
    "accounting.example.com",
    "billing.example.com",
    "orders.example.com",
    "customer.example.com",
]


def generate_customer_email(customer_name):
    """Generate a fake customer email"""
    # Convert customer name to email format
    name_parts = customer_name.lower().split()
    if len(name_parts) >= 2:
        email_user = f"{name_parts[0]}.{name_parts[1]}"
    else:
        email_user = name_parts[0]

    domain = random.choice(EMAIL_DOMAINS)
    return f"{email_user}@{domain}"


def simulate_email_processing(order_data):
    """Simulate slow email server processing"""
    customer_email = generate_customer_email(order_data["customer_name"])

    print(
        f"[{datetime.utcnow().isoformat()}] Processing invoice for order {order_data['order_id'][:8]}..."
    )
    print(f"[{datetime.utcnow().isoformat()}] Customer: {order_data['customer_name']}")
    print(
        f"[{datetime.utcnow().isoformat()}] Product: {order_data['product']} (qty: {order_data['quantity']})"
    )
    print(f"[{datetime.utcnow().isoformat()}] Total: ${order_data['price']:.2f}")

    # Simulate connecting to email server
    print(f"[{datetime.utcnow().isoformat()}] Connecting to email server...")
    time.sleep(random.uniform(0, 0.1))

    # Simulate email template processing
    print(f"[{datetime.utcnow().isoformat()}] Generating invoice template...")
    time.sleep(random.uniform(0, 0.1))

    # Simulate sending email (this is the slow part)
    print(
        f"[{datetime.utcnow().isoformat()}] Sending invoice email to {customer_email}..."
    )

    email_delay = random.uniform(0.1, 0.2)
    time.sleep(email_delay)

    # Simulate email server response
    if random.random() < 0.95:  # 95% success rate
        print(
            f"[{datetime.utcnow().isoformat()}] ✓ Invoice email sent successfully to {customer_email}"
        )
        print(
            f"[{datetime.utcnow().isoformat()}] Email server processing time: {email_delay:.2f}s"
        )
    else:
        print(f"[{datetime.utcnow().isoformat()}] ✗ Email server timeout - retrying...")
        retry_delay = random.uniform(0.1, 0.2)
        time.sleep(retry_delay)
        print(
            f"[{datetime.utcnow().isoformat()}] ✓ Invoice email sent on retry to {customer_email}"
        )

    print(
        f"[{datetime.utcnow().isoformat()}] Invoice processing completed for order {order_data['order_id'][:8]}"
    )
    print("=" * 80)


def main():
    # Kafka configuration
    kafka_bootstrap_servers = "kafka:9092"
    topic = "finance"
    group_id = "invoices-processor"

    print(
        f"Connecting to Kafka at {kafka_bootstrap_servers}. Topic={topic}. Consumer group={group_id}"
    )

    # Retry logic for Kafka connection
    max_retries = 10
    retry_delay = 5  # seconds
    consumer = None

    for attempt in range(max_retries):
        try:
            print(
                f"[{datetime.utcnow().isoformat()}] Kafka connection attempt {attempt + 1}/{max_retries}"
            )
            # Create Kafka consumer with settings to ensure it stays active
            consumer = KafkaConsumer(
                topic,
                bootstrap_servers=[kafka_bootstrap_servers],
                group_id=group_id,
                value_deserializer=lambda x: json.loads(x.decode("utf-8")),
                key_deserializer=lambda x: x.decode("utf-8") if x else None,
                auto_offset_reset="latest",
                enable_auto_commit=True,
                session_timeout_ms=30000,  # 30 seconds
                heartbeat_interval_ms=10000,  # 10 seconds
                max_poll_interval_ms=300000,  # 5 minutes
                consumer_timeout_ms=300000,  # 5 minutes
            )
            print(f"[{datetime.utcnow().isoformat()}] Successfully connected to Kafka!")
            break
        except NoBrokersAvailable:
            print(
                f"[{datetime.utcnow().isoformat()}] Kafka not ready yet, waiting {retry_delay}s before retry..."
            )
            time.sleep(retry_delay)
        except Exception as e:
            print(
                f"[{datetime.utcnow().isoformat()}] Unexpected error connecting to Kafka: {e}"
            )
            time.sleep(retry_delay)
    else:
        print(
            f"[{datetime.utcnow().isoformat()}] Failed to connect to Kafka after {max_retries} attempts"
        )
        return

    print(f"Starting invoices consumer for topic: {topic}")
    print(f"Kafka servers: {kafka_bootstrap_servers}")
    print(f"Consumer group: {group_id}")
    print("=" * 80)

    try:
        while True:
            message_batch = consumer.poll(timeout_ms=1000)

            if message_batch:
                for topic_partition, messages in message_batch.items():
                    for message in messages:
                        print(
                            f"[{datetime.utcnow().isoformat()}] Processing new message from Kafka..."
                        )
                        order_data = message.value

                        # Process the order and send invoice email
                        simulate_email_processing(order_data)

    except KeyboardInterrupt:
        print("\nShutting down invoices consumer...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()

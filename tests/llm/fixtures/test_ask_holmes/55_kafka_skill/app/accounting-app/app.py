#!/usr/bin/env python3

import json
import random
import time
from datetime import datetime

from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

# Database tables simulation
DB_TABLES = [
    "payments",
    "transactions",
    "customer_accounts",
    "merchant_accounts",
    "audit_logs",
    "risk_scores",
]


def calculate_fees(payment_data):
    """Calculate processing fees based on payment method and amount"""
    amount = payment_data["amount"]
    payment_method = payment_data["payment_method"]

    # Different fee structures for different payment methods
    fee_rates = {
        "credit_card": 0.029,  # 2.9%
        "debit_card": 0.015,  # 1.5%
        "bank_transfer": 0.008,  # 0.8%
        "paypal": 0.034,  # 3.4%
        "apple_pay": 0.025,  # 2.5%
        "google_pay": 0.025,  # 2.5%
        "stripe": 0.029,  # 2.9%
        "square": 0.026,  # 2.6%
    }

    fee_rate = fee_rates.get(payment_method, 0.03)  # Default 3%
    processing_fee = round(amount * fee_rate, 2)
    net_amount = round(amount - processing_fee, 2)

    return processing_fee, net_amount


def calculate_risk_score(payment_data):
    """Calculate risk score for the payment"""
    base_score = 50
    amount = payment_data["amount"]

    # Higher amounts increase risk
    if amount > 1000:
        base_score += 20
    elif amount > 500:
        base_score += 10

    # Some payment methods are riskier
    risky_methods = ["paypal", "apple_pay", "google_pay"]
    if payment_data["payment_method"] in risky_methods:
        base_score += 15

    # Add some randomness
    risk_score = max(0, min(100, base_score + random.randint(-15, 15)))
    return risk_score


def simulate_db_operations(payment_data, processing_fee, net_amount, risk_score):
    """Simulate fast database operations"""
    payment_id = payment_data["payment_id"]

    print(
        f"[{datetime.utcnow().isoformat()}] Starting DB operations for payment {payment_id[:8]}..."
    )

    # Simulate fast calculations and DB writes
    start_time = time.time()

    # Quick validation
    print(f"[{datetime.utcnow().isoformat()}] Validating payment data...")
    time.sleep(random.uniform(0.01, 0.03))  # 10-30ms

    # Calculate and log processing
    print(
        f"[{datetime.utcnow().isoformat()}] Processing fee: ${processing_fee:.2f}, Net: ${net_amount:.2f}"
    )
    print(f"[{datetime.utcnow().isoformat()}] Risk score: {risk_score}/100")

    # Simulate database writes
    for table in random.sample(DB_TABLES, 3):  # Write to 3 random tables
        print(f"[{datetime.utcnow().isoformat()}] Writing to {table} table...")
        time.sleep(random.uniform(0.005, 0.015))  # 5-15ms per DB write

    # Final processing
    print(f"[{datetime.utcnow().isoformat()}] Updating account balances...")
    time.sleep(random.uniform(0.01, 0.02))  # 10-20ms

    total_time = (time.time() - start_time) * 1000
    print(
        f"[{datetime.utcnow().isoformat()}] ✓ Payment {payment_id[:8]} processed successfully ({total_time:.1f}ms)"
    )
    print("-" * 60)


def main():
    # Kafka configuration
    kafka_bootstrap_servers = "kafka:9092"
    topic = "payments"
    group_id = "accounting-processor"

    # Retry logic for Kafka connection
    max_retries = 10
    retry_delay = 5  # seconds
    consumer = None

    for attempt in range(max_retries):
        try:
            print(
                f"[{datetime.utcnow().isoformat()}] Kafka connection attempt {attempt + 1}/{max_retries}"
            )
            # Create Kafka consumer with resilient timeout settings
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
                request_timeout_ms=60000,  # 60 seconds (must be > session_timeout)
                api_version=(0, 10, 1),  # Specify API version
                metadata_max_age_ms=300000,  # 5 minutes
                connections_max_idle_ms=540000,  # 9 minutes
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

    print(f"Starting accounting consumer for topic: {topic}")
    print(f"Kafka servers: {kafka_bootstrap_servers}")
    print(f"Consumer group: {group_id}")
    print("=" * 60)

    try:
        for message in consumer:
            payment_data = message.value
            # Calculate fees and risk
            processing_fee, net_amount = calculate_fees(payment_data)
            risk_score = calculate_risk_score(payment_data)

            # Process the payment quickly
            simulate_db_operations(payment_data, processing_fee, net_amount, risk_score)

            # Small random delay (70-130ms total processing time)
            processing_delay = random.uniform(0.07, 0.13)
            time.sleep(processing_delay)

    except KeyboardInterrupt:
        print("\nShutting down accounting consumer...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()

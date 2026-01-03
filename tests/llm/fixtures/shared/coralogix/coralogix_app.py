import logging
import os
import random
import time

from flask import Flask, jsonify, request

# OpenTelemetry imports
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import set_meter_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import (
    Status,
    StatusCode,
    get_tracer,
    set_tracer_provider,
)

LOGS_ENABLED = os.environ.get("CORALOGIX_LOGS_ENABLED", "false").lower() == "true"
TRACES_ENABLED = os.environ.get("CORALOGIX_TRACES_ENABLED", "false").lower() == "true"
METRICS_ENABLED = os.environ.get("CORALOGIX_METRICS_ENABLED", "false").lower() == "true"
SERVICE_NAME = os.environ.get("SERVICE_NAME", "payment-service")
SERVICE_LABEL = os.environ.get("SERVICE_LABEL", "payment")

# Configure OpenTelemetry resource
resource = Resource.create(
    {
        "service.name": SERVICE_NAME,
        "k8s.namespace.name": os.environ.get("K8S_NAMESPACE", "app-173"),
        "k8s.pod.name": os.environ.get("K8S_POD_NAME", "unknown"),
    }
)

# Send telemetry to local OTLP collector; it will forward to Coralogix.
K8S_NODE_IP = os.environ.get("K8S_NODE_IP", "192.168.13.204")
otlp_endpoint = f"http://{K8S_NODE_IP}:4318/v1/"

if TRACES_ENABLED:
    trace_exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}traces")
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    set_tracer_provider(tracer_provider)

if LOGS_ENABLED:
    log_exporter = OTLPLogExporter(endpoint=f"{otlp_endpoint}logs")
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    set_logger_provider(logger_provider)

if METRICS_ENABLED:
    metric_exporter = OTLPMetricExporter(endpoint=f"{otlp_endpoint}metrics")
    reader = PeriodicExportingMetricReader(metric_exporter)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    set_meter_provider(meter_provider)
else:
    meter_provider = None

app = Flask(__name__)
tracer = get_tracer(__name__)

if METRICS_ENABLED:
    meter = meter_provider.get_meter(SERVICE_NAME)
    payment_success_counter = meter.create_counter("payment_success_total")
    payment_failure_counter = meter.create_counter("payment_failure_total")
    payment_duration_hist = meter.create_histogram("payment_duration_seconds", unit="s")
else:
    payment_success_counter = None
    payment_failure_counter = None
    payment_duration_hist = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

if LOGS_ENABLED:
    log_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logger.addHandler(log_handler)


@app.route("/health")
def health():
    return "OK"


@app.route("/payment", methods=["POST"])
def payment():
    start_time = time.perf_counter()
    with tracer.start_as_current_span("process_payment") as span:
        data = request.json or {}
        user_id = data.get("user_id", "guest")
        amount = data.get("amount", 0)

        span.set_attribute("payment.user_id", user_id)
        span.set_attribute("payment.amount", float(amount))

        logger.info(
            f"Processing {SERVICE_LABEL} request for user {user_id}, amount: ${amount}"
        )

        # Simulate database query that sometimes times out
        with tracer.start_as_current_span("database_query") as db_span:
            db_span.set_attribute("db.system", "postgresql")
            db_span.set_attribute("db.operation", "SELECT")

            query_time = random.uniform(0.1, 0.3)
            if random.random() < 0.3:  # 30% chance of slow query
                query_time = random.uniform(2.5, 5.0)
                logger.warning(f"Slow database query detected: {query_time:.2f}s")
                db_span.set_attribute("db.query.duration", f"{query_time:.2f}s")
                db_span.set_attribute("db.slow_query", True)

            time.sleep(query_time)

            # Simulate connection pool exhaustion errors
            if random.random() < 0.2:  # 20% chance of error
                error_msg = (
                    "Database connection timeout after 30s - MaxConnectionsReached"
                )
                logger.error(error_msg)
                db_span.set_status(Status(StatusCode.ERROR, error_msg))
                db_span.set_attribute("error", True)
                db_span.set_attribute("error.type", "connection_timeout")
                span.set_status(Status(StatusCode.ERROR, error_msg))
                span.set_attribute("error", True)

                if METRICS_ENABLED and payment_failure_counter:
                    duration = time.perf_counter() - start_time
                    payment_failure_counter.add(
                        1, attributes={"payment.user_id": user_id}
                    )
                    payment_duration_hist.record(
                        duration,
                        attributes={
                            "payment.user_id": user_id,
                            "payment.status": "failed",
                        },
                    )

                return jsonify({"error": "Payment processing failed"}), 500

        response = {
            "payment_id": f"pay-{random.randint(1000, 9999)}",
            "user_id": user_id,
            "amount": amount,
            "status": "completed",
        }

        span.set_attribute("payment.payment_id", response["payment_id"])
        span.set_attribute("payment.status", "completed")
        logger.info(
            f"{SERVICE_LABEL.title()} completed successfully: {response['payment_id']}"
        )

        if METRICS_ENABLED and payment_success_counter:
            duration = time.perf_counter() - start_time
            payment_success_counter.add(1, attributes={"payment.user_id": user_id})
            payment_duration_hist.record(
                duration,
                attributes={
                    "payment.user_id": user_id,
                    "payment.status": "success",
                },
            )

        return jsonify(response)


if __name__ == "__main__":
    logger.info("Starting payment service on port 8080")
    app.run(host="0.0.0.0", port=8080)

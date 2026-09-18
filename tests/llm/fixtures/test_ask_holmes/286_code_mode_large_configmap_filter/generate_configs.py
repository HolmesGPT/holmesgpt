#!/usr/bin/env python3
"""Generate a large platform-config YAML file with 130 service configurations.

One service (payment-gateway) contains a unique needle connection string
that the LLM must discover by querying the ConfigMap with advanced jq operations.
"""

import random
import sys


SEED = 212
NEEDLE_SERVICE = "payment-gateway"
NEEDLE_CONNECTION_STRING = "postgresql://admin@db-pmt-7k3m9x.internal.svc:5432/transactions_v2"

SERVICE_NAMES = [
    "auth-provider", "billing-engine", "cart-service", "checkout-api",
    "content-delivery", "coupon-manager", "customer-profile", "data-pipeline",
    "delivery-tracker", "discount-engine", "email-dispatcher", "event-bus",
    "feature-flags", "feedback-collector", "file-storage", "fraud-detector",
    "geo-locator", "graph-resolver", "health-monitor", "identity-broker",
    "image-processor", "import-service", "index-builder", "insight-engine",
    "integration-hub", "inventory-manager", "invoice-generator", "job-scheduler",
    "kafka-bridge", "key-manager", "label-service", "lead-tracker",
    "license-manager", "link-shortener", "load-balancer", "log-aggregator",
    "loyalty-program", "mail-queue", "marketplace-api", "media-encoder",
    "membership-service", "message-broker", "metrics-collector", "migration-runner",
    "ml-inference", "notification-hub", "oauth-gateway", "onboarding-flow",
    "order-processor", "org-manager", "outbox-relay", "package-registry",
    NEEDLE_SERVICE, "pdf-generator", "permission-service", "pipeline-orchestrator",
    "platform-gateway", "plugin-loader", "policy-engine", "preference-store",
    "price-calculator", "product-catalog", "promo-engine", "provisioner",
    "pubsub-adapter", "query-optimizer", "queue-manager", "quota-service",
    "rate-limiter", "recommendation-engine", "refund-processor", "registry-sync",
    "reminder-service", "render-engine", "report-builder", "request-router",
    "resource-allocator", "retry-handler", "review-moderator", "risk-analyzer",
    "role-manager", "rule-engine", "sandbox-controller", "scheduler-api",
    "schema-registry", "search-indexer", "secret-rotator", "session-manager",
    "settlement-service", "shard-manager", "shipping-calculator", "sla-monitor",
    "snapshot-service", "social-connector", "sse-gateway", "status-page",
    "storage-gateway", "stream-processor", "subscription-manager", "support-ticket",
    "sync-coordinator", "tag-service", "task-runner", "tax-calculator",
    "telemetry-agent", "template-engine", "tenant-manager", "test-harness",
    "theme-service", "throttle-controller", "timeline-service", "token-issuer",
    "transaction-log", "transform-pipeline", "translation-service", "trial-manager",
    "upload-handler", "usage-tracker", "user-directory", "validation-service",
    "vault-proxy", "vendor-api", "version-control", "video-transcoder",
    "virtual-network", "visitor-tracker", "webhook-relay", "workflow-engine",
    "workspace-manager", "zipkin-collector",
]

TEAMS = [
    "platform", "payments", "identity", "growth", "infrastructure",
    "data", "commerce", "security", "observability", "devex",
    "mobile", "frontend", "backend", "ml-ops", "sre",
]

DB_ENGINES = ["postgresql", "mysql", "mongodb", "redis", "cassandra"]
DB_PREFIXES = ["db", "rds", "store", "persist", "data"]
CACHE_BACKENDS = ["redis", "memcached", "hazelcast"]
LOG_LEVELS = ["info", "debug", "warn", "error"]
PROTOCOLS = ["http", "grpc", "graphql", "websocket"]
REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]
ENVIRONMENTS = ["production", "staging", "canary"]


def random_hex(rng, length=6):
    return "".join(rng.choice("0123456789abcdef") for _ in range(length))


def random_version(rng):
    return f"{rng.randint(1, 12)}.{rng.randint(0, 30)}.{rng.randint(0, 99)}"


def random_connection_string(rng, service_name):
    engine = rng.choice(DB_ENGINES)
    prefix = rng.choice(DB_PREFIXES)
    host_id = random_hex(rng)
    port_map = {
        "postgresql": 5432,
        "mysql": 3306,
        "mongodb": 27017,
        "redis": 6379,
        "cassandra": 9042,
    }
    port = port_map[engine]
    db_name = f"{service_name.replace('-', '_')}_{rng.choice(['main', 'primary', 'prod', 'data', 'store'])}"
    user = rng.choice(["app", "svc", "admin", "readwrite", "service"])
    return f"{engine}://{user}@{prefix}-{host_id}.internal.svc:{port}/{db_name}"


def generate_service_config(rng, name, connection_string=None):
    team = rng.choice(TEAMS)
    version = random_version(rng)
    protocol = rng.choice(PROTOCOLS)
    region = rng.choice(REGIONS)
    env = rng.choice(ENVIRONMENTS)

    if connection_string is None:
        connection_string = random_connection_string(rng, name)

    cpu_req = rng.choice(["100m", "250m", "500m", "1000m"])
    cpu_limit = rng.choice(["500m", "1000m", "2000m", "4000m"])
    mem_req = rng.choice(["128Mi", "256Mi", "512Mi", "1Gi"])
    mem_limit = rng.choice(["256Mi", "512Mi", "1Gi", "2Gi"])
    replicas_min = rng.randint(1, 4)
    replicas_max = rng.randint(replicas_min + 1, replicas_min + 8)
    port = rng.randint(3000, 9999)
    cache_backend = rng.choice(CACHE_BACKENDS)
    cache_host = f"cache-{random_hex(rng)}.internal.svc"
    cache_port = rng.choice([6379, 11211])
    log_level = rng.choice(LOG_LEVELS)
    timeout_ms = rng.choice([1000, 2000, 3000, 5000, 10000])
    retry_count = rng.randint(1, 5)
    circuit_breaker_threshold = rng.randint(3, 10)
    circuit_breaker_timeout = rng.randint(10, 60)
    health_path = rng.choice(["/healthz", "/health", "/ready", "/status", "/_health"])
    metrics_path = rng.choice(["/metrics", "/prometheus", "/_metrics"])
    num_deps = rng.randint(1, 5)
    deps = rng.sample([s for s in SERVICE_NAMES if s != name], num_deps)
    env_vars = {
        "NODE_ENV": env,
        "LOG_FORMAT": rng.choice(["json", "text", "structured"]),
        "TRACE_SAMPLING_RATE": str(round(rng.uniform(0.01, 1.0), 2)),
        "MAX_CONNECTIONS": str(rng.randint(10, 200)),
        "WORKER_THREADS": str(rng.randint(1, 16)),
        "GRACEFUL_SHUTDOWN_TIMEOUT": f"{rng.randint(5, 30)}s",
        "REQUEST_BODY_LIMIT": f"{rng.choice([1, 5, 10, 50])}mb",
    }

    lines = []
    lines.append(f"  {name}:")
    lines.append(f"    name: {name}")
    lines.append(f"    version: \"{version}\"")
    lines.append(f"    team: {team}")
    lines.append(f"    deployment:")
    lines.append(f"      region: {region}")
    lines.append(f"      environment: {env}")
    lines.append(f"      strategy: {rng.choice(['rolling', 'blue-green', 'canary'])}")
    lines.append(f"      max_surge: {rng.choice(['25%', '50%', '1', '2'])}")
    lines.append(f"      max_unavailable: {rng.choice(['0', '1', '25%'])}")
    lines.append(f"    resources:")
    lines.append(f"      requests:")
    lines.append(f"        cpu: \"{cpu_req}\"")
    lines.append(f"        memory: \"{mem_req}\"")
    lines.append(f"      limits:")
    lines.append(f"        cpu: \"{cpu_limit}\"")
    lines.append(f"        memory: \"{mem_limit}\"")
    lines.append(f"    networking:")
    lines.append(f"      protocol: {protocol}")
    lines.append(f"      port: {port}")
    lines.append(f"      health_check: {health_path}")
    lines.append(f"      metrics_endpoint: {metrics_path}")
    lines.append(f"      timeout_ms: {timeout_ms}")
    lines.append(f"    database:")
    lines.append(f"      connection_string: \"{connection_string}\"")
    lines.append(f"      pool_size: {rng.randint(5, 50)}")
    lines.append(f"      max_idle: {rng.randint(2, 20)}")
    lines.append(f"      connection_timeout_ms: {rng.randint(1000, 10000)}")
    lines.append(f"      read_replicas: {rng.randint(0, 3)}")
    lines.append(f"    cache:")
    lines.append(f"      backend: {cache_backend}")
    lines.append(f"      host: \"{cache_host}\"")
    lines.append(f"      port: {cache_port}")
    lines.append(f"      ttl_seconds: {rng.choice([60, 300, 600, 1800, 3600])}")
    lines.append(f"      max_memory: \"{rng.choice(['64mb', '128mb', '256mb', '512mb'])}\"")
    lines.append(f"    logging:")
    lines.append(f"      level: {log_level}")
    lines.append(f"      output: {rng.choice(['stdout', 'file', 'both'])}")
    lines.append(f"      retention_days: {rng.choice([7, 14, 30, 90])}")
    lines.append(f"      structured: {rng.choice(['true', 'false'])}")
    lines.append(f"    monitoring:")
    lines.append(f"      alerts_enabled: {rng.choice(['true', 'false'])}")
    lines.append(f"      slo_target: \"{round(rng.uniform(99.0, 99.99), 2)}%\"")
    lines.append(f"      error_budget_burn_rate: {round(rng.uniform(1.0, 10.0), 1)}")
    lines.append(f"      pager_severity: {rng.choice(['P1', 'P2', 'P3', 'P4'])}")
    lines.append(f"    resilience:")
    lines.append(f"      retry_count: {retry_count}")
    lines.append(f"      retry_backoff_ms: {rng.choice([100, 200, 500, 1000])}")
    lines.append(f"      circuit_breaker_threshold: {circuit_breaker_threshold}")
    lines.append(f"      circuit_breaker_timeout_s: {circuit_breaker_timeout}")
    lines.append(f"      bulkhead_max_concurrent: {rng.randint(10, 100)}")
    lines.append(f"    autoscaling:")
    lines.append(f"      min_replicas: {replicas_min}")
    lines.append(f"      max_replicas: {replicas_max}")
    lines.append(f"      cpu_target_percent: {rng.choice([50, 60, 70, 80])}")
    lines.append(f"      memory_target_percent: {rng.choice([60, 70, 80, 85])}")
    lines.append(f"      scale_down_stabilization_s: {rng.choice([60, 120, 300])}")
    lines.append(f"    environment:")
    for k, v in env_vars.items():
        lines.append(f"      {k}: \"{v}\"")
    lines.append(f"    dependencies:")
    for dep in deps:
        lines.append(f"      - {dep}")

    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <output_file>", file=sys.stderr)
        sys.exit(1)

    output_path = sys.argv[1]
    rng = random.Random(SEED)

    sections = []
    sections.append("services:")

    for name in SERVICE_NAMES:
        if name == NEEDLE_SERVICE:
            section = generate_service_config(rng, name, connection_string=NEEDLE_CONNECTION_STRING)
        else:
            section = generate_service_config(rng, name)
        sections.append(section)

    content = "\n".join(sections) + "\n"

    with open(output_path, "w") as f:
        f.write(content)

    size_bytes = len(content.encode("utf-8"))
    size_tokens_approx = size_bytes // 4

    if NEEDLE_CONNECTION_STRING not in content:
        print("ERROR: Needle connection string not found in generated content", file=sys.stderr)
        sys.exit(1)

    if "7k3m9x" not in content:
        print("ERROR: Needle identifier 7k3m9x not found in generated content", file=sys.stderr)
        sys.exit(1)

    print(f"Generated {len(SERVICE_NAMES)} service configs")
    print(f"File size: {size_bytes:,} bytes (~{size_tokens_approx:,} tokens)")
    print(f"Needle present: {NEEDLE_CONNECTION_STRING}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()

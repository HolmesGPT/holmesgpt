# Anti-Hallucination and Anti-Cheat Patterns

Ruling out hallucinations is the most important aspect of eval design. An eval that can pass without querying real data is useless.

## Core Principle

The LLM must **discover** the answer by calling tools and processing results. It must not be able to guess the answer from the prompt, resource names, or domain knowledge alone.

## Pattern 1: Unique Random Identifiers

Embed random strings that cannot be guessed. The `expected_output` checks for these identifiers.

```yaml
# before_test injects a unique code into test data
# The LLM must discover it by querying
expected_output:
  - "Must report the verification code: HOLMES-EVAL-7x9k2m4p"
  - "Must include the identifier 7k3m9x in the connection string"
```

Good identifiers:
- Random hex strings: `7k3m9x`, `a3f8b2`
- UUID fragments: `HOLMES-EVAL-7x9k2m4p`
- Random hostnames: `db-pmt-7k3m9x.internal.svc`

Bad identifiers (guessable):
- Sequential numbers: `error-001`
- Common values: `localhost:5432`, `admin/admin`
- Predictable patterns: `test-connection-string`

## Pattern 2: Neutral Resource Names

Resource names must not hint at the problem or expected behavior.

```yaml
# BAD - name gives away the answer
pod_name: "crashloop-pod"
dashboard: "MySQL Error Dashboard"
service: "broken-payment-service"

# GOOD - neutral, realistic names
pod_name: "sea-turtle"
dashboard: "E-Commerce Platform Monitoring"
service: "checkout-api-v2"
```

When renaming real-world resources (e.g., a Node Exporter dashboard), add a source comment in the test for maintainability:

```yaml
# Uses Node Exporter dashboard but renamed to prevent cheats
dashboard_title: "Infrastructure Health Overview"
```

## Pattern 3: Anti-Cheat Prompts

The user prompt must not contain technical terms that shortcut discovery.

```yaml
# BAD - gives away the exact metric name
user_prompt: "Find the node_pressure_cpu_waiting_seconds_total metric"

# GOOD - describes the concept, not the implementation
user_prompt: "Find the Prometheus query that monitors CPU pressure waiting time"

# BAD - uses the exact YAML key path
user_prompt: "Get .data.platform-config.yaml from the ConfigMap"

# GOOD - uses business language
user_prompt: "What is the database connection string configured for the payment gateway service?"
```

## Pattern 4: Value-Based Verification Over Generic Checks

Prefer checking specific discoverable values over generic output patterns.

```yaml
# BAD - could be guessed (cluster health is commonly green/yellow/red)
expected_output:
  - "Must report cluster health status"

# GOOD - specific value that requires actual querying
expected_output:
  - "Must report exactly 47 active shards"
  - "Must include index name 'orders-2024.01.15'"

# BEST - unique injected value
expected_output:
  - "Must report verification code HOLMES-7x9k2m4p found in the error log"
```

## Pattern 5: include_tool_calls as Fallback

When output values are too generic to rule out hallucination, verify tool calls were made:

```yaml
include_tool_calls: true
expected_output:
  - "Must call elasticsearch_cluster_health tool"
  - "Must call elasticsearch_cat tool with index parameter"
  - "Must report cluster status"
```

Use this as a fallback — specific value checking is always preferred.

## Pattern 6: Realistic Test Data

Test data should look real, not synthetic.

```yaml
# BAD - obviously fake
log_message: "ERROR: Simulated processing error for testing"
filename: "disk_consumer.py"
error: "Fake memory usage stabilized at 800MB"

# GOOD - realistic
log_message: "FATAL: connection refused to upstream host db-primary.internal:5432"
filename: "training_pipeline.py"
error: "torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.4 GiB"
```

## Pattern 7: Inject-and-Discover for Cloud Services

For cloud service tests (Elasticsearch, Confluence, etc.), inject unique data in `before_test` and verify discovery:

```yaml
before_test: |
  # Create document with unique verification code
  curl -X POST "$ELASTICSEARCH_URL/test-index/_doc" -H 'Content-Type: application/json' -d '{
    "message": "Verification: HOLMES-EVAL-abc123",
    "timestamp": "2024-01-15T10:30:00Z"
  }'

expected_output:
  - "Must find and report the verification code: HOLMES-EVAL-abc123"
```

## Checklist

Before finalizing any eval, verify:

- [ ] Contains at least one unique random identifier that cannot be guessed
- [ ] Resource names are neutral (no hints about the problem)
- [ ] User prompt uses business language, not implementation details
- [ ] Expected output checks specific discoverable values
- [ ] Test data looks realistic, not synthetic
- [ ] `expected_output` is invisible to the LLM (only the evaluator sees it)
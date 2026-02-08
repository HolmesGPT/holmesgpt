# test_case.yaml Complete Field Reference

## Required Fields

### `user_prompt` (str or list[str])
The question Holmes will answer. Can be a single string or a list of variants.

```yaml
# Single prompt
user_prompt: "What is the status of pods in namespace app-42?"

# Multiple variants (test runs once per variant)
user_prompt:
  - "What pods are failing in app-42?"
  - "Show me broken pods in the app-42 namespace"
```

**Prompt design rules:**
- Be specific about what to report ("tell me the title, panels, and time range")
- Don't use technical terms that give away the answer
- Test discovery, not recognition — Holmes should search/analyze, not guess from context

### `expected_output` (str or list[str])
Criteria for the LLM-as-judge evaluator. Each item is a criterion the answer must satisfy.

```yaml
expected_output:
  - "Must report the exact connection string: postgresql://admin@db-xyz.svc:5432/mydb"
  - "Must include the unique identifier xyz in the response"
```

**Critical:** `expected_output` is invisible to the LLM being tested. Only the evaluator (LLM-as-judge) sees it. This means unique verification codes placed here are safe — the tested LLM must discover them by querying.

## Optional Fields

### `tags` (list[str])
Test categorization. Only use tags from `pyproject.toml` markers. Invalid tags cause collection failures.

Common tags:
- Difficulty: `easy`, `medium`, `hard`
- Infrastructure: `kubernetes`, `prometheus`, `loki`, `grafana-dashboard`, `elasticsearch`, `datadog`
- Type: `question-answer`, `chain-of-causation`, `logs`, `metrics`, `traces`
- Special: `regression` (must always pass), `no-cicd` (skip in CI), `fast` (quick tests)

### `before_test` (str)
Bash script executed before the test. Runs from the test directory via `/bin/bash`.

### `after_test` (str)
Bash script for cleanup. Runs after the test completes (or fails).

### `setup_timeout` (int)
Override default setup timeout (300 seconds). Set higher for complex infrastructure:

```yaml
setup_timeout: 600  # 10 minutes for complex setups
```

### `port_forwards` (list[dict])
Kubernetes port forwarding for accessing services:

```yaml
port_forwards:
  - namespace: app-177
    service: grafana
    local_port: 10177    # Must be unique across all tests
    remote_port: 3000
```

Port forwards are set up after `before_test` completes and torn down after `after_test`. Local ports must be unique. Port conflicts are detected before setup and cause the test to be skipped. The `port-forward` tag is automatically added when this field is present.

### `include_tool_calls` (bool, default: false)
Include tool call names in the evaluation context. Use when expected values are too generic to rule out hallucination, and verifying the tool was called adds confidence:

```yaml
include_tool_calls: true
expected_output:
  - "Must call elasticsearch_cluster_health tool"
  - "Must report cluster status"
```

Prefer specific answer checking when possible. Tool call verification is a fallback.

### `runbooks` (dict)
Custom runbook catalog. Set to `{}` to disable runbooks entirely:

```yaml
runbooks: {}  # No runbooks available
```

### `evaluation` (dict)
Scoring configuration:

```yaml
evaluation:
  correctness: 1  # Weight for correctness scoring (default: 1.0)
```

### `conversation_history` (list[dict])
Prior messages for testing multi-turn conversations:

```yaml
conversation_history:
  - role: user
    content: "What pods are running?"
  - role: assistant
    content: "There are 3 pods running in the default namespace."
```

### `test_env_vars` (dict)
Environment variables injected during test execution:

```yaml
test_env_vars:
  CUSTOM_API_KEY: "test-key-123"
```

### `mock_policy` (str)
Controls HTTP mocking: `"always_mock"`, `"never_mock"`, or `"inherit"` (default).

### `skip` / `skip_reason` (bool / str)
Skip test execution with a reason:

```yaml
skip: true
skip_reason: "Waiting for upstream fix"
```

### `cluster_name` (str)
Override the Kubernetes cluster name used in the test.

### `allow_toolset_failures` (bool, default: false)
Allow toolset prerequisite failures without failing the test.

## toolsets.yaml Structure

Separate file in the test directory. When present, only listed toolsets are enabled:

```yaml
toolsets:
  kubernetes/core:
    enabled: true
  grafana/dashboards:
    enabled: true
    config:
      url: http://localhost:10177
      api_key: ""
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: http://localhost:10033
```

Environment variable references use Jinja2 syntax:

```yaml
toolsets:
  elasticsearch/query:
    enabled: true
    config:
      url: "{{ env.ELASTICSEARCH_URL }}"
      api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
```

## Complete Example

```yaml
user_prompt: "What is the database connection string for the payment service in the platform-config ConfigMap in namespace app-212?"

expected_output:
  - "Must report the exact connection string: postgresql://admin@db-7k3m9x.svc:5432/transactions"
  - "Must include the unique identifier 7k3m9x"

tags:
  - kubernetes
  - question-answer
  - hard

setup_timeout: 120

before_test: |
  set -e
  kubectl create namespace app-212 --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -f manifest.yaml -n app-212
  # Verify with retry loop
  for i in $(seq 1 60); do
    if kubectl get pod -l app=myapp -n app-212 -o jsonpath='{.items[0].status.phase}' 2>/dev/null | grep -q Running; then
      echo "Pod ready"
      break
    fi
    sleep 1
  done

after_test: |
  kubectl delete namespace app-212 --ignore-not-found
```
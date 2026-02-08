# Adding a New Eval

Create test cases that measure HolmesGPT's diagnostic accuracy and help track improvements over time.

## Prerequisites

Install HolmesGPT python dependencies:

```bash
poetry install --with=dev
```

## Quick Start: Running Your First Eval

Try running an existing eval to understand how the system works. We'll use [eval 80_pvc_storage_class_mismatch](https://github.com/HolmesGPT/holmesgpt/tree/master/tests/llm/fixtures/test_ask_holmes/80_pvc_storage_class_mismatch) as an example:

```bash
# Run eval #80 with Claude Sonnet 4.5 (this specific eval passes reliably with Sonnet 4.5)
RUN_LIVE=true MODEL=anthropic/claude-sonnet-4-20250514 \
  CLASSIFIER_MODEL=gpt-4.1 \
  poetry run pytest tests/llm/test_ask_holmes.py -k "80_pvc_storage_class_mismatch"

# Compare with GPT-4o (may not pass as reliably)
RUN_LIVE=true MODEL=gpt-4o \
  poetry run pytest tests/llm/test_ask_holmes.py -k "80_pvc_storage_class_mismatch"

# Compare with GPT-4.1 (may not pass as reliably)
RUN_LIVE=true MODEL=gpt-4.1 \
  poetry run pytest tests/llm/test_ask_holmes.py -k "80_pvc_storage_class_mismatch"

# Test multiple models at once to compare performance
RUN_LIVE=true MODEL=gpt-4o,gpt-4.1,anthropic/claude-sonnet-4-20250514 \
  CLASSIFIER_MODEL=gpt-4.1 \
  poetry run pytest tests/llm/test_ask_holmes.py -k "80_pvc_storage_class_mismatch"
```

**Note:** Eval #80 demonstrates how different models perform differently - Sonnet 4.5 passes this specific eval reliably while weaker models like GPT-4o and GPT-4.1 may struggle with this scenario.

## Quick Start

1. Create test folder: `tests/llm/fixtures/test_ask_holmes/99_your_test/`

2. Create `test_case.yaml`:
```yaml
user_prompt: 'Is the nginx pod healthy?'
expected_output:
  - nginx pod is healthy
before_test: kubectl apply -f ./manifest.yaml
after_test: kubectl delete -f ./manifest.yaml
```

3. Create `manifest.yaml` with your test scenario:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: nginx
  namespace: default
spec:
  containers:
  - name: nginx
    image: nginx:latest
    ports:
    - containerPort: 80
```

4. Run test:
```bash
# With GPT-4.1
RUN_LIVE=true MODEL=gpt-4.1 \
  poetry run pytest tests/llm/test_ask_holmes.py -k "99_your_test" -v

# With Claude Sonnet 4.5 (must set CLASSIFIER_MODEL since Anthropic models can't be used as classifiers)
RUN_LIVE=true MODEL=anthropic/claude-sonnet-4-20250514 \
  CLASSIFIER_MODEL=gpt-4.1 \
  poetry run pytest tests/llm/test_ask_holmes.py -k "99_your_test" -v
```

**Note on CLASSIFIER_MODEL:** An LLM judges whether tests pass. Only OpenAI models (like `gpt-4.1`) work as classifiers. Set `CLASSIFIER_MODEL=gpt-4.1` explicitly when using Anthropic models. For OpenAI models, it defaults to `MODEL`.

## test_case.yaml Configuration

Configure your test by defining these fields in `test_case.yaml`:

### Required Fields
- `user_prompt`: Question for Holmes
- `expected_output`: List of required elements in response
- `before_test`/`after_test`: Setup/teardown commands (run with `RUN_LIVE=true`)

### Optional Fields
- `tags`: List of test markers (e.g., `[easy, kubernetes, logs]`)
- `skip`: Boolean to skip test
- `skip_reason`: Explanation why test is skipped
- `mocked_date`: Override system time for test (e.g., `"2025-06-23T11:34:00Z"`)
- `cluster_name`: Specify kubernetes cluster name
- `include_files`: List of files to include in context (like CLI's `--include` flag)
- `runbooks`: Override runbook catalog:
  ```yaml
  runbooks:
    catalog:
      - description: "Database Connection Troubleshooting"
        link: "database_troubleshooting.md"
        update_date: "2025-07-01"
  ```
- `toolsets`: Configure toolsets (can also use separate `toolsets.yaml` file):
  ```yaml
  toolsets:
    aws/lambda:
      enabled: true
    aws/cloudwatch:
      enabled: false
  ```
- `port_forwards`: Configure port forwarding for tests
- `test_env_vars`: Environment variables during test execution
- `mock_policy`: Control mock behavior (`always_mock`, `never_mock`, or `inherit`)
- `conversation_history`: For multi-turn conversation tests
- `expected_sections`: For investigation tests only

## Mock Data Usage

**Live evaluations (`RUN_LIVE=true`) are strongly preferred** because they're more reliable and accurate.

### Generating Mock Data

```bash
# Generate mocks for one test
poetry run pytest tests/llm/test_ask_holmes.py -k "your_test" --generate-mocks

# Remove any existing mocks for your test and generate them from scratch
poetry run pytest tests/llm/test_ask_holmes.py -k "your_test" --regenerate-all-mocks
```

Mock files are named: `{tool_name}_{context}.txt`

### Mock Data Guidelines

When creating mock data:

- Never generate mock data manually - always use `--generate-mocks` with live execution
- Mock data should match real-world responses exactly
- Include all fields that would be present in actual responses
- Maintain proper timestamps and data relationships

### Important Notes About Mocks

- **Mock data captures only one investigation path** - LLMs may take completely different approaches
- Tests with mocks often fail when the LLM chooses a different but equally valid investigation strategy
- Mock execution misses the dynamic nature of real troubleshooting
- Always develop and validate tests with `RUN_LIVE=true`
- Mock data becomes stale as APIs and tool behaviors evolve

## Advanced Features

### Toolsets Configuration

You can configure which toolsets are available during your test in two ways:

1. **Inline in test_case.yaml**:
```yaml
toolsets:
  kubernetes/core:
    enabled: true
  aws/cloudwatch:
    enabled: false
```

2. **Separate toolsets.yaml file** (preferred for complex configurations):
```yaml
# toolsets.yaml
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: http://loki.app-143.svc.cluster.local:3100
      api_key: ""
  kafka/admin:
    enabled: true
    config:
      kafka_clusters:
        - name: "kafka"
          kafka_broker: "kafka:9092"
```

### Port Forwarding

Some tests require access to services that are not directly exposed. You can configure port forwards that will be automatically set up and torn down for your test:

```yaml
port_forwards:
  - namespace: app-01
    service: rabbitmq
    local_port: 15672
    remote_port: 15672
  - namespace: app-01
    service: prometheus
    local_port: 9090
    remote_port: 9090
```

**Note**: Use unique local ports across all tests to avoid conflicts

Port forwards are:

- Automatically started before any tests run
- Shared across all tests in a session to avoid conflicts
- Always cleaned up after tests complete, even if tests are interrupted
- Run regardless of `--skip-setup` or `--skip-cleanup` flags

**Important notes:**

- Use unique local ports across all tests to avoid conflicts
- Port forwards persist for the entire test session
- If a port is already in use, the test will fail with helpful debugging information
- Use `lsof -ti :<port>` to find processes using a port
- Port forwards work with both mock and live (`RUN_LIVE=true`) test modes

### Toolset Configuration

Create `toolsets.yaml` to customize available tools:

```yaml
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: "http://custom-prometheus:9090"
  grafana/dashboards:
    enabled: false  # Disable specific toolsets
```

### Mock Policy

```yaml
mock_policy: "inherit"  # Options: inherit (default), never_mock, always_mock
```

- `inherit`: Use global settings
- `never_mock`: Force live execution (skipped if RUN_LIVE not set)
- `always_mock`: Always use mocks (avoid when possible)

### Custom Runbooks

```yaml
runbooks:
  catalog:
    - description: "DNS troubleshooting"
      link: "dns-runbook.md"  # Place .md file in test directory
```

Options:

- No field: Use default runbooks
- `runbooks: {}`: No runbooks available
- `runbooks: {catalog: [...]}`: Custom catalog

## Tagging

Evals support tags for organization, filtering, and reporting purposes. Tags help categorize tests by their characteristics and enable selective test execution.

### Available Tags

The valid tags are defined in the test constants file in the repository.

Some examples

- `logs` - Tests HolmesGPT's ability to find and interpret logs correctly
- `context_window` - Tests handling of data that exceeds the LLM's context window
- `synthetic` - Tests that use manually generated mock data (cannot be run live)
- `datetime` - Tests date/time handling and interpretation
- etc.

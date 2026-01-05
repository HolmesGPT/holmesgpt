# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

HolmesGPT is an AI-powered troubleshooting agent that connects to observability platforms (Kubernetes, Prometheus, Grafana, etc.) to automatically diagnose and analyze infrastructure and application issues. It uses an agentic loop to investigate problems by calling tools to gather data from multiple sources.

## Development Commands

### Environment Setup
```bash
# Install dependencies with Poetry
poetry install

# Install pre-commit hooks
poetry run pre-commit install
```

### Testing

```bash
# Install test dependencies with Poetry
poetry install --with dev
```

```bash
# Run all non-LLM tests (unit and integration tests)
make test-without-llm
poetry run pytest tests -m "not llm"

# Run LLM evaluation tests (requires API keys)
make test-llm-ask-holmes          # Test single-question interactions
make test-llm-investigate         # Test AlertManager investigations
poetry run pytest tests/llm/ -n 6 -vv  # Run all LLM tests in parallel

# Run pre-commit checks (includes ruff, mypy, poetry validation)
make check
poetry run pre-commit run -a
```

### Code Quality
```bash
# Format code with ruff
poetry run ruff format

# Check code with ruff (auto-fix issues)
poetry run ruff check --fix

# Type checking with mypy
poetry run mypy
```

## Architecture Overview

### Core Components

**CLI Entry Point** (`holmes/main.py`):
- Typer-based CLI with subcommands for `ask`, `investigate`, `toolset`
- Handles configuration loading, logging setup, and command routing

** Interactive mode for CLI** (`holmes/interactive.py`):
- Handles interactive mode for `ask` subcommand
- Implements slash commands

**Configuration System** (`holmes/config.py`):
- Loads settings from `~/.holmes/config.yaml` or via CLI options
- Manages API keys, model selection, and toolset configurations
- Factory methods for creating sources (AlertManager, Jira, PagerDuty, etc.)

**Core Investigation Engine** (`holmes/core/`):
- `tool_calling_llm.py`: Main LLM interaction with tool calling capabilities
- `investigation.py`: Orchestrates multi-step investigations with runbooks
- `toolset_manager.py`: Manages available tools and their configurations
- `tools.py`: Tool definitions and execution logic

**Plugin System** (`holmes/plugins/`):
- **Sources**: AlertManager, Jira, PagerDuty, OpsGenie integrations
- **Toolsets**: Kubernetes, Prometheus, Grafana, AWS, Docker, etc.
- **Prompts**: Jinja2 templates for different investigation scenarios
- **Destinations**: Slack integration for sending results

### Key Patterns

**Toolset Architecture**:
- Each toolset is a YAML file defining available tools and their parameters
- Tools can be Python functions or bash commands with safety validation
- Toolsets are loaded dynamically and can be customized via config files
- **Important**: All toolsets MUST return detailed error messages from underlying APIs to enable LLM self-correction
  - Include the exact query/command that was executed
  - Include time ranges, parameters, and filters used
  - Include the full API error response (status code and message)
  - For "no data" responses, specify what was searched and where

**Thin API Wrapper Pattern for Python Toolsets**:
- Reference implementation: `servicenow_tables/servicenow_tables.py`
- Use `requests` library for HTTP calls (not specialized client libraries like `opensearchpy`)
- Simple config class with Pydantic validation
- Health check in `prerequisites_callable()` method
- Each tool is a thin wrapper around a single API endpoint

**Server-Side Filtering is Critical**:
- **Never return unbounded data from APIs** - this causes token overflow
- Always include filter parameters on tools that query collections (e.g., `index` parameter for Elasticsearch _cat APIs)
- Example problem: `opensearch_list_shards` returned ALL shards → 25K+ tokens on large clusters
- Example fix: `elasticsearch_cat` tool requires `index` parameter for shards/segments endpoints
- When server-side filtering is not possible, use `JsonFilterMixin` (see `json_filter_mixin.py`) to add `max_depth` and `jq` parameters for client-side filtering

**Toolset Config Backwards Compatibility**:
When renaming config fields in a toolset, maintain backwards compatibility using Pydantic's `extra="allow"`:

```python
# ✅ DO: Use extra="allow" to accept deprecated fields without polluting schema
class MyToolsetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Only define current field names in schema
    new_field_name: int = 10

    @model_validator(mode="after")
    def handle_deprecated_fields(self):
        extra = self.model_extra or {}
        deprecated = []

        # Map old names to new names
        if "old_field_name" in extra:
            self.new_field_name = extra["old_field_name"]
            deprecated.append("old_field_name -> new_field_name")

        if deprecated:
            logging.warning(f"Deprecated config names: {', '.join(deprecated)}")
        return self

# ❌ DON'T: Define deprecated fields in schema with Optional[None]
class BadConfig(BaseModel):
    new_field_name: int = 10
    old_field_name: Optional[int] = None  # Pollutes schema, shows in model_dump()
```

Benefits of `extra="allow"` approach:
- Schema only shows current field names
- `model_dump()` returns clean output without deprecated fields
- Old configs still work (backwards compatible)
- Deprecation warnings guide users to update

See `prometheus/prometheus.py` PrometheusConfig for a complete example.

**LLM Integration**:
- Uses LiteLLM for multi-provider support (OpenAI, Anthropic, Azure, etc.)
- Structured tool calling with automatic retry and error handling
- Context-aware prompting with system instructions and examples

**Investigation Flow**:
1. Load user question/alert
2. Select relevant toolsets based on context
3. Execute LLM with available tools
4. LLM calls tools to gather data
5. LLM analyzes results and provides conclusions
6. Optionally write results back to source system

## Testing Framework

**Three-tier testing approach**:

1. **Unit Tests** (`tests/`): Standard pytest tests for individual components
2. **Integration Tests**: Test toolset integrations
3. **LLM Evaluation Tests** (`tests/llm/`): End-to-end tests using fixtures

**LLM Test Structure**:
- `tests/llm/fixtures/test_ask_holmes/`: 53+ test scenarios with YAML configs
- Each test has expected outputs validated by LLM-as-judge
- Supports Braintrust integration for result tracking

**Running LLM Tests**:
```bash
# Run all LLM tests
poetry run pytest -m 'llm' --no-cov

# Run specific test - IMPORTANT: Use -k flag, NOT full test path!
# CORRECT - use -k flag with test name pattern:
poetry run pytest -m 'llm' -k "09_crashpod" --no-cov
poetry run pytest tests/llm/test_ask_holmes.py -k "114_checkout_latency" --no-cov

# WRONG - DO NOT specify full test path with brackets:
# poetry run pytest tests/llm/test_ask_holmes.py::test_ask_holmes[114_checkout_latency_tracing_rebuild-gpt-4o]
# This syntax fails when environment variables are passed!

# Run regression tests (easy marker) - all should pass with ITERATIONS=10
poetry run pytest -m 'llm and easy' --no-cov
ITERATIONS=10 poetry run pytest -m 'llm and easy' --no-cov

# Run tests in parallel
poetry run pytest tests/llm/ -n 6

# Test with different models
# Note: When using Anthropic models, set CLASSIFIER_MODEL to OpenAI (Anthropic not supported as classifier)
MODEL=anthropic/claude-sonnet-4-20250514 CLASSIFIER_MODEL=gpt-4.1 poetry run pytest tests/llm/test_ask_holmes.py -k "test_name"

# Setting environment variables - IMPORTANT:
# Environment variables must be set BEFORE the poetry command, NOT as pytest arguments
# CORRECT:
EVAL_SETUP_TIMEOUT=600 poetry run pytest -m 'llm' -k "slow_test" --no-cov

# WRONG - this won't work:
# poetry run pytest EVAL_SETUP_TIMEOUT=600 -m 'llm' -k "slow_test"
```

### Evaluation CLI Reference

**Custom Pytest Flags**:
- `--skip-setup`: Skip before_test commands (useful for iterative testing)
- `--skip-cleanup`: Skip after_test commands (useful for debugging)

**Environment Variables**:
- `MODEL`: LLM model(s) to use - supports comma-separated list (e.g., `gpt-4.1` or `gpt-4.1,anthropic/claude-sonnet-4-20250514`)
- `CLASSIFIER_MODEL`: Model for scoring answers (defaults to MODEL)
- `RUN_LIVE=true`: Execute real commands (now enabled by default)
- `ITERATIONS=<number>`: Run each test multiple times
- `UPLOAD_DATASET=true`: Sync dataset to Braintrust
- `EXPERIMENT_ID`: Custom experiment name for tracking
- `BRAINTRUST_API_KEY`: Enable Braintrust integration
- `ASK_HOLMES_TEST_TYPE`: Controls message building flow in ask_holmes tests
  - `cli` (default): Uses `build_initial_ask_messages` like the CLI ask() command (skips conversation history tests)
  - `server`: Uses `build_chat_messages` with ChatRequest for server-style flow

**Common Evaluation Patterns**:

```bash
# Run tests multiple times for reliability
ITERATIONS=100 poetry run pytest tests/llm/test_ask_holmes.py -k "flaky_test"

# Model comparison workflow
EXPERIMENT_ID=gpt41_baseline MODEL=gpt-4.1 poetry run pytest tests/llm/ -n 6
EXPERIMENT_ID=claude_opus41_test MODEL=anthropic/claude-opus-4-1-20250805 CLASSIFIER_MODEL=gpt-4.1 poetry run pytest tests/llm/ -n 6

# Debug with verbose output
poetry run pytest -vv -s tests/llm/test_ask_holmes.py -k "failing_test" --no-cov

# List tests by marker
poetry run pytest -m "llm and not network" --collect-only -q

# Test marker combinations
poetry run pytest -m "llm and easy" --no-cov  # Regression tests
poetry run pytest -m "llm and not easy" --no-cov  # Non-regression tests
```

## Tag Management Guidelines

**Before adding new tags**:
1. Check existing tags in `pyproject.toml` markers section
2. Ask user permission for new tags  
3. Use descriptive, hyphenated names (e.g., `grafana-dashboard`, not `grafana_dashboard`)

**Tag naming conventions**:
- Service-specific: `grafana-dashboard`, `prometheus-metrics`, `loki`
- Functionality: `question-answer`, `chain-of-causation` 
- Difficulty: `easy`, `medium`, `hard`
- Infrastructure: `kubernetes`, `database`, `traces`

**Adding new tags workflow**:
1. Add tag to `pyproject.toml` markers section with description
2. Apply tag to relevant test files
3. Verify tag filtering works: `pytest -m "new-tag" --collect-only`

**Available Test Markers (same as eval tags)**:
Check in pyproject.toml and NEVER use a marker/tag that doesn't exist there. Ask the user before adding a new one.

**Important**: The `regression` marker identifies critical tests that must always pass in CI/CD. The `easy` marker is a legacy marker that contains broader regression tests.

**Test Infrastructure Notes**:
- All test state tracking uses pytest's `user_properties` to ensure compatibility with pytest-xdist parallel execution
- Test results are stored in `user_properties` and aggregated in the terminal summary
- This design ensures tests work correctly when run in parallel with `-n` flag
- **Important for LLM tests**: Each test must use a dedicated namespace `app-<testid>` (e.g., `app-01`, `app-02`) to prevent conflicts when tests run simultaneously
- All pod names must be unique across tests (e.g., `giant-narwhal`, `blue-whale`, `sea-turtle`) - never reuse pod names between tests
- **Resource naming in evals**: Never use names that hint at the problem or expected behavior (e.g., avoid `broken-pod`, `test-project-that-does-not-exist`, `crashloop-app`). Use neutral names that don't give away what the LLM should discover

## Configuration

**Config File Location**: `~/.holmes/config.yaml`

**Key Configuration Sections**:
- `model`: LLM model to use (default: gpt-4.1)
- `api_key`: LLM API key (or use environment variables)
- `custom_toolsets`: Override or add toolsets
- `custom_runbooks`: Add investigation runbooks
- Platform-specific settings (alertmanager_url, jira_url, etc.)

**Environment Variables**:
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`: LLM API keys
- `OPENROUTER_API_KEY`: Alternative LLM provider via OpenRouter (domain: `api.openrouter.ai`)
- `MODEL`: Override default model(s) - supports comma-separated list
- `RUN_LIVE`: Use live tools in tests (strongly recommended)
- `BRAINTRUST_API_KEY`: For test result tracking and CI/CD report generation
- `BRAINTRUST_ORG`: Braintrust organization name (default: "robustadev")
- `ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY`: For Elasticsearch/OpenSearch cloud testing

## Development Guidelines

**Code Quality**:
- Use Ruff for formatting and linting (configured in pyproject.toml)
- Type hints required (mypy configuration in pyproject.toml)
- Pre-commit hooks enforce quality checks
- **ALWAYS place Python imports at the top of the file**, not inside functions or methods

**Testing Requirements**:
- All new features require unit tests
- New toolsets require integration tests
- Complex investigations should have LLM evaluation tests
- Maintain 40% minimum test coverage
- **Live execution is now enabled by default** to ensure tests match real-world behavior

**Pull Request Process**:
- PRs require maintainer approval
- Pre-commit hooks must pass
- LLM evaluation tests run automatically in CI
- Keep PRs focused and include tests
- **ALWAYS use `git commit -s`** to sign off commits (required for DCO)

**File Structure Conventions**:
- Toolsets: `holmes/plugins/toolsets/{name}.yaml` or `{name}/`
- Prompts: `holmes/plugins/prompts/{name}.jinja2`
- Tests: Match source structure under `tests/`

## Security Notes

- All tools have read-only access by design
- Bash toolset validates commands for safety
- No secrets should be committed to repository
- Use environment variables or config files for API keys
- RBAC permissions are respected for Kubernetes access

## Eval Notes

### Creating New Eval Tests

**Test Structure:**
- Use sequential test numbers: check existing tests for next available number
- Required files: `test_case.yaml`, infrastructure manifests, `toolsets.yaml` (if needed)
- Use dedicated namespace per test: `app-<testid>` (e.g., `app-177`)
- All resource names must be unique across tests to prevent conflicts

**Tags:**
- **CRITICAL**: Only use valid tags from `pyproject.toml` - invalid tags cause test collection failures
- Check existing tags before adding new ones, ask user permission for new tags

**Cloud Service Evals (No Kubernetes Required)**:
- Evals can test against cloud services (Elasticsearch, external APIs) directly via environment variables
- Faster setup (<30 seconds vs minutes for K8s infrastructure)
- `before_test` creates test data in the cloud service, `after_test` cleans up
- Use `toolsets.yaml` to configure the toolset with env var references: `url: "{{ env.ELASTICSEARCH_URL }}"`
- **CI/CD secrets**: When adding evals for a new integration, you must add the required environment variables to `.github/workflows/eval-regression.yaml` in the "Run tests" step. Tell the user which secrets they need to add to their GitHub repository settings (e.g., `ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY`).
- **HTTP request passthrough**: The root `conftest.py` has a `responses` fixture with `autouse=True` that mocks ALL HTTP requests by default. When adding a new cloud integration, you MUST add the service's URL pattern to the passthrough list in `conftest.py` (search for `rsps.add_passthru`). Use `re.compile()` for pattern matching (e.g., `rsps.add_passthru(re.compile(r"https://.*\.cloud\.es\.io"))`).

**User Prompts & Expected Outputs:**
- **Be specific**: Test exact values like `"The dashboard title is 'Home'"` not generic `"Holmes retrieves dashboard"`
- **Match prompt to test**: User prompt must explicitly request what you're testing
  - BAD: `"Get the dashboard"`
  - GOOD: `"Get the dashboard and tell me the title, panels, and time range"`
- **Anti-cheat prompts**: Don't use technical terms that give away solutions
  - BAD: `"Find node_exporter metrics"`
  - GOOD: `"Find CPU pressure monitoring queries"`
- **Test discovery, not recognition**: Holmes should search/analyze, not guess from context
- **Ruling out hallucinations is paramount**: When choosing between test approaches, prefer the one that rules out hallucinations:
  - **Best**: Check specific values that can only be discovered by querying (e.g., unique IDs, injected error codes, exact counts)
  - **Acceptable**: Use `include_tool_calls: true` to verify the tool was called when output values are too generic to rule out hallucinations
  - **Bad**: Check generic output patterns that an LLM could plausibly guess (e.g., "cluster status is green/yellow/red", "has N nodes")
- **`include_tool_calls: true`**: Use when expected output is too generic to be hallucination-proof. Prefer specific answer checking when possible, but verifying tool calls is better than a test that can't rule out hallucinations.
  ```yaml
  # Use when values are generic (cluster health could be guessed)
  include_tool_calls: true
  expected_output:
    - "Must call elasticsearch_cluster_health tool"
    - "Must report cluster status"
  ```

**Infrastructure Setup:**
- **Don't just test pod readiness** - verify actual service functionality
- Poll real API endpoints and check for expected content (e.g., `"title":"Home"`, `"type":"welcome"`)
- **CRITICAL**: Use `exit 1` when setup verification fails to fail the test early
- **Never use `:latest` container tags** - use specific versions like `grafana/grafana:12.3.1`

### Running and Testing Evals

## 🚨 CRITICAL: Always Test Your Changes

**NEVER submit test changes without verification**:

### Required Testing Workflow:
1. **Setup Phase**: `poetry run pytest -k "test_name" --only-setup --no-cov`
2. **Full Test**: `poetry run pytest -k "test_name" --no-cov`
3. **Verify Results**: Ensure 100% pass rate and expected behavior

### When to Test:
- ✅ After creating new tests
- ✅ After modifying existing tests  
- ✅ After refactoring shared infrastructure
- ✅ After performance optimizations
- ✅ After adding/changing tags

### Red Flags - Never Skip Testing:
- ❌ "The changes look good" without running
- ❌ "It's just a small change"
- ❌ "I'll test it later"

**Testing is Part of Development**: Testing is not optional - it's an integral part of the development process. Untested code is broken code.

**Testing Methodology:**
- Phase 1: Test setup with `--only-setup` flag first
- Phase 2: Run full test after confirming setup works
- Use background execution for long tests: `nohup ... > logfile.log 2>&1 &`
- Handle port conflicts: clean up previous test port forwards before running

**Common Flags:**
- `--skip-cleanup`: Keep resources after test (useful for debugging setup)
- `--skip-setup`: Skip before_test commands (useful for iterative testing)

## Shared Infrastructure Pattern

**When to use shared infrastructure**:
- Multiple tests use the same service (Grafana, Loki, Prometheus)
- Service configuration is standardized across tests

**Implementation**:
```bash
# Create shared manifest in tests/llm/fixtures/shared/servicename.yaml
# Use in tests:
kubectl apply -f ../../shared/servicename.yaml -n app-<testid>
```

**Benefits**:
- Single place for version updates
- Consistent configuration across tests
- Reduced maintenance overhead
- Follows established pattern (Loki, Prometheus, Grafana)

## Setup Verification Best Practices

**Prefer kubectl exec over port forwarding for setup verification**:
```bash
# GOOD - kubectl exec pattern (no port conflicts)
kubectl exec -n namespace deployment/service -- wget -q -O- http://localhost:port/health

# AVOID - port forward for setup verification (causes conflicts)
kubectl port-forward svc/service port:port &
curl localhost:port/health
kill $PORTFWD_PID
```

**Performance optimization guidelines**:
- Use `sleep 1` instead of `sleep 5` for most retry loops
- Remove sleeps after straightforward operations (port forward start)
- Reduce timeout values: 60s for pod readiness, 30s for API verification
- Question every sleep - many are unnecessary

**Race Condition Handling:**
Never use bare `kubectl wait` immediately after resource creation. Use retry loops:
```bash
# WRONG - fails if pod not scheduled yet
kubectl apply -f deployment.yaml
kubectl wait --for=condition=ready pod -l app=myapp --timeout=300s

# CORRECT - retry loop handles race condition
kubectl apply -f deployment.yaml
POD_READY=false
for i in {1..60}; do
  if kubectl wait --for=condition=ready pod -l app=myapp --timeout=5s 2>/dev/null; then
    echo "✅ Pod is ready!"
    POD_READY=true
    break
  fi
  sleep 1
done
if [ "$POD_READY" = false ]; then
  echo "❌ Pod failed to become ready after 60 seconds"
  kubectl logs -l app=myapp --tail=20  # Diagnostic info
  exit 1  # CRITICAL: Fail the test early
fi
```

### Eval Best Practices

**Realism:**
- No fake/obvious logs like "Memory usage stabilized at 800MB"
- No hints in filenames like "disk_consumer.py" - use realistic names like "training_pipeline.py"
- No error messages that give away it's simulated like "Simulated processing error"
- Use real-world scenarios: ML pipelines with checkpoint issues, database connection pools
- Resource naming should be neutral, not hint at the problem (avoid "broken-pod", "crashloop-app")

**Architecture:**
- Implement full architecture even if complex (e.g., use Loki for log aggregation, not simplified alternatives)
- Proper separation of concerns (app → file → Promtail → Loki → Holmes)
- **ALWAYS use Secrets for scripts**, not inline manifests or ConfigMaps
- Use minimal resource footprints (reduce memory/CPU for test services)

**Anti-Cheat Testing Guidelines:**
- **Prevent Domain Knowledge Cheats**: Use neutral, application-specific names instead of obvious technical terms
  - Example: "E-Commerce Platform Monitoring" not "Node Exporter Full"
  - Example: "Payment Service Dashboard" not "MySQL Error Dashboard"
  - Add source comments: `# Uses Node Exporter dashboard but renamed to prevent cheats`
- **Resource Naming Rules**: Avoid hint-giving names
  - Use realistic business context: "checkout-api", "user-service", "inventory-db" 
  - Avoid obvious problem indicators: "broken-pod" → "payment-service-1"
  - Test discovery ability, not pattern recognition
- **Prompt Design**: Don't give away solutions in prompts
  - BAD: "Find the node_pressure_cpu_waiting_seconds_total query"
  - GOOD: "Find the Prometheus query that monitors CPU pressure waiting time"
  - Test Holmes's search/analysis skills, not domain knowledge shortcuts

**Configuration:**
- Custom runbooks: Add `runbooks` field in test_case.yaml (`runbooks: {}` for empty catalog)
- Custom toolsets: Create separate `toolsets.yaml` file (never put in test_case.yaml)
- Toolset config must go under `config` field:
```yaml
toolsets:
  grafana/dashboards:
    enabled: true
    config:  # All toolset-specific config under 'config'
      url: http://localhost:10177
```

## Documentation Lookup

When asked about content from the HolmesGPT documentation website (https://holmesgpt.dev/), look in the local `docs/` directory:
- Python SDK examples: `docs/installation/python-installation.md`
- CLI installation: `docs/installation/cli-installation.md`
- Kubernetes deployment: `docs/installation/kubernetes-installation.md`
- Toolset documentation: `docs/data-sources/builtin-toolsets/`
- API reference: `docs/reference/`

## MkDocs Formatting Notes

When writing documentation in the `docs/` directory:
- **Lists after headers**: Always add a blank line between a header/bold text and a list, otherwise MkDocs won't render the list properly
  ```markdown
  **Good:**

  - item 1
  - item 2

  **Bad:**
  - item 1
  - item 2
  ```

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
# IMPORTANT: Always use RUN_LIVE=true for accurate test results
# This ensures tests match real-world behavior

# Run all LLM tests
RUN_LIVE=true poetry run pytest -m 'llm' --no-cov

# Run specific test - IMPORTANT: Use -k flag, NOT full test path!
# CORRECT - use -k flag with test name pattern:
RUN_LIVE=true poetry run pytest -m 'llm' -k "09_crashpod" --no-cov
RUN_LIVE=true poetry run pytest tests/llm/test_ask_holmes.py -k "114_checkout_latency" --no-cov

# WRONG - DO NOT specify full test path with brackets:
# RUN_LIVE=true poetry run pytest tests/llm/test_ask_holmes.py::test_ask_holmes[114_checkout_latency_tracing_rebuild-gpt-4o]
# This syntax fails when environment variables are passed!

# Run regression tests (easy marker) - all should pass with ITERATIONS=10
RUN_LIVE=true poetry run pytest -m 'llm and easy' --no-cov
RUN_LIVE=true ITERATIONS=10 poetry run pytest -m 'llm and easy' --no-cov

# Run tests in parallel
RUN_LIVE=true poetry run pytest tests/llm/ -n 6

# Test with different models
# Note: When using Anthropic models, set CLASSIFIER_MODEL to OpenAI (Anthropic not supported as classifier)
RUN_LIVE=true MODEL=anthropic/claude-sonnet-4-20250514 CLASSIFIER_MODEL=gpt-4.1 poetry run pytest tests/llm/test_ask_holmes.py -k "test_name"

# Setting environment variables - IMPORTANT:
# Environment variables must be set BEFORE the poetry command, NOT as pytest arguments
# CORRECT:
RUN_LIVE=true EVAL_SETUP_TIMEOUT=600 poetry run pytest -m 'llm' -k "slow_test" --no-cov

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
- `RUN_LIVE=true`: Execute real commands (recommended for all tests)
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
RUN_LIVE=true ITERATIONS=100 poetry run pytest tests/llm/test_ask_holmes.py -k "flaky_test"

# Model comparison workflow
RUN_LIVE=true EXPERIMENT_ID=gpt41_baseline MODEL=gpt-4.1 poetry run pytest tests/llm/ -n 6
RUN_LIVE=true EXPERIMENT_ID=claude_opus41_test MODEL=anthropic/claude-opus-4-1-20250805 CLASSIFIER_MODEL=gpt-4.1 poetry run pytest tests/llm/ -n 6

# Debug with verbose output
RUN_LIVE=true poetry run pytest -vv -s tests/llm/test_ask_holmes.py -k "failing_test" --no-cov

# List tests by marker
poetry run pytest -m "llm and not network" --collect-only -q

# Test marker combinations
RUN_LIVE=true poetry run pytest -m "llm and easy" --no-cov  # Regression tests
RUN_LIVE=true poetry run pytest -m "llm and not easy" --no-cov  # Non-regression tests
```

**Available Test Markers (same as eval tags)**:
Check in pyproject.toml and NEVER use a marker/tag that doesn't exist there. Ask the user before adding a new one.

**Important**: The `easy` marker identifies regression tests - these are the most important tests that should always pass. Run with `RUN_LIVE=true ITERATIONS=10 poetry run pytest -m "llm and easy"` to ensure stability.

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
- `MODEL`: Override default model(s) - supports comma-separated list
- `RUN_LIVE`: Use live tools in tests (strongly recommended)
- `BRAINTRUST_API_KEY`: For test result tracking and CI/CD report generation
- `BRAINTRUST_ORG`: Braintrust organization name (default: "robustadev")

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
- **ALWAYS use `RUN_LIVE=true` when running LLM tests** to ensure tests match real-world behavior

**Pull Request Process**:
- PRs require maintainer approval
- Pre-commit hooks must pass
- LLM evaluation tests run automatically in CI
- Keep PRs focused and include tests

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

### Running and Testing Evals
- **ALWAYS use `RUN_LIVE=true`** when testing evals to ensure tests match real-world behavior
- Use `--skip-cleanup` when troubleshooting setup issues (resources remain after test)
- Use `--skip-setup` if you are debugging the eval itself
- **kubectl wait race condition warning**: Never use bare `kubectl wait --for=condition=ready pod -l app=foo` immediately after creating resources. This will fail with "no matching resources found" if the pod hasn't been scheduled yet. Instead, use a retry loop:
  ```bash
  # WRONG - fails if pod not scheduled yet
  kubectl apply -f deployment.yaml
  kubectl wait --for=condition=ready pod -l app=myapp --timeout=300s  # May fail immediately!

  # CORRECT - retry loop handles race condition
  kubectl apply -f deployment.yaml
  POD_READY=false
  for i in {1..60}; do
    if kubectl wait --for=condition=ready pod -l app=myapp --timeout=5s 2>/dev/null; then
      echo "✅ Pod is ready!"
      POD_READY=true
      break
    else
      echo "⏳ Attempt $i/60: Pod not ready yet, waiting 5s..."
      sleep 5
    fi
  done

  if [ "$POD_READY" = false ]; then
    echo "❌ Pod failed to become ready after 300 seconds"
    kubectl get pods -l app=myapp  # Show pod status for debugging
    exit 1
  fi
  ```
- Test cases can specify custom runbooks by adding a `runbooks` field in test_case.yaml:
  - `runbooks: {}` - No runbooks available (empty catalog)
  - `runbooks: {catalog: [...]}` - Custom runbook catalog with entries pointing to .md files in the same directory
  - If `runbooks` field is not specified, default system runbooks are used
- Test cases can specify custom toolsets by creating a separate `toolsets.yaml` file in the test directory:
  - The `toolsets.yaml` file should follow the format shown in `_EXAMPLE_01_toolsets_config/toolsets.yaml`
  - You can enable/disable specific toolsets and provide custom configurations
  - If no `toolsets.yaml` file exists, default system toolsets are used
  - Note: Do NOT put toolsets configuration directly in test_case.yaml - it must be in a separate file
- For mock data usage (rare cases), see [Using Mock Data](docs/using-mock-data.md)

**Realism is Critical:**
- No fake/obvious logs like "Memory usage stabilized at 800MB"
- No hints in filenames like "disk_consumer.py" - use realistic names like "training_pipeline.py"
- No error messages that give away it's simulated like "Simulated processing error"
- Use real-world scenarios: ML pipelines with checkpoint issues, database connection pools, diagnostic logging left enabled
- Implement realistic application behavior with proper business logic

**Code Organization Standards:**
- **ALWAYS use Secrets for scripts**, not inline manifests or ConfigMaps (prevents code visibility with kubectl describe)
- Follow existing eval patterns - check similar test cases for reference
- Resource naming should be neutral, not hint at the problem (avoid "broken-pod", "crashloop-app")
- Each test must use a dedicated namespace `app-<testid>` to prevent conflicts
- All pod names must be unique across tests

**Architectural Preferences:**
- Implement the full architecture even if it's complex (e.g., use Loki for log aggregation, not simplified alternatives)
- Don't take shortcuts - if the scenario needs Loki, implement Loki properly
- Proper separation of concerns (app → file → Promtail → Loki → Holmes)
- Use minimal resource footprints (e.g., reduce memory/CPU for Loki in tests)

**Expected Analysis Quality:**
- Holmes should identify root causes from historical data
- Expected outputs should be comprehensive but realistic
- Include specific details like file paths, configuration issues, metrics
- Don't expect Holmes to find information that isn't in the data

**Common Pitfalls to Avoid:**
- Don't use invalid tags - check pyproject.toml for the list of valid markers/tags
- Don't add convenience logs that give away the problem
- Don't write logs that directly state the issue
- Ensure historical timestamps are properly handled in logs (especially with Loki)
- Verify that data sources (like Loki) are actually working before expecting Holmes to query them

**Toolset Configuration in Evals:**
When configuring toolsets in `toolsets.yaml` files, ALL toolset-specific configuration must go under a `config` field:

```yaml
# CORRECT - toolset-specific config under 'config' field
toolsets:
  grafana/loki:
    enabled: true
    config:
      url: http://loki.app-143.svc.cluster.local:3100
      api_key: ""
      grafana_datasource_uid: "loki"

  rabbitmq/core:
    enabled: true
    config:
      clusters:
        - id: rabbitmq
          username: user
          password: "{{env.RABBITMQ_PASSWORD}}"
          management_url: http://localhost:15672

# WRONG - toolset config at top level
toolsets:
  grafana/loki:
    enabled: true
    url: http://loki.app-143.svc.cluster.local:3100
    api_key: ""
```

The only valid top-level fields for toolsets in YAML are: `enabled`, `name`, `description`, `additional_instructions`, `prerequisites`, `tools`, `docs_url`, `icon_url`, `installation_instructions`, `config`, `url` (for MCP toolsets only).

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

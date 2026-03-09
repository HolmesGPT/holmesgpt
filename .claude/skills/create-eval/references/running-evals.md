# Running and Debugging Evals

## Running Specific Tests

```bash
# IMPORTANT: Use -k flag, NOT full test path with brackets
# CORRECT:
poetry run pytest -m 'llm' -k "09_crashpod" --no-cov
poetry run pytest tests/llm/test_ask_holmes.py -k "114_checkout_latency" --no-cov

# WRONG - fails when environment variables are passed:
# poetry run pytest tests/llm/test_ask_holmes.py::test_ask_holmes[114_checkout_latency-gpt-4o]
```

## Running All Evals

```bash
# All LLM tests
poetry run pytest -m 'llm' --no-cov

# In parallel
poetry run pytest tests/llm/ -n 6

# Regression tests (easy marker) - all should pass with ITERATIONS=10
poetry run pytest -m 'llm and easy' --no-cov
ITERATIONS=10 poetry run pytest -m 'llm and easy' --no-cov
```

## Custom Pytest Flags

- `--only-setup`: Run before_test only, skip test execution and port forwards
- `--skip-setup`: Skip before_test, run tests with existing infrastructure
- `--only-cleanup`: Run after_test only
- `--skip-cleanup`: Skip after_test (keep resources for debugging)
- `--strict-setup-mode`: Fail pytest if ANY setup fails

## Environment Variables

Set environment variables BEFORE the poetry command, NOT as pytest arguments:

```bash
# CORRECT:
EVAL_SETUP_TIMEOUT=600 poetry run pytest -m 'llm' -k "slow_test" --no-cov

# WRONG:
# poetry run pytest EVAL_SETUP_TIMEOUT=600 -m 'llm' -k "slow_test"
```

Available variables:

- `MODEL`: LLM model(s) — supports comma-separated list (e.g., `gpt-4.1` or `gpt-4.1,anthropic/claude-sonnet-4-20250514`)
- `CLASSIFIER_MODEL`: Model for scoring answers (defaults to MODEL). When using Anthropic models, set this to OpenAI (Anthropic not supported as classifier)
- `ITERATIONS=<number>`: Run each test multiple times
- `EVAL_SETUP_TIMEOUT`: Setup timeout in seconds (default: 300)
- `EXPERIMENT_ID`: Custom experiment name for Braintrust tracking
- `BRAINTRUST_API_KEY`: Enable Braintrust integration
- `ASK_HOLMES_TEST_TYPE`: `cli` (default) or `server` — controls message building flow
- `SSL_VERIFY`: Set to `false` to disable SSL verification (for sandbox environments with TLS interception proxies)

## Testing with Different Models

```bash
# Test with Anthropic Claude
MODEL=anthropic/claude-sonnet-4-20250514 CLASSIFIER_MODEL=gpt-4.1 \
  poetry run pytest tests/llm/test_ask_holmes.py -k "test_name"

# RECOMMENDED: Test with Opus 4.5 via OpenRouter
MODEL=openrouter/anthropic/claude-opus-4.5 CLASSIFIER_MODEL=openrouter/openai/gpt-4.1 \
  poetry run pytest tests/llm/test_ask_holmes.py -k "test_name"

# Model comparison workflow
EXPERIMENT_ID=gpt41_baseline MODEL=gpt-4.1 \
  poetry run pytest tests/llm/ -n 6
EXPERIMENT_ID=claude_test MODEL=anthropic/claude-opus-4-1-20250805 CLASSIFIER_MODEL=gpt-4.1 \
  poetry run pytest tests/llm/ -n 6
```

## Debugging

```bash
# Verbose output with stdout
poetry run pytest -vv -s tests/llm/test_ask_holmes.py -k "failing_test" --no-cov

# Run setup only, then iterate on the test
poetry run pytest -k "test_name" --only-setup --no-cov
poetry run pytest -k "test_name" --skip-setup --no-cov  # repeat as needed

# Keep resources after test for manual inspection
poetry run pytest -k "test_name" --skip-cleanup --no-cov

# List tests by marker
poetry run pytest -m "llm and not network" --collect-only -q

# Background execution for long tests
nohup poetry run pytest -k "test_name" --no-cov > test.log 2>&1 &
```

## Tag Management

Only use tags from `pyproject.toml` markers section. Invalid tags cause test collection failures.

Tag naming conventions:
- Service-specific: `grafana-dashboard`, `prometheus`, `loki`
- Functionality: `question-answer`, `chain-of-causation`
- Difficulty: `easy`, `medium`, `hard`
- Infrastructure: `kubernetes`, `database`, `traces`

Adding a new tag:
1. Check existing tags in `pyproject.toml`
2. Ask user permission
3. Add to `pyproject.toml` markers with description
4. Verify: `pytest -m "new-tag" --collect-only`

Special markers:
- `regression`: Critical tests, must always pass in CI/CD
- `easy`: Legacy broader regression marker
- `no-cicd`: Skip in CI/CD pipeline

## SSL Issues in Sandbox Environments

```bash
SSL_VERIFY=false poetry run pytest -m "confluence" --no-cov -v
```

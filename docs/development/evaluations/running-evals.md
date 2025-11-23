# HolmesGPT Evaluations

Evaluations are automated tests that measure HolmesGPT's accuracy on real-world scenarios.

They are used to both catch regressions and measure the impact of new features.

[Example: pod crashloop eval](https://github.com/robusta-dev/holmesgpt/tree/master/tests/llm/fixtures/test_ask_holmes/09_crashpod).

## Eval Tags

Evals are tagged and grouped into categories. Two common tags are `easy` and `medium`:

* `easy` - regression tests - scenarios that HolmesGPT passes today and must continue to pass after any change
* `medium` - more challenging scenarios that push boundaries of what HolmesGPT can do

Changes to HolmesGPT are good if they allow us to promote an eval from `easy` to `medium` without increasing latency by too much.

## Getting Started

### Prerequisites

Install HolmesGPT python dependencies:

```bash
poetry install --with=dev
```

### Quick Start: Running Your First Eval

Try running a single eval to understand how the system works. We'll use [eval 80_pvc_storage_class_mismatch](https://github.com/robusta-dev/holmesgpt/tree/master/tests/llm/fixtures/test_ask_holmes/80_pvc_storage_class_mismatch) as an example:

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

**Note:** This eval demonstrates how different models perform differently - Sonnet 4.5 passes this specific eval reliably while weaker models like GPT-4o and GPT-4.1 may struggle with this scenario.

### Running Full Benchmark Suite

Once you're comfortable running individual evals, you can run the full benchmark suite to test all important evals at once. The easiest way to do this locally is using the `run_benchmarks_local.sh` script, which mirrors the exact behavior of our CI/CD workflow:

```bash
# Run with defaults (easy tests, default models, 1 iteration)
./run_benchmarks_local.sh

# Test specific models
./run_benchmarks_local.sh 'gpt-4o,anthropic/claude-sonnet-4-20250514'

# Run with custom markers and iterations
./run_benchmarks_local.sh 'gpt-4o' 'easy and kubernetes' 3

# Filter specific tests by name
./run_benchmarks_local.sh 'gpt-4o' 'easy' 1 '01_how_many_pods'

# Run with parallel workers for faster execution
./run_benchmarks_local.sh 'gpt-4o' 'easy' 1 '' 6
```

## Environment Variables

Essential variables for controlling test behavior:

| Variable | Purpose | Example |
|----------|---------|---------|
| `RUN_LIVE` | Use real tools instead of mocks | `RUN_LIVE=true` |
| `ITERATIONS` | Run each test N times | `ITERATIONS=10` |
| `MODEL` | LLM to test | `MODEL=gpt-4.1` |
| `CLASSIFIER_MODEL` | LLM for scoring (needed for Anthropic) | `CLASSIFIER_MODEL=gpt-4.1` |

## Advanced Usage

### Selecting Which Evals to Run

For more control over which evals to run, you can use pytest directly with markers (tags) or test name patterns:

```bash
# Run all easy evals (regression tests - should always pass)
RUN_LIVE=true poetry run pytest -m 'llm and easy' --no-cov

# Run challenging tests
RUN_LIVE=true poetry run pytest -m 'llm and medium' --no-cov

# Run evals with a specific tag (e.g., tests involving logs)
RUN_LIVE=true poetry run pytest -m "llm and logs" --no-cov

# Run a specific eval by name
RUN_LIVE=true poetry run pytest tests/llm/test_ask_holmes.py -k "01_how_many_pods"
```

**Available markers:** See `pyproject.toml` for all available markers. Common ones include:
- `easy` - Regression tests that should always pass
- `medium` - More challenging scenarios
- `logs` - Tests involving log analysis
- `kubernetes` - Kubernetes-specific tests

### Testing Different Models

The `MODEL` environment variable is equivalent to the `--model` flag on the `holmes ask` CLI command. You can test HolmesGPT with different LLM providers:

```bash
# Test with GPT-4.1 (default)
RUN_LIVE=true MODEL=gpt-4.1 poetry run pytest -m 'llm and easy'

# Test with Claude
# Note: CLASSIFIER_MODEL must be set to OpenAI or Azure as Anthropic models are not currently supported for classification
RUN_LIVE=true MODEL=anthropic/claude-opus-4-1-20250805 CLASSIFIER_MODEL=gpt-4.1 poetry run pytest -m 'llm and easy'

# Test with Azure OpenAI
# Set required Azure environment variables for your deployment
export AZURE_API_KEY=your-azure-api-key
export AZURE_API_BASE=https://your-deployment.openai.azure.com/
export AZURE_API_VERSION=2024-02-15-preview
RUN_LIVE=true MODEL=azure/your-deployment-name CLASSIFIER_MODEL=azure/your-deployment-name poetry run pytest -m 'llm and easy'
```

**Important Notes:**

- When using Anthropic models, you must set `CLASSIFIER_MODEL` to an OpenAI or Azure model because the evaluation framework's classifier currently only supports these providers
- For any model provider, ensure you have the necessary API keys and environment variables set (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_API_KEY`)
- The model specified here is passed directly to LiteLLM, so any model supported by LiteLLM can be used

### Multi-Model Benchmarking

HolmesGPT supports running evaluations across multiple models simultaneously to compare their performance:

```bash
# Test multiple models in a single run
# Models are specified as comma-separated list
RUN_LIVE=true MODEL=gpt-4o,anthropic/claude-sonnet-4-20250514 \
  CLASSIFIER_MODEL=gpt-4o \
  poetry run pytest -m 'llm and easy' --no-cov
# Run with multiple iterations for statistically significant results
RUN_LIVE=true ITERATIONS=10 \
  MODEL=gpt-4o,anthropic/claude-sonnet-4-20250514 \
  CLASSIFIER_MODEL=gpt-4o \
  poetry run pytest -m 'llm and easy' -n 10

# Test specific scenario across models
RUN_LIVE=true MODEL=gpt-4o,gpt-4o-mini \
  poetry run pytest tests/llm/test_ask_holmes.py -k "01_how_many_pods"
```

When running multi-model benchmarks:
- Results will show a **Model Comparison Table** with side-by-side performance metrics
- Each model's pass rate, execution times, and P90 percentiles are displayed
- Tests are parameterized by model, so you'll see separate results for each model/test combination
- Use `CLASSIFIER_MODEL` to ensure consistent scoring across all models

### Running Evals with Multiple Iterations

LLMs are non-deterministic - they produce different outputs for the same input. **10 iterations is a good rule of thumb** for reliable results.

```bash
# Recommended: Run with multiple iterations
RUN_LIVE=true ITERATIONS=10 poetry run pytest -m 'llm and easy' --no-cov

# Quick check: Single run (less reliable)
RUN_LIVE=true poetry run pytest -m 'llm and easy' --no-cov
```

### Using RUN_LIVE=true vs Mock Data

Some evals support mock-data and don't need a live Kubernetes cluster to run. However, for the most accurate evaluation you should set `RUN_LIVE=true` which tests HolmesGPT with a live Kubernetes cluster not mock data.

This is important because LLMs can take multiple paths to reach conclusions, and mock data only captures one path.

### Parallel Execution

Speed up test runs with parallel workers:

```bash
# Run with 10 parallel workers
RUN_LIVE=true ITERATIONS=10 poetry run pytest tests/llm/ -n 10
```

### Debugging Failed Tests

When tests fail, use these techniques to investigate:

**CLI Flags for Debugging:**

- `--skip-setup`: Skip `before_test` commands (useful when resources already exist)
- `--skip-cleanup`: Skip `after_test` commands (useful for inspecting resources after test)
- `--only-setup`: Only run `before_test` commands, skip test execution
- `--only-cleanup`: Only run `after_test` commands, skip setup and test execution

```bash
# 1. Verbose output to see details
RUN_LIVE=true pytest -vv -s tests/llm/test_ask_holmes.py -k "failing_test"

# 2. Keep resources after test for inspection
RUN_LIVE=true pytest -k "test" --skip-cleanup

# 3. Iterate quickly without setup/cleanup
RUN_LIVE=true pytest -k "test" --skip-setup --skip-cleanup

# 4. Clean up when done debugging
RUN_LIVE=true pytest -k "test" --skip-setup

# 5. Or just run cleanup without the test
RUN_LIVE=true pytest -k "test" --only-cleanup

# 6. Test only setup commands without running the actual test
RUN_LIVE=true pytest -k "test" --only-setup
```

## Model Comparison Workflow

### Recommended: Multi-Model Testing (Single Run)

**Use the `MODEL` environment variable to test multiple models in a single run:**

```bash
# Compare multiple models simultaneously - RECOMMENDED approach
RUN_LIVE=true ITERATIONS=10 \
  MODEL=gpt-4.1,anthropic/claude-sonnet-4-20250514 \
  CLASSIFIER_MODEL=gpt-4.1 \
  poetry run pytest -m 'llm and easy' -n 10

# This will generate a comparison table showing:
# - Side-by-side pass rates for each model
# - Execution time comparisons
# - Cost comparisons
# - Best performing models summary
```

### Alternative: Single-Model Testing (Separate Runs)

For cases where you need separate experiments or different configurations per model:

```bash
# Run separate experiments for each model
# Useful when you need different settings or want to track experiments separately

# 1. Baseline with GPT-4
RUN_LIVE=true ITERATIONS=10 EXPERIMENT_ID=baseline_gpt4.1 MODEL=gpt-4.1 pytest -n 10 tests/llm/

# 2. Compare with Claude (using GPT-4 as classifier since Anthropic models can't classify)
RUN_LIVE=true ITERATIONS=10 EXPERIMENT_ID=claude4 MODEL=anthropic/claude-sonnet-4-20250514 CLASSIFIER_MODEL=gpt-4.1 pytest -n 10 tests/llm/
```

### Braintrust Integration

Results are automatically tracked if Braintrust is configured:

```bash
# Set these once in your environment
export BRAINTRUST_API_KEY=your-key
export BRAINTRUST_ORG=your-org

# Then run any evaluation command - results will be tracked automatically
RUN_LIVE=true MODEL=gpt-4o,anthropic/claude-sonnet-4-20250514 pytest -m 'llm and easy'
```

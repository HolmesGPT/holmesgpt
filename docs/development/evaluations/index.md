# HolmesGPT Evaluations

We use 150+ evaluations ('evals' for short) to benchmark HolmesGPT, map out areas for improvement, and compare performance across different models.

We also use the evals as regression tests on every commit.

**[View latest evaluation results →](./latest-results.md)**

## Test Categories

- **Regression tests (`easy`)**: Scenarios that must always pass
- **Advanced tests (`medium` and `hard`)**: More challenging scenarios
- **Specialized tests**: Focused on specific capabilities (logs, kubernetes, prometheus, etc.)

## Quick Start

### Running Evaluations

```bash
# Prerequisites
poetry install --with=dev

# Run regression tests (should always pass)
RUN_LIVE=true poetry run pytest -m 'llm and easy' --no-cov

# Run specific test
RUN_LIVE=true poetry run pytest tests/llm/test_ask_holmes.py -k "01_how_many_pods"

# Run with multiple iterations for reliable results
RUN_LIVE=true ITERATIONS=10 poetry run pytest -m 'llm and easy'
```

**[→ Complete guide to running evaluations](./running-evals.md)**

### Adding New Tests

Create test scenarios to improve coverage:

```yaml
# test_case.yaml
user_prompt: 'Is the nginx pod healthy?'
expected_output:
  - nginx pod is healthy
before_test: kubectl apply -f ./manifest.yaml
after_test: kubectl delete -f ./manifest.yaml
```

**[→ Guide to adding new evaluations](./adding-evals.md)**

### Analyzing Results

Track and debug evaluation results with Braintrust:

```bash
export BRAINTRUST_API_KEY=your-key
RUN_LIVE=true poetry run pytest -m 'llm and easy'
```

**[→ Reporting and analysis guide](./reporting.md)**

## Automated Benchmarking

Our CI/CD pipeline runs evaluations automatically:

- **Weekly** - Every Sunday at 2 AM UTC (comprehensive testing with 10 iterations)
- **Pull Requests** - When eval-related files are modified (quick validation)
- **On-demand** - Via GitHub Actions UI

Results are published here and archived in [history](./history/index.md).

## Model Comparison

Compare different LLMs to find the best for your use case:

```bash
# Test multiple models in one run
RUN_LIVE=true MODEL=gpt-4o,anthropic/claude-sonnet-4-20250514 \
  CLASSIFIER_MODEL=gpt-4o \
  poetry run pytest -m 'llm and easy'
```

See the [latest results](./latest-results.md) for current model performance comparisons.

### Benchmarking New Models

Want to test a new LLM model? Follow our step-by-step guide:

**[→ Guide to benchmarking new models](./benchmarking-new-models.md)**

## Resources

- **[Running Evaluations](./running-evals.md)** - Complete guide to running tests
- **[Adding New Evaluations](./adding-evals.md)** - Contribute test scenarios
- **[Benchmarking New Models](./benchmarking-new-models.md)** - Step-by-step guide for testing new LLM models
- **[Reporting with Braintrust](./reporting.md)** - Analyze results in detail
- **[Historical Results](./history/index.md)** - Past benchmark data

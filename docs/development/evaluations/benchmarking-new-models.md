# Benchmarking New Models

This guide walks you through the process of benchmarking a new LLM model in HolmesGPT's evaluation framework.

## Prerequisites

- At least 4 nodes
- Prometheus installed in cluster - (required by a few evals)

## Step 1: Create Model List File

Create a YAML file listing all models you want to benchmark. For provider-specific configuration options, see the [AI Providers documentation](../../ai-providers/index.md).

```yaml
# Example: model_list_eval.yaml
gpt-5.1:
  api_key: "API_KEY_HERE"
  model: azure/gpt-5.1
  api_base: https://your-resource.openai.azure.com/
  api_version: "2025-01-01-preview"

gpt-5:
  api_key: "API_KEY_HERE"
  model: azure/gpt-5
  api_base: https://your-resource.openai.azure.com/
  api_version: "2025-01-01-preview"

gpt-4.1:
  api_key: "API_KEY_HERE"
  model: openai/gpt-4.1

```

Set the environment variable:

```bash
export MODEL_LIST_FILE_LOCATION=/path/to/your/model_list_eval.yaml
```

## Step 2: Run Initial Test

Set required environment variables:

```bash
export MODEL="your-model-name"  # From your model list
export CLASSIFIER_MODEL=gpt-4.1  # Use gpt-4.1 for consistent evaluation
```

Run a quick test:

```bash
poetry run pytest --no-cov tests/llm/test_ask_holmes.py -s -m 'easy' -k '01_how_many_pods'
```

## Step 3: Known Issues and Troubleshooting

### Rate Limiting

When testing new models, you may encounter rate limiting from your provider:

- **Symptom**: You might see a `ThrottledError` or rate limit errors
- **Solution**: Contact your provider to raise the rate limit for your API key

### Mock Errors

If you see mock-related errors:

- Ensure `RUN_LIVE=true` is set
- Verify your Kubernetes cluster is accessible (if testing Kubernetes-related evals)
- Check that all required toolsets are properly configured

## Step 4: Run Benchmarks

Run the benchmark script with your new model (along with other models you have configured in your model list):

```bash
unset MODEL # to be safe
export CLASSIFIER_MODEL=gpt-4.1  # Use gpt-4.1 for consistent evaluation
# the default tests run are tags 'regression or benchmark'
./run_benchmarks_local.py --models your-new-model,gpt-4.1,gpt-4o
```

See `./run_benchmarks_local.py --help` for full usage details.

## Step 5: Review Results

After benchmarks complete, review the generated reports:

- **Latest results**: `docs/development/evaluations/latest-results.md`
- **Historical copy**: `docs/development/evaluations/history/results_YYYYMMDD_HHMMSS.md`
- **JSON results**: `eval_results.json`

The reports include:
- Pass rates for each model
- Execution time comparisons
- Cost comparisons (if available)
- Model comparison tables

## Best Practices

- Use `CLASSIFIER_MODEL=gpt-4.1` for consistent evaluation across all benchmarks
- Test incrementally: start with easy evals, then move to medium/hard
- Document model configuration, rate limits, and known issues

## Related Documentation

- [Running Evaluations](running-evals.md) - General guide to running evals
- [Adding New Evals](adding-evals.md) - How to create new evaluation tests
- [Reporting with Braintrust](reporting.md) - Analyzing evaluation results
- [AI Providers](../../ai-providers/index.md) - Provider-specific configuration

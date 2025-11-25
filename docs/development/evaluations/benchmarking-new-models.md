# Benchmarking New Models

This guide walks you through the process of benchmarking a new LLM model in HolmesGPT's evaluation framework.

## Step 1: Set Up Model Configuration

First, configure all necessary environment variables for your model provider.


### Required Environment Variables
Refer to the [AI Providers documentation](../../ai-providers/index.md) for provider-specific configuration.

### Classifier Model Setup

You will also need to set up the model that evaluates your benchmark. **Use `gpt-4.1` for consistency** across all benchmarks:

**For OpenAI classifier model:**
```bash
export OPENAI_API_KEY=your-api-key
export CLASSIFIER_MODEL=gpt-4.1
```

**For Azure OpenAI classifier model:**
```bash
export AZURE_API_KEY=your-azure-api-key
export AZURE_API_BASE=https://your-deployment.openai.azure.com/
export AZURE_API_VERSION=2024-02-15-preview
export CLASSIFIER_MODEL=azure/gpt-4.1
```

The classifier model is used to score and evaluate the benchmark results. Using a consistent classifier model ensures fair comparison across different models being benchmarked.

### Required Environment Variables for Testing

Set these variables before running tests:

```bash
# Set the new model you want to test
export MODEL="azure/gpt-5.1"  # Replace with your model

# Enable live testing to verify you don't get mock errors
export RUN_LIVE=true
```

**Important Notes:**
- `CLASSIFIER_MODEL` should always be set to `gpt-4.1` (or `azure/gpt-4.1` if using Azure) for consistency across all benchmarks
- `RUN_LIVE=true` ensures tests use real tools instead of mocks, providing more accurate evaluation results
- The model format follows LiteLLM conventions (e.g., `azure/gpt-5.1`, `anthropic/claude-sonnet-4-20250514`)

### Verify Cluster Requirements

Before running benchmarks, verify your Kubernetes cluster has at least 4 nodes available:

```bash
kubectl get nodes
```

Ensure you have at least 4 nodes in a `Ready` state. Some benchmark tests require multiple nodes to properly test distributed scenarios and resource allocation.

## Step 3: Known Issues and Troubleshooting

### Rate Limiting

When testing new models, you may encounter rate limiting from your provider:

- **Symptom**: You might see a `ThrottledError` or rate limit errors
- **Solution**: Contact your provider to raise the rate limit for your API key
- **Workaround**: Reduce parallelism (`-n` flag) or run tests sequentially

### Mock Errors

If you see mock-related errors:

- Ensure `RUN_LIVE=true` is set
- Verify your Kubernetes cluster is accessible (if testing Kubernetes-related evals)
- Check that all required toolsets are properly configured

## Step 4: Run Complete Benchmarks

Once your initial test passes, run the complete benchmark suite using the `run_benchmarks_local.sh` script.

### Create a Model List File

First, create a YAML file listing all models you want to benchmark. Each model should include its configuration details:

```yaml
# Example: model_list_eval.yaml
gpt-5.1:
  api_key: "{{ env.AZURE_API_KEY }}"  # Use environment variable, not hardcoded secrets
  model: azure/gpt-5.1
  model_type: azure
  args:
    api_base: https://your-resource.openai.azure.com/
    api_version: "2025-01-01-preview"
    temperature: 1

gpt-5:
  api_key: "{{ env.AZURE_API_KEY }}"
  model: azure/gpt-5
  model_type: azure
  args:
    api_base: https://your-resource.openai.azure.com/
    api_version: "2025-01-01-preview"
    temperature: 1

gpt-4.1:
  api_key: "{{ env.OPENAI_API_KEY }}"
  model: openai/gpt-4.1
  model_type: openai
  args:
    temperature: 0
```

!!! warning "Never commit secrets"
    Always use environment variables (e.g., `{{ env.API_KEY }}`) instead of hardcoding API keys in your model list file. See the [Using Multiple Providers](../../ai-providers/using-multiple-providers.md) documentation for complete configuration details.

Set the environment variable pointing to your model list:

```bash
export MODEL_LIST_FILE_LOCATION=/path/to/your/model_list_eval.yaml
```

For complete model configuration options and supported parameters, see the [Using Multiple Providers](../../ai-providers/using-multiple-providers.md) documentation.

### Run Benchmarks

Run the benchmark script with your model list:

```bash
./run_benchmarks_local.sh 'gpt-4o,eu.anthropic.claude-sonnet-4-20250514-v1:0,gpt-4.1,gpt-5,novita/deepseek/deepseek-v3.1-terminus,novita/qwen/qwen3-next-80b-a3b-instruct,gpt-5.1'
```

The script accepts comma-separated model names as the first argument. You can also customize:

```bash
# Run with specific test markers
./run_benchmarks_local.sh 'gpt-5.1' 'easy and kubernetes' 3

# Run with parallel workers for faster execution
./run_benchmarks_local.sh 'gpt-5.1' 'easy' 1 '' 6

# Run specific tests only
./run_benchmarks_local.sh 'gpt-5.1' 'easy' 1 '01_how_many_pods'
```

See `./run_benchmarks_local.sh --help` for full usage details.

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

## Complete Example Workflow

Here's a complete example for benchmarking a new Azure OpenAI model:

```bash
# 1. Set up Azure environment variables
export AZURE_API_KEY=your-key
export AZURE_API_BASE=https://your-deployment.openai.azure.com/
export AZURE_API_VERSION=2024-02-15-preview

# 2. Set test configuration
export CLASSIFIER_MODEL=azure/gpt-4.1
export MODEL="azure/gpt-5.1"
export RUN_LIVE=true

# 3. Quick test with easy evals
poetry run pytest --no-cov tests/llm/test_ask_holmes.py -s -m 'easy' -n10

# 4. Run full benchmarks
./run_benchmarks_local.sh 'azure/gpt-5.1'

# 5. Compare with other models
./run_benchmarks_local.sh 'gpt-4o,gpt-4.1,azure/gpt-5.1'
```

## Best Practices

1. **Always use consistent classifier model**: Use `gpt-4.1` (or `azure/gpt-4.1`) for `CLASSIFIER_MODEL` to ensure consistent scoring across all benchmarks

2. **Test incrementally**: Start with easy evals, then move to medium/hard evals once you've verified the model works

3. **Use multiple iterations**: LLMs are non-deterministic. Run with `ITERATIONS=10` for statistically significant results

4. **Compare with baseline**: Always compare new models against existing baselines (e.g., `gpt-4.1`, `gpt-4o`) to understand relative performance

5. **Document model details**: Note any special configuration, rate limits, or known issues for future reference

## Related Documentation

- [Running Evaluations](running-evals.md) - General guide to running evals
- [Adding New Evals](adding-evals.md) - How to create new evaluation tests
- [Reporting with Braintrust](reporting.md) - Analyzing evaluation results
- [AI Providers](../../ai-providers/index.md) - Provider-specific configuration

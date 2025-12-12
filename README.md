# KAITO Integration for HolmesGPT

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/11586/badge)](https://www.bestpractices.dev/projects/11586)

>🎉 **HolmesGPT is now a CNCF Sandbox Project!**  
HolmesGPT was originally created by [Robusta.Dev](https://home.robusta.dev/) and is a CNCF sandbox project.

Find more about HolmesGPT's maintainers and adopters [here](./ADOPTERS.md).

📚 **[Read the full documentation at holmesgpt.dev](https://holmesgpt.dev/)** for installation guides, tutorials, API reference, and more.

  <p align="center">
    <a href="#how-it-works"><strong>How it Works</strong></a> |
    <a href="#installation"><strong>Installation</strong></a> |
    <a href="#supported-llm-providers"><strong>LLM Providers</strong></a> |
    <a href="https://www.youtube.com/watch?v=TfQfx65LsDQ"><strong>YouTube Demo</strong></a> |
    <a href="https://deepwiki.com/HolmesGPT/holmesgpt"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki"></a>
  </p>
</div>

## What is KAITO

[KAITO (Kubernetes AI Toolkit Operator)](https://github.com/Azure/kaito) is a Kubernetes operator that automates the AI/ML inference model deployment on Azure Kubernetes Service (AKS). It manages GPU resources and provides optimized inference endpoints for large language models.

This integration enhances HolmesGPT to work seamlessly with KAITO-deployed models by:

- **Optimized Prompting**: Specialized prompt engineering for KAITO model characteristics
- **Model-Aware Behavior**: Automatic adaptation to different model capabilities and limitations
- **Clean Integration**: Consolidated KAITO-specific configurations that don't interfere with standard HolmesGPT usage

## Quick Start

### Prerequisites

- AKS cluster with KAITO operator installed
- KAITO model workspace deployed (see [supported models](#supported-models))
- HolmesGPT configured to use your KAITO model endpoint
- `KAITO_CONFIG_PATH` environment variable set (for testing/evaluation workflows)

### Installation

1. **Deploy a KAITO model workspace** (if not already done):

```yaml
# Example: llama-3.1-8b-workspace.yaml
apiVersion: kaito.sh/v1beta1
kind: Workspace
metadata:
  name: llama-3-1-8b-instruct
spec:
  resource:
    instanceType: "Standard_NC24ads_A100_v4"
    labelSelector:
      matchLabels:
        apps: llama-3-1-8b-instruct
  inference:
    preset:
      name: "llama-3.1-8b-instruct"
```

```bash
kubectl apply -f llama-3.1-8b-workspace.yaml
```

2. **Configure HolmesGPT** to use your KAITO endpoint:

```yaml
# ~/.holmes/config.yaml
model: "openai/llama-3.1-8b-instruct"
api_key: "not-needed"  # KAITO models typically don't require API keys
api_base: "http://llama-3-1-8b-instruct.default.svc.cluster.local/chat/completions"
```

3. **Run HolmesGPT** with KAITO optimizations:

```bash
holmes ask "what pods are failing in my cluster and why?"
```

The integration automatically detects KAITO usage and applies appropriate optimizations.

## Supported Models

The integration has been tested and optimized for the following KAITO model presets:

| Model | Status | Notes |
|-------|--------|-------|
| **Llama 3.1 8B Instruct** | ✅ | Optimized prompting for single-turn investigations |
| **Mistral 7B Instruct** | ✅ | Full parallel tool calling support |
| **Phi-4 14B Instruct** | 🟡 Beta | Limited testing, may require tuning |
| **Mixtral 8x7B Instruct** | 🟡 Beta | Resource-intensive, requires larger instances |

### Model-Specific Configurations

#### Llama 3.1 8B Instruct

```yaml
# Optimized for memory efficiency and focused investigations
model: "openai/llama-3.1-8b-instruct"
api_base: "http://llama-3-1-8b-instruct.default.svc.cluster.local/chat/completions"
```

#### Mistral 7B Instruct

```yaml
# Supports parallel tool calling for faster investigations
model: "openai/mistral-7b-instruct"
api_base: "http://mistral-7b-instruct.default.svc.cluster.local/chat/completions"
```

## Configuration

### Automatic KAITO Detection

The integration automatically detects KAITO usage and applies optimizations when:

- The `api_base` URL contains typical KAITO service patterns
- The model name matches known KAITO presets
- KAITO-specific environment variables are present

### Manual Configuration

For fine-tuned control, you can explicitly enable KAITO optimizations:

```yaml
# ~/.holmes/config.yaml
model: "openai/your-kaito-model"
api_base: "http://your-kaito-service.namespace.svc.cluster.local/chat/completions"

# Optional: Force KAITO optimizations
llm_config:
  kaito_mode: true
  max_parallel_tools: 1  # Override for models with limitations
```

### Advanced Settings

<details>
<summary>Custom Prompt Optimization</summary>

```yaml
# ~/.holmes/config.yaml
llm_config:
  # Disable KAITO optimizations if needed
  kaito_mode: false
  
  # Custom investigation settings
  investigation:
    max_depth: 3
    focus_mode: true  # Reduces verbosity for token-limited models
```
</details>

<details>
<summary>Environment Configuration</summary>

```bash
# Set KAITO config path for testing/evaluation workflows
export KAITO_CONFIG_PATH="/path/to/your/kaito-config.yaml"

# Alternative: Use standard Holmes configuration
# ~/.holmes/config.yaml will be used automatically
```
</details>

<details>
<summary>Resource Management</summary>

```yaml
# KAITO Workspace with resource optimization
apiVersion: kaito.sh/v1beta1
kind: Workspace
metadata:
  name: holmes-llm
spec:
  resource:
    instanceType: "Standard_NC24ads_A100_v4"
    count: 1
  inference:
    preset:
      name: "mistral-7b-instruct"
    # Optional: Custom resource limits
    resources:
      limits:
        nvidia.com/gpu: 1
      requests:
        nvidia.com/gpu: 1
```
</details>

## Usage Examples

### Basic Investigation

```bash
# Standard HolmesGPT usage - KAITO optimizations applied automatically
holmes ask "analyze the health of my ingress controllers"
```

### Interactive Mode

```bash
# Multi-turn investigation with KAITO optimization
holmes ask "what's wrong with my database connections?" --interactive
```

### Custom Investigation Scope

```bash
# Focused investigation for KAITO models
holmes ask "check only the failing pods in the production namespace" \
  --namespace production
```

### With Custom Context

```bash
# Provide logs as context
holmes ask "analyze this error pattern" \
  -f ./application-logs.txt
```

## Performance Considerations

### Model Selection Guidelines

You can view an example config file with all available settings [here](config.example.yaml).

### Tool Output Transformers

HolmesGPT supports **transformers** to process large tool outputs before sending them to your primary LLM. This feature helps manage context window limits while preserving essential information.

The most common transformer is `llm_summarize`, which uses a fast secondary model to summarize lengthy outputs from tools like `kubectl describe`, log queries, or metrics collection.

📖 **Learn more**: [Tool Output Transformers Documentation](docs/transformers.md)
</details>

### Resource Optimization

```yaml
# Recommended KAITO instance types by model
llama-3.1-8b-instruct:
  instanceType: "Standard_NC24ads_A100_v4"  # 24 vCPU, 1x A100
  
mistral-7b-instruct:
  instanceType: "Standard_NC24ads_A100_v4"  # 24 vCPU, 1x A100
  
mixtral-8x7b-instruct:
  instanceType: "Standard_NC48ads_A100_v4"  # 48 vCPU, 2x A100
```

### Investigation Efficiency

- KAITO optimizations reduce token usage by up to 30%
- Automatic prompt condensation for memory-limited models
- Intelligent tool selection based on model capabilities

## Troubleshooting

### Common Issues

<details>
<summary>KAITO model not responding</summary>

- [Introduction to HolmesGPT's evals](https://holmesgpt.dev/development/evaluations/).
- [Write your own evals](https://holmesgpt.dev/development/evaluations/adding-evals/).
- [Use Braintrust to view analyze results (optional)](https://holmesgpt.dev/development/evaluations/reporting/).

# Check model pod readiness
kubectl get pods -l app=your-model-name

## License
Distributed under the Apache 2.0 License. See [LICENSE](https://github.com/HolmesGPT/holmesgpt/blob/master/LICENSE) for more information.
<!-- Change License -->

<details>
<summary>Slow investigation performance</summary>

- Verify KAITO model is using GPU resources
- Check for resource contention on AKS nodes
- Consider using a larger instance type
- Enable `focus_mode` for token efficiency

```yaml
llm_config:
  investigation:
    focus_mode: true
    max_depth: 2  # Reduce investigation depth
```
</details>

Join our community to discuss the HolmesGPT roadmap and share feedback:

📹 **First Community Meetup Recording:** [Watch on YouTube](https://youtu.be/slQRc6nlFQU)
- **Topics:** Roadmap discussion, community feedback, and Q&A
- **Resources:** [📝 Meeting Notes](https://docs.google.com/document/d/1sIHCcTivyzrF5XNvos7ZT_UcxEOqgwfawsTbb9wMJe4/edit?tab=t.0) | [📋 Community Page](https://holmesgpt.dev/community/)

- **Consolidated KAITO Prompting**: Specialized templates optimized for KAITO model characteristics
- **Automatic Model Detection**: Smart detection of KAITO environments and model types
- **Performance Optimizations**: Reduced token usage and improved investigation efficiency
- **Clean Architecture**: KAITO-specific code separated from core HolmesGPT functionality

If you have any questions, feel free to message us on [HolmesGPT Slack Channel](https://cloud-native.slack.com/archives/C0A1SPQM5PZ)

Found an issue or want to add support for a new KAITO model?

1. Test the model with standard HolmesGPT workflows
2. Document any model-specific quirks or limitations
3. Submit a PR with optimizations if needed

For help, contact us on [Slack](https://cloud-native.slack.com/archives/C0A1SPQM5PZ) or ask [DeepWiki AI](https://deepwiki.com/HolmesGPT/holmesgpt) your questions.

Please make sure to follow the CNCF code of conduct - [details here](https://github.com/HolmesGPT/holmesgpt/blob/master/CODE_OF_CONDUCT.md).
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/HolmesGPT/holmesgpt)

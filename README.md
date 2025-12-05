# KAITO Integration for HolmesGPT

<div align="center">
  <h3>Enhanced HolmesGPT with KAITO Model Support</h3>
  
Enhanced version of HolmesGPT with optimized support for KAITO-deployed models on Azure Kubernetes Service (AKS). This integration provides specialized prompting and behavior optimizations for running HolmesGPT investigations with KAITO-managed LLMs.

  <p align="center">
    <a href="#what-is-kaito"><strong>What is KAITO</strong></a> |
    <a href="#quick-start"><strong>Quick Start</strong></a> |
    <a href="#supported-models"><strong>Supported Models</strong></a> |
    <a href="#configuration"><strong>Configuration</strong></a>
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

- **Llama 3.1 8B**: Best for focused, single-issue investigations
- **Mistral 7B**: Recommended for complex multi-system analysis
- **Larger Models**: Use for comprehensive cluster-wide investigations

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

```bash
# Check KAITO workspace status
kubectl get workspace

# Check model pod readiness
kubectl get pods -l app=your-model-name

# Test direct model access
curl -X POST http://your-model-service/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello"}], "max_tokens": 100}'
```
</details>

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

<details>
<summary>Memory or context limitations</summary>

```yaml
llm_config:
  kaito_mode: true
  investigation:
    compact_output: true  # Reduces verbose tool outputs
    max_tools_per_turn: 3  # Limits parallel tool execution
```
</details>

## Integration Details

This enhanced version includes:

- **Consolidated KAITO Prompting**: Specialized templates optimized for KAITO model characteristics
- **Automatic Model Detection**: Smart detection of KAITO environments and model types
- **Performance Optimizations**: Reduced token usage and improved investigation efficiency
- **Clean Architecture**: KAITO-specific code separated from core HolmesGPT functionality

## Contributing

Found an issue or want to add support for a new KAITO model?

1. Test the model with standard HolmesGPT workflows
2. Document any model-specific quirks or limitations
3. Submit a PR with optimizations if needed

## Related Projects

- [KAITO](https://github.com/Azure/kaito) - Kubernetes AI Toolkit Operator
- [HolmesGPT](https://github.com/HolmesGPT/holmesgpt) - AI Agent for Cloud Troubleshooting  
- [AKS](https://azure.microsoft.com/en-us/services/kubernetes-service/) - Azure Kubernetes Service
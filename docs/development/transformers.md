# Tool Output Transformers

HolmesGPT supports **transformers** that can process tool outputs before they're sent to the primary LLM. This enables automatic summarization of large outputs, reducing context window usage while preserving essential information.

## Overview

Transformers are functions that take a tool's raw output and transform it before returning to the LLM. The primary use case is the `llm_summarize` transformer, which uses a fast secondary model to summarize lengthy outputs from tools like `kubectl describe`, log queries, or metrics collection.

## Configuration

### Global Configuration

Configure transformer behavior globally in your HolmesGPT configuration:

```bash
# CLI flags
holmes ask "what pods are unhealthy?" --fast-model gpt-4o-mini

# Environment variables
export FAST_MODEL="gpt-4o-mini"

# Or via config file
# ~/.holmes/config.yaml:
# fast_model: gpt-4o-mini
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `--fast-model` | Fast model for summarization tasks | `None` (disabled) |

### Tool-Level Configuration

Tools can declare transformers in their definitions:

#### YAML Tools

```yaml
# Basic summarization with defaults
- name: "kubectl_get_by_kind_in_namespace"
  description: "Get all resources of a type in a namespace"
  command: "kubectl get --show-labels -o wide {{ kind }} -n {{ namespace }}"
  transformers:
    - name: llm_summarize
      config: {}

# Custom threshold and prompt
- name: "kubectl_describe"
  description: "Describe a Kubernetes resource"
  command: "kubectl describe {{ kind }} {{ name }}{% if namespace %} -n {{ namespace }}{% endif %}"
  transformers:
    - name: llm_summarize
      config:
        input_threshold: 1000
        prompt: |
          Summarize this kubectl describe output focusing on:
          - What needs attention or immediate action
          - Resource status and health indicators
          - Any errors, warnings, or non-standard states
          - Key configuration details that could affect functionality
          - When possible, mention exact field names so the user can grep for specific details
```

#### Python Toolsets

```python
from holmes.core.transformers import Transformer

class PrometheusToolset(Toolset):
    def __init__(self):
        super().__init__(
            name="prometheus/metrics",
            tools=[
                ListPrometheusRules(
                    toolset=self,
                    transformers=[
                        Transformer(name="llm_summarize", config={})  # use default config
                    ]
                ),
                ListAvailableMetrics(
                    toolset=self,
                    transformers=[
                        Transformer(
                            name="llm_summarize",
                            config={
                                "input_threshold": 800,
                                "prompt": "Summarize the available Prometheus metrics, grouping similar metrics and highlighting any unusual patterns."
                            }
                        )
                    ]
                ),
            ]
        )
```

#### MCP Tools

To be implemented in future phases, allowing MCP tools to leverage transformers similarly to YAML and Python tools.

## LLM Summarize Transformer

The `llm_summarize` transformer is the primary transformer available in HolmesGPT.

### Behavior

1. **Threshold Check**: Only processes outputs longer than `input_threshold` characters
2. **Fast Model Required**: Skips summarization if no `--fast-model` is configured
3. **Context Preservation**: Maintains essential debugging information while reducing size
4. **Error Handling**: Falls back to original output if summarization fails
5. **Non-Expanding Fallback**: If the generated summary is not smaller than the original output, preserves the original output to prevent expansion

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `input_threshold` | Minimum characters to trigger summarization | `1000` characters (default) |
| `prompt` | Custom summarization instructions | Default diagnostic prompt |

### Default Prompt

The default summarization prompt is optimized for operational diagnostics:

```text
Summarize this operational data focusing on:
- What needs attention or immediate action
- Group similar entries into a single line and description
- Make sure to mention outliers, errors, and non-standard patterns
- List normal/healthy patterns as aggregate descriptions
- When listing problematic entries, also try to use aggregate descriptions when possible
- When possible, mention exact keywords, IDs, or patterns so the user can filter/search the original data and drill down on the parts they care about
```

## When to Use Transformers

### ✅ Good Candidates for Transformers

- **Large kubectl outputs** (`kubectl get -A`, `kubectl describe`)
- **Log aggregation results** with many similar entries
- **Metrics queries** returning extensive time series data
- **Database query results** with repetitive rows
- **API responses** with verbose metadata

### ❌ Poor Candidates for Transformers

- **Small, structured outputs** (single resource descriptions)
- **Error messages** that need exact preservation
- **Configuration files** where details matter
- **Already concise outputs** under the threshold

## Examples

### Kubernetes Resource Listing

**Without Transformer:**
```text
NAME                                READY   STATUS      RESTARTS   AGE     IP           NODE
pod-1                              1/1     Running     0          5d      10.1.1.1     node-1
pod-2                              1/1     Running     0          5d      10.1.1.2     node-1
pod-3                              1/1     Running     0          5d      10.1.1.3     node-2
pod-4                              0/1     CrashLoopBackOff  15    1h      10.1.1.4     node-2
[... 100 more similar pods ...]
```

**With Transformer:**
```text
Found 104 pods across 2 nodes:
- 103 pods are healthy and running (age: 5d, on node-1 and node-2)
- 1 pod in CrashLoopBackOff state: pod-4 (15 restarts, 1h old, IP 10.1.1.4, node-2)
- Search with "grep pod-4" or "grep CrashLoopBackOff" to drill down on the problematic pod
```

### Log Analysis

**Without Transformer:**
```text
2024-01-15T10:30:01Z INFO Starting application...
2024-01-15T10:30:02Z INFO Database connection established
2024-01-15T10:30:03Z INFO Loading configuration...
[... 1000 similar INFO logs ...]
2024-01-15T10:35:15Z ERROR Failed to connect to Redis: connection timeout
2024-01-15T10:35:16Z WARN Retrying Redis connection (attempt 1/3)
```

**With Transformer:**
```text
Log analysis (2024-01-15 10:30-10:35):
- 1000+ INFO messages showing normal application startup and operations
- 1 ERROR: Redis connection timeout at 10:35:15Z
- 1 WARN: Redis retry attempt at 10:35:16Z
- Search with "grep ERROR" or "grep Redis" to investigate the connection issue
```

## Best Practices

### Prompt Design

1. **Focus on actionable information** - what needs attention
2. **Group similar items** - avoid repetitive listings
3. **Preserve searchable keywords** - help users drill down
4. **Highlight anomalies** - errors, warnings, outliers
5. **Use aggregate descriptions** - "5 pods in pending state" vs listing each

### Configuration

1. **Set appropriate thresholds** - avoid summarizing small outputs
2. **Use fast models** - gpt-4o-mini, claude-haiku for cost/speed
3. **Customize prompts** - tailor to specific tool output types
4. **Test thoroughly** - ensure key information isn't lost

### Tool Integration

1. **High-value targets first** - kubectl, logs, metrics
2. **Preserve debug capability** - users should be able to get raw output
3. **Graceful degradation** - work without transformers configured
4. **Monitor effectiveness** - track context window reduction

## Troubleshooting

### Transformer Not Running

- **Check fast model configuration**: Ensure `--fast-model` is set
- **Verify threshold**: Output must exceed `input_threshold` characters
- **Review tool configuration**: Confirm `transformers` is properly defined

### Poor Summarization Quality

- **Customize the prompt**: Add domain-specific instructions
- **Adjust threshold**: Lower threshold for smaller outputs
- **Try different fast models**: Some models excel at different tasks
- **Check original output**: Ensure raw data contains the expected information

### Performance Issues

- **Monitor token usage**: Fast models should be cost-effective
- **Set reasonable thresholds**: Avoid processing small outputs
- **Consider rate limits**: Fast model APIs have usage constraints
- **Cache when possible**: Some tools may benefit from result caching

## Migration Guide

### Existing YAML Tools

Add transformer configuration to tools that generate large outputs:

```yaml
# Before
- name: "my_large_output_tool"
  command: "some command that produces lots of output"

# After
- name: "my_large_output_tool"
  command: "some command that produces lots of output"
  transformers:
    - name: llm_summarize
      config:
        input_threshold: 1000
        prompt: "Custom prompt for this tool's output type"
```

### Python Toolsets

Update tool constructors to accept transformer configurations:

```python
from holmes.core.transformers import Transformer

# Before
MyTool(toolset=self)

# After
MyTool(
    toolset=self,
    transformers=[
        Transformer(name="llm_summarize", config={"input_threshold": 800})
    ]
)
```

For more information, see the [HolmesGPT documentation](https://holmesgpt.dev/data-sources/custom-toolsets/).

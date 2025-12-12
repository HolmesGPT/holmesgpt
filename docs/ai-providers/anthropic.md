# Anthropic

Configure HolmesGPT to use Anthropic's Claude models.

## Setup

Get an [Anthropic API key](https://support.anthropic.com/en/articles/8114521-how-can-i-access-the-anthropic-api){:target="_blank"}.

## Configuration

=== "Holmes CLI"

    ```bash
    export ANTHROPIC_API_KEY="your-anthropic-api-key"
    holmes ask "what pods are failing?" --model="anthropic/<your-claude-model>"
    ```

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=anthropic-api-key="sk-ant-..." \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: ANTHROPIC_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: anthropic-api-key

    # Configure at least one model using modelList
    modelList:
      claude-sonnet-4:
        api_key: "{{ env.ANTHROPIC_API_KEY }}"
        model: claude-sonnet-4-20250514
        temperature: 1
        thinking:
          budget_tokens: 10000
          type: enabled

      claude-opus-4:
        api_key: "{{ env.ANTHROPIC_API_KEY }}"
        model: anthropic/claude-opus-4-1-20250805
        temperature: 1

    # Optional: Set default model (use modelList key name)
    config:
      model: "claude-sonnet-4"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=anthropic-api-key="sk-ant-..." \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: ANTHROPIC_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: anthropic-api-key

      # Configure at least one model using modelList
      modelList:
        claude-sonnet-4:
          api_key: "{{ env.ANTHROPIC_API_KEY }}"
          model: claude-sonnet-4-20250514
          temperature: 1
          thinking:
            budget_tokens: 10000
            type: enabled

        claude-opus-4:
          api_key: "{{ env.ANTHROPIC_API_KEY }}"
          model: anthropic/claude-opus-4-1-20250805
          temperature: 1

      # Optional: Set default model (use modelList key name)
      config:
        model: "claude-sonnet-4"  # This refers to the key name in modelList above
    ```

## Using CLI Parameters

You can also pass the API key directly as a command-line parameter:

```bash
holmes ask "what pods are failing?" --model="anthropic/<your-claude-model>" --api-key="your-api-key"
```

## Prompt Caching

HolmesGPT adds Anthropic's prompt caching feature, which can significantly reduce costs and latency for repeated API calls with similar prompts.

HolmesGPT automatically adds cache control to the last message in each API call. This caches everything from the beginning of the conversation up to that point, making subsequent calls with the same prefix much faster and cheaper.

### How It Works

- Anthropic uses prefix-based caching - it caches the exact sequence of messages up to the cache control point
- The cache has a 5-minute lifetime by default
- Cached content must be at least 1024 tokens to be effective
- You're charged for cache writes on the first call, but subsequent cache hits are much cheaper

### Benefits in HolmesGPT

Prompt caching is particularly effective for HolmesGPT because:

- System prompts with tool definitions are large and static - perfect for caching
- Tool investigation loops reuse the same context multiple times
- Multi-step investigations benefit from cached conversation history

## Additional Resources

HolmesGPT uses the LiteLLM API to support Anthropic provider. Refer to [LiteLLM Anthropic docs](https://litellm.vercel.app/docs/providers/anthropic){:target="_blank"} for more details.

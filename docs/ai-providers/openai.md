# OpenAI

Configure HolmesGPT to use OpenAI's GPT models.

## Setup

Get a paid [OpenAI API key](https://help.openai.com/en/articles/4936850-where-do-i-find-my-openai-api-key){:target="_blank"}.

!!! note
    Requires a paid OpenAI API key, not a ChatGPT Plus subscription.

## Configuration

=== "Holmes CLI"

    **Using Environment Variables:**
    ```bash
    export OPENAI_API_KEY="your-openai-api-key"
    holmes ask "what pods are failing?"
    ```

    **Using Command Line Parameters:**

    You can also pass the API key directly as a command-line parameter:
    ```bash
    holmes ask "what pods are failing?" --api-key="your-api-key"
    ```

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=openai-api-key="sk-..." \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: OPENAI_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: openai-api-key

    # Configure at least one model using modelList
    modelList:
      gpt-4.1:
        api_key: "{{ env.OPENAI_API_KEY }}"
        model: openai/gpt-4.1
        temperature: 0

      gpt-5:
        api_key: "{{ env.OPENAI_API_KEY }}"
        model: openai/gpt-5
        temperature: 1
        reasoning_effort: medium

    # Optional: Set default model (use modelList key name)
    config:
      model: "gpt-4.1"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**

    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=openai-api-key="sk-..." \
      -n <namespace>
    ```

    **Configure Helm Values:**

    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: openai-api-key

      # Configure at least one model using modelList
      modelList:
        gpt-4.1:
          api_key: "{{ env.OPENAI_API_KEY }}"
          model: openai/gpt-4.1
          temperature: 0

        gpt-5:
          api_key: "{{ env.OPENAI_API_KEY }}"
          model: openai/gpt-5
          temperature: 1
          reasoning_effort: medium

      # Optional: Set default model (use modelList key name)
      config:
        model: "gpt-4.1"  # This refers to the key name in modelList above
    ```

## Available Models

Most OpenAI models are supported. For example:

```bash
# GPT-4.1 (default) - fast and decent responses
holmes ask "what pods are failing?"

# GPT-5 (more accurate but much slower)
holmes ask "what pods are failing?" --model="gpt-5"
```

!!! tip "Best Results"
    For more accurate results, consider using Anthropic's Claude models.

    See [benchmark results](../development/evaluations/latest-results.md) for a comparison.

## GPT-5 Reasoning Effort

When using GPT-5 models, you can control the reasoning effort level. This allows you to balance between response quality and processing time/cost.

=== "Holmes CLI"

    **Using Environment Variables:**
    ```bash
    # Use minimal reasoning effort for faster responses
    export REASONING_EFFORT="minimal"
    holmes ask "what pods are failing?" --model="gpt-5"

    # Use default reasoning effort
    export REASONING_EFFORT="medium"
    holmes ask "what pods are failing?" --model="gpt-5"

    # Use high reasoning effort for complex investigations
    export REASONING_EFFORT="high"
    holmes ask "what pods are failing?" --model="gpt-5"
    ```

=== "Holmes Helm Chart"

    **Configure in modelList:**
    ```yaml
    # values.yaml
    modelList:
      gpt-5-minimal:
        api_key: "{{ env.OPENAI_API_KEY }}"
        model: openai/gpt-5
        temperature: 1
        reasoning_effort: minimal  # Fast responses

      gpt-5-medium:
        api_key: "{{ env.OPENAI_API_KEY }}"
        model: openai/gpt-5
        temperature: 1
        reasoning_effort: medium  # Balanced (default)

      gpt-5-high:
        api_key: "{{ env.OPENAI_API_KEY }}"
        model: openai/gpt-5
        temperature: 1
        reasoning_effort: high  # Complex investigations

    # Use the appropriate model based on your needs
    config:
      model: "gpt-5-medium"
    ```

=== "Robusta Helm Chart"

    **Configure in modelList:**
    ```yaml
    # values.yaml
    holmes:
      modelList:
        gpt-5-minimal:
          api_key: "{{ env.OPENAI_API_KEY }}"
          model: openai/gpt-5
          temperature: 1
          reasoning_effort: minimal  # Fast responses

        gpt-5-medium:
          api_key: "{{ env.OPENAI_API_KEY }}"
          model: openai/gpt-5
          temperature: 1
          reasoning_effort: medium  # Balanced (default)

        gpt-5-high:
          api_key: "{{ env.OPENAI_API_KEY }}"
          model: openai/gpt-5
          temperature: 1
          reasoning_effort: high  # Complex investigations

      # Use the appropriate model based on your needs
      config:
        model: "gpt-5-medium"
    ```

**Available reasoning effort levels:**

- `minimal` - Fastest responses, suitable for simple queries
- `low` - Balance between speed and quality
- `medium` - Standard reasoning depth (default)
- `high` - Deeper reasoning for complex problems

For more details on reasoning effort levels, refer to the [OpenAI documentation](https://platform.openai.com/docs/).

## Additional Resources

HolmesGPT uses the LiteLLM API to support OpenAI provider. Refer to [LiteLLM OpenAI docs](https://litellm.vercel.app/docs/providers/openai){:target="_blank"} for more details.

# OpenRouter

Configure HolmesGPT to use [OpenRouter](https://openrouter.ai/) for access to multiple AI models through a single API.

## Methods

### Method 1: Native LiteLLM OpenRouter Provider (Recommended)

The simplest approach uses LiteLLM's native OpenRouter support. Only `OPENROUTER_API_KEY` is required. This method is preferred because HolmesGPT can automatically determine token limits and context window sizes for each model.

=== "Holmes CLI"

    ```bash
    export OPENROUTER_API_KEY="sk-or-..."  # your OpenRouter key
    holmes ask "hello" --model="openrouter/anthropic/claude-sonnet-4-5" --no-interactive
    ```

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=openrouter-api-key="sk-or-..." \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: OPENROUTER_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: openrouter-api-key

    # Configure at least one model using modelList
    modelList:
      claude-sonnet-4:
        api_key: "{{ env.OPENROUTER_API_KEY }}"
        model: openrouter/anthropic/claude-sonnet-4-5-20250929
        temperature: 1
        thinking:
          budget_tokens: 10000
          type: enabled

      claude-opus-4:
        api_key: "{{ env.OPENROUTER_API_KEY }}"
        model: openrouter/anthropic/claude-opus-4-5-20251101
        temperature: 1

    # Optional: Set default model (use modelList key name)
    config:
      model: "claude-sonnet-4"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=openrouter-api-key="sk-or-..." \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: OPENROUTER_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: openrouter-api-key

      # Configure at least one model using modelList
      modelList:
        claude-sonnet-4:
          api_key: "{{ env.OPENROUTER_API_KEY }}"
          model: openrouter/anthropic/claude-sonnet-4-5-20250929
          temperature: 1
          thinking:
            budget_tokens: 10000
            type: enabled

        claude-opus-4:
          api_key: "{{ env.OPENROUTER_API_KEY }}"
          model: openrouter/anthropic/claude-opus-4-5-20251101
          temperature: 1

      # Optional: Set default model (use modelList key name)
      config:
        model: "claude-sonnet-4"  # This refers to the key name in modelList above
    ```

**Optional environment variables:**

- `OPENROUTER_API_BASE` - Custom API base URL (defaults to `https://openrouter.ai/api/v1`)
- `OR_SITE_URL` - Your site URL for OpenRouter rankings
- `OR_APP_NAME` - Your app name for OpenRouter rankings

### Method 2: OpenAI-Compatible Endpoint

Alternatively, you can use OpenRouter's OpenAI-compatible endpoint by setting the base URL and using `OPENAI_API_KEY`. Note the `openai/` prefix instead of `openrouter/`.

!!! warning "Token Limits"
    With this method, HolmesGPT cannot automatically determine token limits for the model. You may need to set `OVERRIDE_MAX_CONTENT_SIZE` and `OVERRIDE_MAX_OUTPUT_TOKEN` environment variables manually.

=== "Holmes CLI"

    ```bash
    export OPENAI_API_BASE="https://openrouter.ai/api/v1"
    export OPENAI_API_KEY="sk-or-..."  # your OpenRouter key
    holmes ask "hello" --model="openai/anthropic/claude-sonnet-4-5" --no-interactive
    ```

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=openai-api-key="sk-or-..." \
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
      - name: OPENAI_API_BASE
        value: "https://openrouter.ai/api/v1"

    # Configure at least one model using modelList
    modelList:
      claude-sonnet-4:
        api_key: "{{ env.OPENAI_API_KEY }}"
        api_base: "https://openrouter.ai/api/v1"
        model: openai/anthropic/claude-sonnet-4-5-20250929
        temperature: 1
        thinking:
          budget_tokens: 10000
          type: enabled

      claude-opus-4:
        api_key: "{{ env.OPENAI_API_KEY }}"
        api_base: "https://openrouter.ai/api/v1"
        model: openai/anthropic/claude-opus-4-5-20251101
        temperature: 1

    # Optional: Set default model (use modelList key name)
    config:
      model: "claude-sonnet-4"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=openai-api-key="sk-or-..." \
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
        - name: OPENAI_API_BASE
          value: "https://openrouter.ai/api/v1"

      # Configure at least one model using modelList
      modelList:
        claude-sonnet-4:
          api_key: "{{ env.OPENAI_API_KEY }}"
          api_base: "https://openrouter.ai/api/v1"
          model: openai/anthropic/claude-sonnet-4-5-20250929
          temperature: 1
          thinking:
            budget_tokens: 10000
            type: enabled

        claude-opus-4:
          api_key: "{{ env.OPENAI_API_KEY }}"
          api_base: "https://openrouter.ai/api/v1"
          model: openai/anthropic/claude-opus-4-5-20251101
          temperature: 1

      # Optional: Set default model (use modelList key name)
      config:
        model: "claude-sonnet-4"  # This refers to the key name in modelList above
    ```

## Available Models

You can use any model available on OpenRouter. The model prefix depends on which method you use:

**Method 1 (Native):** Use `openrouter/` prefix

- `openrouter/anthropic/claude-sonnet-4-5`
- `openrouter/anthropic/claude-opus-4-5`
- `openrouter/openai/gpt-4o`
- `openrouter/google/gemini-pro`

**Method 2 (OpenAI-Compatible):** Use `openai/` prefix

- `openai/anthropic/claude-sonnet-4-5`
- `openai/anthropic/claude-opus-4-5`
- `openai/openai/gpt-4o`
- `openai/google/gemini-pro`

See the [OpenRouter models page](https://openrouter.ai/models) for a complete list of available models.

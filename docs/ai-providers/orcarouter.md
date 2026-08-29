# OrcaRouter

Configure HolmesGPT to use [OrcaRouter](https://www.orcarouter.ai) for access to multiple AI models through a single OpenAI-compatible endpoint.

OrcaRouter is an OpenAI-compatible AI gateway for models and agents. Like OpenRouter, it exposes a provider/model namespace across many models — but it also combines adaptive routing, automatic failover, zero-markup inference, observability, guardrails, and agent-tool governance behind the same endpoint. It also runs gateway-level, zero-trust security for AI agents on the same endpoint — screening every prompt/response and governing every tool call on a default-deny basis, with no application code changes.

## Methods

### Method 1: Native `orcarouter/` Model Prefix (Recommended)

HolmesGPT has built-in support for OrcaRouter. Only `ORCAROUTER_API_KEY` is required. Models use the `orcarouter/` prefix and are automatically routed to `https://api.orcarouter.ai/v1` through LiteLLM's OpenAI-compatible path.

=== "Holmes CLI"

    ```bash
    export ORCAROUTER_API_KEY="sk-orca-..."  # your OrcaRouter key
    holmes ask "hello" --model="orcarouter/anthropic/claude-sonnet-4.5" --no-interactive
    ```

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=orcarouter-api-key="sk-orca-..." \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: ORCAROUTER_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: orcarouter-api-key

    # Configure at least one model using modelList
    modelList:
      claude-sonnet-4:
        api_key: "{{ env.ORCAROUTER_API_KEY }}"
        model: orcarouter/anthropic/claude-sonnet-4.5-20250929
        temperature: 1
        thinking:
          budget_tokens: 10000
          type: enabled

      claude-opus-4:
        api_key: "{{ env.ORCAROUTER_API_KEY }}"
        model: orcarouter/anthropic/claude-opus-4.5-20251101
        temperature: 1

    # Optional: Set default model (use modelList key name)
    config:
      model: "claude-sonnet-4"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=orcarouter-api-key="sk-orca-..." \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: ORCAROUTER_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: orcarouter-api-key

      # Configure at least one model using modelList
      modelList:
        claude-sonnet-4:
          api_key: "{{ env.ORCAROUTER_API_KEY }}"
          model: orcarouter/anthropic/claude-sonnet-4.5-20250929
          temperature: 1
          thinking:
            budget_tokens: 10000
            type: enabled

        claude-opus-4:
          api_key: "{{ env.ORCAROUTER_API_KEY }}"
          model: orcarouter/anthropic/claude-opus-4.5-20251101
          temperature: 1

      # Optional: Set default model (use modelList key name)
      config:
        model: "claude-sonnet-4"  # This refers to the key name in modelList above
    ```

**Optional environment variables:**

- `ORCAROUTER_API_BASE` - Custom API base URL (defaults to `https://api.orcarouter.ai/v1`)

### Method 2: OpenAI-Compatible Endpoint

Alternatively, you can use OrcaRouter's OpenAI-compatible endpoint by setting the base URL and using `OPENAI_API_KEY`. Note the `openai/` prefix instead of `orcarouter/`.

!!! warning "Token Limits"
    With this method, HolmesGPT cannot automatically determine token limits for the model. You may need to set `OVERRIDE_MAX_CONTENT_SIZE` and `OVERRIDE_MAX_OUTPUT_TOKEN` environment variables manually.

=== "Holmes CLI"

    ```bash
    export OPENAI_API_BASE="https://api.orcarouter.ai/v1"
    export OPENAI_API_KEY="sk-orca-..."  # your OrcaRouter key
    holmes ask "hello" --model="openai/anthropic/claude-sonnet-4.5" --no-interactive
    ```

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=openai-api-key="sk-orca-..." \
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
        value: "https://api.orcarouter.ai/v1"

    # Configure at least one model using modelList
    modelList:
      claude-sonnet-4:
        api_key: "{{ env.OPENAI_API_KEY }}"
        api_base: "https://api.orcarouter.ai/v1"
        model: openai/anthropic/claude-sonnet-4.5-20250929
        temperature: 1
        thinking:
          budget_tokens: 10000
          type: enabled

      claude-opus-4:
        api_key: "{{ env.OPENAI_API_KEY }}"
        api_base: "https://api.orcarouter.ai/v1"
        model: openai/anthropic/claude-opus-4.5-20251101
        temperature: 1

    # Optional: Set default model (use modelList key name)
    config:
      model: "claude-sonnet-4"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=openai-api-key="sk-orca-..." \
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
          value: "https://api.orcarouter.ai/v1"

      # Configure at least one model using modelList
      modelList:
        claude-sonnet-4:
          api_key: "{{ env.OPENAI_API_KEY }}"
          api_base: "https://api.orcarouter.ai/v1"
          model: openai/anthropic/claude-sonnet-4.5-20250929
          temperature: 1
          thinking:
            budget_tokens: 10000
            type: enabled

        claude-opus-4:
          api_key: "{{ env.OPENAI_API_KEY }}"
          api_base: "https://api.orcarouter.ai/v1"
          model: openai/anthropic/claude-opus-4.5-20251101
          temperature: 1

      # Optional: Set default model (use modelList key name)
      config:
        model: "claude-sonnet-4"  # This refers to the key name in modelList above
    ```

## Available Models

You can use any model available on OrcaRouter. The model prefix depends on which method you use:

**Method 1 (Native):** Use `orcarouter/` prefix

- `orcarouter/anthropic/claude-sonnet-4.5`
- `orcarouter/anthropic/claude-opus-4.5`
- `orcarouter/openai/gpt-4o`
- `orcarouter/google/gemini-2.5-pro`

**Method 2 (OpenAI-Compatible):** Use `openai/` prefix

- `openai/anthropic/claude-sonnet-4.5`
- `openai/anthropic/claude-opus-4.5`
- `openai/openai/gpt-4o`
- `openai/google/gemini-2.5-pro`

See the [OrcaRouter models page](https://www.orcarouter.ai) for a complete list of available models.

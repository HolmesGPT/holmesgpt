# Ollama

Configure HolmesGPT to use local models with Ollama.

!!! warning
    Ollama support is experimental and can be tricky to configure correctly. We recommend trying HolmesGPT with a hosted model first (like Claude or OpenAI) to ensure everything works before switching to Ollama. Tool-calling capabilities are limited and may produce inconsistent results. Only [LiteLLM supported Ollama models](https://docs.litellm.ai/docs/providers/ollama#ollama-models){:target="_blank"} work with HolmesGPT.

## Setup

1. Download Ollama from [ollama.com](https://ollama.com/){:target="_blank"}
2. Start Ollama: `ollama serve`
3. Download models: `ollama pull <model-name>`

## Configuration

=== "Holmes CLI"

    ```bash
    export OLLAMA_API_BASE="http://localhost:11434"
    holmes ask "what pods are failing?" --model="ollama_chat/<your-ollama-model>"

    # Or use MODEL environment variable instead of --model flag
    export MODEL="ollama_chat/<your-ollama-model>"
    holmes ask "what pods are failing?"
    ```

    **Alternative (OpenAI-compatible gateway)**

    If you hit compatibility issues with certain Ollama models via LiteLLM, you can use Ollama's OpenAI-compatible API endpoint:

    ```bash
    export OPENAI_API_BASE="http://localhost:11434/v1"
    export OPENAI_API_KEY="dummy-key"  # Required but can be any value
    holmes ask "what pods are failing?" --model="openai/<your-ollama-model>"

    # Or use MODEL environment variable instead of --model flag
    export MODEL="openai/<your-ollama-model>"
    holmes ask "what pods are failing?"
    ```

=== "Holmes Helm Chart"

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: OLLAMA_API_BASE
        value: "http://ollama-service:11434"

    # Configure at least one model using modelList
    modelList:
      ollama-llama3:
        api_base: "{{ env.OLLAMA_API_BASE }}"
        model: ollama_chat/llama3
        temperature: 1

      ollama-codellama:
        api_base: "{{ env.OLLAMA_API_BASE }}"
        model: ollama_chat/codellama
        temperature: 1

    # Optional: Set default model (use modelList key name)
    config:
      model: "ollama-llama3"  # This refers to the key name in modelList above
    ```

    !!! note "Ollama Service"
        You'll need to deploy Ollama as a service in your cluster. The `OLLAMA_API_BASE` should point to your Ollama service endpoint.

    **Alternative (OpenAI-compatible gateway)**

    If you hit compatibility issues with certain Ollama models via LiteLLM, you can configure an OpenAI-compatible gateway in your Helm values:

    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: OPENAI_API_BASE
        value: "http://ollama-service:11434/v1"
      - name: OPENAI_API_KEY
        value: "YOUR_BEARER_TOKEN_HERE"

    modelList:
      ollama-alt:
        api_base: "{{ env.OPENAI_API_BASE }}"
        api_key: "{{ env.OPENAI_API_KEY }}"
        model: openai/OLLAMA_MODEL_NAME

    # Optional
    config:
      model: "ollama-alt"
    ```

=== "Robusta Helm Chart"

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: OLLAMA_API_BASE
          value: "http://ollama-service:11434"

      # Configure at least one model using modelList
      modelList:
        ollama-llama3:
          api_base: "{{ env.OLLAMA_API_BASE }}"
          model: ollama_chat/llama3
          temperature: 1

        ollama-codellama:
          api_base: "{{ env.OLLAMA_API_BASE }}"
          model: ollama_chat/codellama
          temperature: 1

      # Optional: Set default model (use modelList key name)
      config:
        model: "ollama-llama3"  # This refers to the key name in modelList above
    ```

    !!! note "Ollama Service"
        You'll need to deploy Ollama as a service in your cluster. The `OLLAMA_API_BASE` should point to your Ollama service endpoint.

    **Alternative (OpenAI-compatible gateway)**

    If you hit compatibility issues with certain Ollama models via LiteLLM, you can configure an OpenAI-compatible gateway in your Robusta chart values:

    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: OPENAI_API_BASE
          value: "http://ollama-service:11434/v1"
        - name: OPENAI_API_KEY
          value: "YOUR_BEARER_TOKEN_HERE"

      modelList:
        ollama-alt:
          api_base: "{{ env.OPENAI_API_BASE }}"
          api_key: "{{ env.OPENAI_API_KEY }}"
          model: openai/OLLAMA_MODEL_NAME

      # Optional
      config:
        model: "ollama-alt"
    ```

## Additional Resources

HolmesGPT uses the LiteLLM API to support Ollama provider. Refer to [LiteLLM Ollama docs](https://docs.litellm.ai/docs/providers/ollama){:target="_blank"} for more details.

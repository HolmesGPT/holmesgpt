# OpenAI-Compatible Models

Configure HolmesGPT to use any OpenAI-compatible API.

!!! warning "Function Calling Required"
    Your model and inference server must support function calling (tool calling). Models that lack this capability may produce incorrect results.

## Overview

HolmesGPT works with **any OpenAI-compatible API endpoint**. This includes API gateways, proxy servers, and local inference servers—as long as they expose an OpenAI-compatible interface with function calling support.

## Quick Start

Point HolmesGPT at your OpenAI-compatible endpoint:

- Set `OPENAI_API_BASE` to your endpoint URL
- Set `OPENAI_API_KEY` to whatever API key your endpoint expects
- Use `openai/<model-name>` format for the model parameter, where `<model-name>` matches what your endpoint expects

=== "Holmes CLI"

    ```bash
    export OPENAI_API_BASE="http://localhost:8000/v1"
    export OPENAI_API_KEY="not-needed"
    # Optional: Custom CA certificate (base64-encoded)
    # export CERTIFICATE="$(cat /path/to/ca.crt | base64)"
    holmes ask "what pods are failing?" --model="openai/<your-model>"
    ```

=== "Holmes Helm Chart"

    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: OPENAI_API_BASE
        value: "http://your-inference-server:8000/v1"
      - name: OPENAI_API_KEY
        value: "not-needed"
        # OR if authentication is required:
        # valueFrom:
        #   secretKeyRef:
        #     name: holmes-secrets
        #     key: openai-api-key

    # Optional: Custom CA certificate (base64-encoded)
    # certificate: "LS0tLS1CRUdJTi..."

    # Configure at least one model using modelList
    modelList:
      local-llama:
        api_key: "not-needed"
        api_base: "{{ env.OPENAI_API_BASE }}"
        model: openai/llama3
        temperature: 1

      custom-model:
        api_key: "{{ env.OPENAI_API_KEY }}"
        api_base: "{{ env.OPENAI_API_BASE }}"
        model: openai/your-custom-model
        temperature: 1

    # Optional: Set default model (use modelList key name)
    config:
      model: "local-llama"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: OPENAI_API_BASE
          value: "http://your-inference-server:8000/v1"
        - name: OPENAI_API_KEY
          value: "not-needed"
          # OR if authentication is required:
          # valueFrom:
          #   secretKeyRef:
          #     name: robusta-holmes-secret
          #     key: openai-api-key

      # Optional: Custom CA certificate (base64-encoded)
      # certificate: "LS0tLS1CRUdJTi..."

      # Configure at least one model using modelList
      modelList:
        local-llama:
          api_key: "not-needed"
          api_base: "{{ env.OPENAI_API_BASE }}"
          model: openai/llama3
          temperature: 1

        custom-model:
          api_key: "{{ env.OPENAI_API_KEY }}"
          api_base: "{{ env.OPENAI_API_BASE }}"
          model: openai/your-custom-model
          temperature: 1

      # Optional: Set default model (use modelList key name)
      config:
        model: "local-llama"  # This refers to the key name in modelList above
    ```

## Known Limitations

- **vLLM**: [Does not yet support function calling](https://github.com/vllm-project/vllm/issues/1869){:target="_blank"}
- **Some models**: May hallucinate responses instead of reporting function calling limitations

## Additional Resources

HolmesGPT uses the LiteLLM API to support OpenAI-compatible providers. Refer to [LiteLLM OpenAI-compatible docs](https://litellm.vercel.app/docs/providers/openai_compatible){:target="_blank"} for more details.

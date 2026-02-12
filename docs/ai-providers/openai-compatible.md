# OpenAI-Compatible Models

HolmesGPT works with **any OpenAI-compatible API endpoint**. This includes API gateways, proxy servers, and local inference servers—as long as they expose an OpenAI-compatible interface with function calling support.

!!! warning "Function Calling Required"
    Your model and inference server must support function calling (tool calling). Models that lack this capability may produce incorrect results.

## Quick Start

Point HolmesGPT at your OpenAI-compatible endpoint:

- Set `OPENAI_API_BASE` to your endpoint URL
- Set `OPENAI_API_KEY` to your endpoint's API key, or any placeholder value like `"none"` if your endpoint doesn't require authentication (this parameter is always required by LiteLLM)
- Use `openai/<model-name>` format for the model parameter, where `<model-name>` matches what your endpoint expects
- Optional: Set `CERTIFICATE` to a base64-encoded CA certificate if your endpoint uses a custom CA

=== "Holmes CLI"

    ```bash
    export OPENAI_API_BASE="http://localhost:8000/v1"
    export OPENAI_API_KEY="none"  # Or any placeholder if endpoint doesn't need auth
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
        value: "none"  # Or any placeholder if endpoint doesn't need auth
        # If authentication is required, use a secret instead:
        # valueFrom:
        #   secretKeyRef:
        #     name: holmes-secrets
        #     key: openai-api-key

    # Optional: Custom CA certificate (base64-encoded)
    # certificate: "LS0tLS1CRUdJTi..."

    modelList:
      my-model:
        api_key: "{{ env.OPENAI_API_KEY }}"
        api_base: "{{ env.OPENAI_API_BASE }}"
        model: openai/your-model-name
        temperature: 1

    config:
      model: "my-model"
    ```

=== "Robusta Helm Chart"

    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: OPENAI_API_BASE
          value: "http://your-inference-server:8000/v1"
        - name: OPENAI_API_KEY
          value: "none"  # Or any placeholder if endpoint doesn't need auth
          # If authentication is required, use a secret instead:
          # valueFrom:
          #   secretKeyRef:
          #     name: robusta-holmes-secret
          #     key: openai-api-key

      # Optional: Custom CA certificate (base64-encoded)
      # certificate: "LS0tLS1CRUdJTi..."

      modelList:
        my-model:
          api_key: "{{ env.OPENAI_API_KEY }}"
          api_base: "{{ env.OPENAI_API_BASE }}"
          model: openai/your-model-name
          temperature: 1

      config:
        model: "my-model"
    ```

## Known Limitations

- **Some models**: May hallucinate responses instead of reporting function calling limitations. See [benchmark results](../development/evaluations/index.md) for recommended models.

## Additional Resources

HolmesGPT uses the LiteLLM API to support OpenAI-compatible providers. Refer to [LiteLLM OpenAI-compatible docs](https://litellm.vercel.app/docs/providers/openai_compatible){:target="_blank"} for more details.

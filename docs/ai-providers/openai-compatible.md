# OpenAI-Compatible Models

Configure HolmesGPT to use any OpenAI-compatible API.

!!! warning "Function Calling Required"
    Your model and inference server must support function calling (tool calling). Models that lack this capability may produce incorrect results.

## Requirements

- **Function calling support** - OpenAI-style tool calling
- **OpenAI-compatible API** - Standard endpoints and request/response format

## Supported Inference Servers

- [llama-cpp-python](https://github.com/abetlen/llama-cpp-python){:target="_blank"}
- [LocalAI](https://localai.io/){:target="_blank"}
- [Text Generation WebUI](https://github.com/oobabooga/text-generation-webui){:target="_blank"} (with OpenAI extension)

## Configuration

=== "Holmes CLI"

    ```bash
    export OPENAI_API_BASE="http://localhost:8000/v1"
    export OPENAI_API_KEY="not-needed"
    holmes ask "what pods are failing?" --model="openai/<your-model>"
    ```

=== "Holmes Helm Chart"

    **Create Kubernetes Secret (if authentication is required):**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=openai-api-key="your-api-key-if-needed" \
      -n <namespace>
    ```

    **Configure Helm Values:**
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

    **Create Kubernetes Secret (if authentication is required):**
    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=openai-api-key="your-api-key-if-needed" \
      -n <namespace>
    ```

    **Configure Helm Values:**
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

## Using CLI Parameters

You can also specify the model directly as a command-line parameter:

```bash
holmes ask "what pods are failing?" --model="openai/<your-model>"
```

## Setup Examples

### LocalAI

```bash
docker run -p 8080:8080 localai/localai:latest
export OPENAI_API_BASE="http://localhost:8080/v1"
```

### llama-cpp-python

```bash
pip install 'llama-cpp-python[server]'
python -m llama_cpp.server --model model.gguf --chat_format chatml
export OPENAI_API_BASE="http://localhost:8000/v1"
holmes ask "analyze my deployment" --model=openai/your-loaded-model
```

## Custom SSL Certificates

If your LLM provider uses a custom Certificate Authority (CA):

```bash
# Base64 encode your certificate and set it as an environment variable
export CERTIFICATE="base64-encoded-cert-here"
```

## Known Limitations

- **vLLM**: [Does not yet support function calling](https://github.com/vllm-project/vllm/issues/1869){:target="_blank"}
- **Text Generation WebUI**: Requires OpenAI extension enabled
- **Some models**: May hallucinate responses instead of reporting function calling limitations

## Additional Resources

HolmesGPT uses the LiteLLM API to support OpenAI-compatible providers. Refer to [LiteLLM OpenAI-compatible docs](https://litellm.vercel.app/docs/providers/openai_compatible){:target="_blank"} for more details.

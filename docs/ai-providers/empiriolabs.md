# EmpirioLabs AI

Configure HolmesGPT to use [EmpirioLabs AI](https://empiriolabs.ai/) through its OpenAI-compatible API.

## Quick Start

Use the OpenAI-compatible provider and set the EmpirioLabs base URL:

=== "Holmes CLI"

    ```bash
    export OPENAI_API_KEY="sk-empiriolabs-..."
    export OPENAI_API_BASE="https://api.empiriolabs.ai/v1"

    holmes ask "what pods are failing?" --model="openai/qwen3-max"
    ```

=== "Holmes Helm Chart"

    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: OPENAI_API_BASE
        value: "https://api.empiriolabs.ai/v1"
      - name: OPENAI_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: empirio-api-key

    modelList:
      empirio-qwen:
        api_key: "{{ env.OPENAI_API_KEY }}"
        api_base: "{{ env.OPENAI_API_BASE }}"
        model: openai/qwen3-max
        temperature: 1

    config:
      model: "empirio-qwen"
    ```

=== "Robusta Helm Chart"

    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: OPENAI_API_BASE
          value: "https://api.empiriolabs.ai/v1"
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: empirio-api-key

      modelList:
        empirio-qwen:
          api_key: "{{ env.OPENAI_API_KEY }}"
          api_base: "{{ env.OPENAI_API_BASE }}"
          model: openai/qwen3-max
          temperature: 1

      config:
        model: "empirio-qwen"
    ```

## Models

Use any chat model available in your EmpirioLabs account. See the [EmpirioLabs model catalog](https://empiriolabs.ai/models) for current model IDs, then prefix the model with `openai/` in HolmesGPT.

## Notes

- EmpirioLabs uses an OpenAI-compatible `/v1` endpoint.
- HolmesGPT requires models that support function calling for tool use.

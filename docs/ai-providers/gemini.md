# Gemini

Configure HolmesGPT to use Google's Gemini models via Google AI Studio.

## Setup

Get your API key from [Google AI Studio](https://aistudio.google.com/app/apikey){:target="_blank"}.

## Configuration

=== "Holmes CLI"

    ```bash
    export GEMINI_API_KEY="your-gemini-api-key"
    export TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS=true
    holmes ask "what pods are failing?" --model="gemini/<your-gemini-model>"
    ```

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=gemini-api-key="your-gemini-api-key" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: GEMINI_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: gemini-api-key
      - name: TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS
        value: "true"  # Required for Gemini - see Environment Variables Reference

    # Configure at least one model using modelList
    modelList:
      gemini-pro:
        api_key: "{{ env.GEMINI_API_KEY }}"
        model: gemini/gemini-pro
        temperature: 1

      gemini-flash:
        api_key: "{{ env.GEMINI_API_KEY }}"
        model: gemini/gemini-1.5-flash
        temperature: 1

      gemini-pro-exp:
        api_key: "{{ env.GEMINI_API_KEY }}"
        model: gemini/gemini-exp-1206
        temperature: 1

    # Optional: Set default model (use modelList key name)
    config:
      model: "gemini-pro"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=gemini-api-key="your-gemini-api-key" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: GEMINI_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: gemini-api-key
        - name: TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS
          value: "true"  # Required for Gemini - see Environment Variables Reference

      # Configure at least one model using modelList
      modelList:
        gemini-pro:
          api_key: "{{ env.GEMINI_API_KEY }}"
          model: gemini/gemini-pro
          temperature: 1

        gemini-flash:
          api_key: "{{ env.GEMINI_API_KEY }}"
          model: gemini/gemini-1.5-flash
          temperature: 1

        gemini-pro-exp:
          api_key: "{{ env.GEMINI_API_KEY }}"
          model: gemini/gemini-exp-1206
          temperature: 1

      # Optional: Set default model (use modelList key name)
      config:
        model: "gemini-pro"  # This refers to the key name in modelList above
    ```

## Using CLI Parameters

You can also pass the API key directly as a command-line parameter:

```bash
holmes ask "what pods are failing?" --model="gemini/<your-gemini-model>" --api-key="your-api-key"
```

## Additional Resources

HolmesGPT uses the LiteLLM API to support Gemini provider. Refer to [LiteLLM Gemini docs](https://litellm.vercel.app/docs/providers/gemini){:target="_blank"} for more details.

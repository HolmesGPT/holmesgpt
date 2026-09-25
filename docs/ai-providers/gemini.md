# Gemini

Configure HolmesGPT to use Google's Gemini models via Google AI Studio.

## Setup

Get your API key from [Google AI Studio](https://aistudio.google.com/app/apikey){:target="_blank"}.

## Configuration

=== "Holmes CLI"

    ```bash
    export GEMINI_API_KEY="your-gemini-api-key"
    export TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS=true
    export MODEL="gemini/<your-gemini-model>"
    holmes ask "what pods are failing?"

    # Or specify the model explicitly per command:
    holmes ask "what pods are failing?" --model="gemini/<your-gemini-model>"
    ```

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-gemini \
      --from-literal=GEMINI_API_KEY="your-gemini-api-key" \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-gemini

    additionalEnvVars:
      - name: TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS
        value: "true"  # Required for Gemini - see Environment Variables Reference
      # Optional: Set default model (use modelList key name)
      - name: MODEL
        value: "gemini-pro"  # This refers to the key name in modelList below

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
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmesgpt robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-gemini \
      --from-literal=GEMINI_API_KEY="your-gemini-api-key" \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-gemini

      additionalEnvVars:
        - name: TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS
          value: "true"  # Required for Gemini - see Environment Variables Reference
        # Optional: Set default model (use modelList key name)
        - name: MODEL
          value: "gemini-pro"  # This refers to the key name in modelList below

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
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Using CLI Parameters

You can also pass the API key directly as a command-line parameter:

```bash
export TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS=true
holmes ask "what pods are failing?" --model="gemini/<your-gemini-model>" --api-key="your-api-key"
```

## Additional Resources

HolmesGPT uses the LiteLLM API to support Gemini provider. Refer to [LiteLLM Gemini docs](https://litellm.vercel.app/docs/providers/gemini){:target="_blank"} for more details.

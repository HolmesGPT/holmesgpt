# Azure OpenAI

Configure HolmesGPT to use Azure OpenAI Service.

## Setup

Create an [Azure OpenAI resource](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/create-resource?pivots=web-portal#create-a-resource){:target="_blank"}.

## Configuration

=== "Holmes CLI"

    ```bash
    export AZURE_API_VERSION="2024-02-15-preview"
    export AZURE_API_BASE="https://your-resource.openai.azure.com"
    export AZURE_API_KEY="your-azure-api-key"

    holmes ask "what pods are failing?" --model="azure/<your-deployment-name>"
    ```

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=azure-api-key="your-azure-api-key" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: AZURE_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: azure-api-key

    # Configure at least one model using modelList
    modelList:
      azure-gpt-41:
        api_key: "{{ env.AZURE_API_KEY }}"
        model: azure/gpt-4.1
        api_base: https://your-resource.openai.azure.com/
        api_version: "2025-01-01-preview"
        temperature: 0

      azure-gpt-5:
        api_key: "{{ env.AZURE_API_KEY }}"
        model: azure/gpt-5
        api_base: https://your-resource.openai.azure.com/
        api_version: "2025-01-01-preview"
        temperature: 1

    # Optional: Set default model (use modelList key name)
    config:
      model: "azure-gpt-41"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=azure-api-key="your-azure-api-key" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: AZURE_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: azure-api-key

      # Configure at least one model using modelList
      modelList:
        azure-gpt-41:
          api_key: "{{ env.AZURE_API_KEY }}"
          model: azure/gpt-4.1
          api_base: https://your-resource.openai.azure.com/
          api_version: "2025-01-01-preview"
          temperature: 0

        azure-gpt-5:
          api_key: "{{ env.AZURE_API_KEY }}"
          model: azure/gpt-5
          api_base: https://your-resource.openai.azure.com/
          api_version: "2025-01-01-preview"
          temperature: 1

      # Optional: Set default model (use modelList key name)
      config:
        model: "azure-gpt-41"  # This refers to the key name in modelList above
    ```

## Using CLI Parameters

You can also pass the API key directly as a command-line parameter:

```bash
holmes ask "what pods are failing?" --model="azure/<your-deployment-name>" --api-key="your-api-key"
```

## Additional Resources

HolmesGPT uses the LiteLLM API to support Azure OpenAI provider. For advanced authentication methods (Azure AD, Managed Identity, Workload Identity, Service Principal), refer to:

- [LiteLLM Azure OpenAI docs](https://litellm.vercel.app/docs/providers/azure){:target="_blank"}
- [LiteLLM OIDC docs](https://docs.litellm.ai/docs/oidc){:target="_blank"} - For Workload Identity and Managed Identity authentication
- [LiteLLM Azure AD Token support (PR #3861)](https://github.com/BerriAI/litellm/pull/3861){:target="_blank"} - AKS Workload Identity OIDC support
- [LiteLLM Azure Service Principal (PR #5131)](https://github.com/BerriAI/litellm/pull/5131){:target="_blank"} - Service Principal with client credentials

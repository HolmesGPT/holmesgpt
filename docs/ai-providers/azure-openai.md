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

## Azure AD Authentication Methods

HolmesGPT supports several Azure AD authentication methods through LiteLLM, allowing you to avoid using API keys.

### Managed Identity (UAMI/SAMI)

For Azure VMs, AKS clusters, or other Azure services with managed identity enabled, use the `oidc/azure/` token provider. This works with both User Assigned Managed Identity (UAMI) and System Assigned Managed Identity (SAMI).

=== "Holmes Helm Chart"

    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: AZURE_CLIENT_ID
        value: "your-managed-identity-client-id"  # Required for User Assigned Managed Identity
      - name: AZURE_TENANT_ID
        value: "your-tenant-id"

    modelList:
      azure-gpt-41:
        model: azure/gpt-4.1
        api_base: https://your-resource.openai.azure.com/
        api_version: "2025-01-01-preview"
        azure_ad_token: "oidc/azure/https://cognitiveservices.azure.com"
        # No api_key needed - uses managed identity

    config:
      model: "azure-gpt-41"
    ```

=== "Robusta Helm Chart"

    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: AZURE_CLIENT_ID
          value: "your-managed-identity-client-id"  # Required for User Assigned Managed Identity
        - name: AZURE_TENANT_ID
          value: "your-tenant-id"

      modelList:
        azure-gpt-41:
          model: azure/gpt-4.1
          api_base: https://your-resource.openai.azure.com/
          api_version: "2025-01-01-preview"
          azure_ad_token: "oidc/azure/https://cognitiveservices.azure.com"
          # No api_key needed - uses managed identity

      config:
        model: "azure-gpt-41"
    ```

!!! note "System Assigned vs User Assigned Managed Identity"

    - **System Assigned (SAMI)**: No `AZURE_CLIENT_ID` needed - Azure automatically provides the identity
    - **User Assigned (UAMI)**: Set `AZURE_CLIENT_ID` to your managed identity's client ID

### Workload Identity (AKS)

For AKS clusters with [Workload Identity](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview){:target="_blank"} enabled, use the `oidc/azure/` token provider.

=== "Holmes Helm Chart"

    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: AZURE_CLIENT_ID
        value: "your-app-client-id"
      - name: AZURE_TENANT_ID
        value: "your-tenant-id"

    modelList:
      azure-gpt-41:
        model: azure/gpt-4.1
        api_base: https://your-resource.openai.azure.com/
        api_version: "2025-01-01-preview"
        azure_ad_token: "oidc/azure/https://cognitiveservices.azure.com"
        # No api_key needed - uses workload identity

    config:
      model: "azure-gpt-41"
    ```

=== "Robusta Helm Chart"

    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: AZURE_CLIENT_ID
          value: "your-app-client-id"
        - name: AZURE_TENANT_ID
          value: "your-tenant-id"

      modelList:
        azure-gpt-41:
          model: azure/gpt-4.1
          api_base: https://your-resource.openai.azure.com/
          api_version: "2025-01-01-preview"
          azure_ad_token: "oidc/azure/https://cognitiveservices.azure.com"
          # No api_key needed - uses workload identity

      config:
        model: "azure-gpt-41"
    ```

!!! tip "AKS Workload Identity Setup"
    Ensure your AKS cluster has workload identity enabled and the Holmes service account is federated with your Azure AD application. See [Microsoft's Workload Identity documentation](https://learn.microsoft.com/en-us/azure/aks/workload-identity-deploy-cluster){:target="_blank"}.

### Service Principal

For service principal authentication using client credentials:

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-azure-sp \
      --from-literal=client-secret="your-client-secret" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: AZURE_CLIENT_SECRET
        valueFrom:
          secretKeyRef:
            name: holmes-azure-sp
            key: client-secret

    modelList:
      azure-gpt-41:
        model: azure/gpt-4.1
        api_base: https://your-resource.openai.azure.com/
        api_version: "2025-01-01-preview"
        tenant_id: "your-tenant-id"
        client_id: "your-client-id"
        client_secret: "{{ env.AZURE_CLIENT_SECRET }}"
        # No api_key needed - uses service principal

    config:
      model: "azure-gpt-41"
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic robusta-azure-sp \
      --from-literal=client-secret="your-client-secret" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: AZURE_CLIENT_SECRET
          valueFrom:
            secretKeyRef:
              name: robusta-azure-sp
              key: client-secret

      modelList:
        azure-gpt-41:
          model: azure/gpt-4.1
          api_base: https://your-resource.openai.azure.com/
          api_version: "2025-01-01-preview"
          tenant_id: "your-tenant-id"
          client_id: "your-client-id"
          client_secret: "{{ env.AZURE_CLIENT_SECRET }}"
          # No api_key needed - uses service principal

      config:
        model: "azure-gpt-41"
    ```

## Additional Resources

HolmesGPT uses the LiteLLM API to support Azure OpenAI provider. Refer to [LiteLLM Azure docs](https://litellm.vercel.app/docs/providers/azure){:target="_blank"} and [LiteLLM OIDC docs](https://docs.litellm.ai/docs/oidc){:target="_blank"} for more details.

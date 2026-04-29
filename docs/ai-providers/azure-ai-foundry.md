# Azure AI Foundry

Configure HolmesGPT to use Azure AI Foundry (formerly Azure OpenAI Service).

## Setup

Create an [Azure AI Foundry resource](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/create-resource?pivots=web-portal#create-a-resource){:target="_blank"} and deploy a model.

## Model Types

Azure AI Foundry supports two model families, each using a different endpoint and configuration:

- **Anthropic models (recommended)** — e.g. Claude Opus 4.7. The model name uses the `anthropic/<model>` prefix and the endpoint uses the `/anthropic` path on an `ai.azure.com` domain. No `api_version` is required.
- **Azure OpenAI-style deployments** — e.g. GPT-5.4. The model name uses the `azure/<your-deployment-name>` prefix (the deployment name you created in Azure, not the underlying model ID) and the endpoint uses the `cognitiveservices.azure.com` domain. A matching `api_version` is required.

The examples below lead with the Anthropic option and include a GPT deployment alongside for reference.

## Configuration

=== "Holmes CLI"

    **Anthropic models (recommended):**

    ```bash
    export AZURE_API_KEY="your-azure-api-key"
    export AZURE_API_BASE="https://XXXX.services.ai.azure.com/anthropic"

    holmes ask "what pods are failing?" --model="anthropic/claude-opus-4-7"
    ```

    **Azure OpenAI deployments:**

    ```bash
    export AZURE_API_KEY="your-azure-api-key"
    export AZURE_API_BASE="https://YYYY.cognitiveservices.azure.com/"
    export AZURE_API_VERSION="2025-04-01-preview"

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
      # Anthropic model on Azure AI Foundry (recommended)
      azure-opus-4-7:
        api_key: "{{ env.AZURE_API_KEY }}"
        model: anthropic/claude-opus-4-7
        api_base: https://XXXX.services.ai.azure.com/anthropic
        temperature: 1

      # Azure OpenAI-style deployment (e.g. GPT-5.4)
      azure-gpt-5-4:
        api_key: "{{ env.AZURE_API_KEY }}"
        model: azure/my-gpt-5.4-deployment
        api_base: https://YYYY.cognitiveservices.azure.com/
        api_version: "2025-04-01-preview"

    # Optional: Set default model (use modelList key name)
    config:
      model: "azure-opus-4-7"  # This refers to the key name in modelList above
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
        # Anthropic model on Azure AI Foundry (recommended)
        azure-opus-4-7:
          api_key: "{{ env.AZURE_API_KEY }}"
          model: anthropic/claude-opus-4-7
          api_base: https://XXXX.services.ai.azure.com/anthropic
          temperature: 1

        # Azure OpenAI-style deployment (e.g. GPT-5.4)
        azure-gpt-5-4:
          api_key: "{{ env.AZURE_API_KEY }}"
          model: azure/my-gpt-5.4-deployment
          api_base: https://YYYY.cognitiveservices.azure.com/
          api_version: "2025-04-01-preview"

      # Optional: Set default model (use modelList key name)
      config:
        model: "azure-opus-4-7"  # This refers to the key name in modelList above
    ```

## Using CLI Parameters

You can also pass the API key directly as a command-line parameter:

```bash
holmes ask "what pods are failing?" --model="anthropic/claude-opus-4-7" --api-key="your-api-key"
```

## Microsoft Entra ID Authentication

Authenticate HolmesGPT to Azure AI Foundry using Microsoft Entra ID (formerly Azure AD) instead of an API key.

This uses `DefaultAzureCredential` to obtain a bearer token, which is ideal for organizations that enforce identity-based access control and [disable local authentication (API keys)](https://docs.azure.cn/en-us/ai-services/disable-local-auth#how-to-disable-local-authentication){:target="_blank"} on their Azure AI resources.

### Why Disable Local Auth?

Azure AI Foundry resources support two authentication methods: API keys (local auth) and Microsoft Entra ID. For production environments, Microsoft recommends disabling local authentication so that:

- All access is governed by Azure RBAC — no static secrets to rotate or leak
- Every request is tied to an auditable identity
- Conditional Access policies and Privileged Identity Management apply

When local auth is disabled on your Azure resource, API key access is completely blocked and only Entra ID tokens are accepted. HolmesGPT supports this scenario via the `AZURE_AD_TOKEN_AUTH` environment variable.

To disable local auth on your resource:

```bash
az cognitiveservices account update \
  --name <resource-name> \
  --resource-group <rg> \
  --custom-domain <resource-name> \
  --disable-local-auth true
```

For more details, see [Disable local authentication in Azure AI Services](https://docs.azure.cn/en-us/ai-services/disable-local-auth#how-to-disable-local-authentication){:target="_blank"}.

### How It Works

Set `AZURE_AD_TOKEN_AUTH=true` to enable Entra ID authentication. When enabled, HolmesGPT will:

- Skip the `AZURE_API_KEY` requirement during startup validation
- Obtain a token via `DefaultAzureCredential` and pass it to each LLM request
- Cache the token for 1 hour and refresh automatically

### Required Permissions

The identity used by HolmesGPT must be able to perform the action:

```
Microsoft.CognitiveServices/accounts/OpenAI/deployments/chat/completions/action
```

on the target Azure AI Foundry resource.

You can grant this with the **Cognitive Services OpenAI User** or **Azure AI User** built-in role, or a custom role that includes the action above:

```bash
az role assignment create \
  --assignee "<identity-principal-id>" \
  --role "Cognitive Services OpenAI User" \
  --scope "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<resource>"
```

### Running Locally

When running HolmesGPT on your machine, `DefaultAzureCredential` tries credentials in order: environment variables, Azure CLI, Azure PowerShell, etc. The simplest method is to sign in with the Azure CLI:

```bash
az login

export AZURE_AD_TOKEN_AUTH=true
export AZURE_API_BASE="https://XXXX.services.ai.azure.com/anthropic"

holmes ask "what pods are failing?" --model="anthropic/claude-opus-4-7"
```

No `AZURE_API_KEY` is needed.

For an Azure OpenAI-style deployment, use the matching `cognitiveservices.azure.com` endpoint, set `AZURE_API_VERSION`, and pass `--model="azure/<your-deployment-name>"` instead.

For service-principal auth locally, set these environment variables instead:

```bash
export AZURE_CLIENT_ID="<app-client-id>"
export AZURE_CLIENT_SECRET="<app-client-secret>"
export AZURE_TENANT_ID="<tenant-id>"
```

### Running in Kubernetes with Workload Identity

When running as a pod in AKS, use [AKS Workload Identity](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview){:target="_blank"} so the pod authenticates via a federated credential rather than a stored secret. The managed identity (or service principal) bound to the pod must have the **Cognitive Services OpenAI User** role on the target resource.

=== "Holmes Helm Chart"

    **Prerequisites:**

    - AKS cluster with OIDC issuer and workload identity enabled
    - A managed identity with the **Cognitive Services OpenAI User** role on your Azure AI Foundry resource
    - A federated credential linking the managed identity to the Holmes ServiceAccount

    **Set up the identity and federation:**

    ```bash
    # Get the OIDC issuer URL
    OIDC_ISSUER=$(az aks show -n <cluster> -g <rg> --query "oidcIssuerProfile.issuerUrl" -o tsv)

    # Create a managed identity
    az identity create -n holmes-identity -g <rg>
    IDENTITY_CLIENT_ID=$(az identity show -n holmes-identity -g <rg> --query clientId -o tsv)
    IDENTITY_PRINCIPAL_ID=$(az identity show -n holmes-identity -g <rg> --query principalId -o tsv)

    # Assign the Cognitive Services OpenAI User role
    az role assignment create \
      --assignee "$IDENTITY_PRINCIPAL_ID" \
      --role "Cognitive Services OpenAI User" \
      --scope "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<resource>"

    # Create a federated credential for the Holmes ServiceAccount
    az identity federated-credential create \
      --name holmes-federated \
      --identity-name holmes-identity \
      --resource-group <rg> \
      --issuer "$OIDC_ISSUER" \
      --subject "system:serviceaccount:<namespace>:holmes" \
      --audiences "api://AzureADTokenExchange"
    ```

    **Configure Helm Values:**

    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: AZURE_AD_TOKEN_AUTH
        value: "true"
      - name: AZURE_CLIENT_ID
        value: "<managed-identity-client-id>"
      - name: AZURE_TENANT_ID
        value: "<tenant-id>"

    serviceAccount:
      annotations:
        azure.workload.identity/client-id: "<managed-identity-client-id>"

    podLabels:
      azure.workload.identity/use: "true"

    modelList:
      # Anthropic model on Azure AI Foundry (recommended)
      azure-opus-4-7:
        model: anthropic/claude-opus-4-7
        api_base: https://XXXX.services.ai.azure.com/anthropic
        temperature: 1

      # Azure OpenAI-style deployment (e.g. GPT-5.4)
      azure-gpt-5-4:
        model: azure/my-gpt-5.4-deployment
        api_base: https://YYYY.cognitiveservices.azure.com/
        api_version: "2025-04-01-preview"

    config:
      model: "azure-opus-4-7"
    ```

    Note that `api_key` is omitted from the `modelList` entries — authentication is handled entirely by the workload identity token.

=== "Robusta Helm Chart"

    **Configure Helm Values:**

    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: AZURE_AD_TOKEN_AUTH
          value: "true"
        - name: AZURE_CLIENT_ID
          value: "<managed-identity-client-id>"
        - name: AZURE_TENANT_ID
          value: "<tenant-id>"

      serviceAccount:
        annotations:
          azure.workload.identity/client-id: "<managed-identity-client-id>"

      podLabels:
        azure.workload.identity/use: "true"

      modelList:
        # Anthropic model on Azure AI Foundry (recommended)
        azure-opus-4-7:
          model: anthropic/claude-opus-4-7
          api_base: https://XXXX.services.ai.azure.com/anthropic
          temperature: 1

        # Azure OpenAI-style deployment (e.g. GPT-5.4)
        azure-gpt-5-4:
          model: azure/my-gpt-5.4-deployment
          api_base: https://YYYY.cognitiveservices.azure.com/
          api_version: "2025-04-01-preview"

      config:
        model: "azure-opus-4-7"
    ```

### Troubleshooting

```bash
# Verify the pod has workload identity labels and env vars injected
kubectl describe pod -l app=holmes -n <namespace> | grep -A5 "AZURE_"

# Test that the identity can obtain a token (from inside the pod)
kubectl exec -n <namespace> deploy/holmes -- python -c "
from azure.identity import DefaultAzureCredential
token = DefaultAzureCredential().get_token('https://cognitiveservices.azure.com/.default')
print('Token obtained, expires at:', token.expires_on)
"

# Check role assignment
az role assignment list \
  --assignee "<identity-principal-id>" \
  --scope "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<resource>" \
  --output table
```

## Additional Resources

- [LiteLLM Azure docs](https://litellm.vercel.app/docs/providers/azure){:target="_blank"}
- [Disable local authentication in Azure AI Services](https://docs.azure.cn/en-us/ai-services/disable-local-auth){:target="_blank"}
- [Cognitive Services OpenAI User role](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/role-based-access-control){:target="_blank"}
- [AKS Workload Identity overview](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview){:target="_blank"}
- [DefaultAzureCredential documentation](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential){:target="_blank"}

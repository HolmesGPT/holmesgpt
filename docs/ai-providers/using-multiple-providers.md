# Using Multiple Providers

Define multiple model configurations and switch between them by name. This is useful when you work with different AI providers, API keys, endpoints, or parameters.

## Configuration

=== "Holmes CLI"

    **1. Create a model list file:**

    ```yaml
    # model_list.yaml
    sonnet:
        aws_access_key_id: "your-access-key"
        aws_region_name: us-east-1
        aws_secret_access_key: "your-secret-key"
        model: bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0
        temperature: 1
        thinking:
            budget_tokens: 10000
            type: enabled

    azure-5:
        api_base: https://your-resource.openai.azure.com
        api_key: "your-api-key"
        api_version: 2025-01-01-preview
        model: azure/gpt-5
        temperature: 0
    ```

    **2. Set the environment variable:**

    ```bash
    export MODEL_LIST_FILE_LOCATION="/path/to/model_list.yaml"
    ```

    **3. Use models by name:**

    ```bash
    holmes ask "what pods are failing?" --model=sonnet --no-interactive
    holmes ask "analyze deployment" --model=azure-5 --no-interactive
    ```

    When using `--model`, specify the model name (key) from your YAML file, not the underlying model identifier. All configuration (API keys, endpoints, temperature, etc.) will be automatically loaded from the model list file.

    **Note:** Environment variable substitution is supported using `{{ env.VARIABLE_NAME }}` syntax in the model list file.

=== "Holmes Helm Chart"

    Configure multiple models using the `modelList` parameter in your Helm values, along with the necessary environment variables.

    **Create the Kubernetes Secret:**

    ```bash
    # Example with all providers - only include what you're using
    kubectl create secret generic holmes-secrets \
      --from-literal=openai-api-key="sk-..." \
      --from-literal=anthropic-api-key="sk-ant-..." \
      --from-literal=azure-api-key="..." \
      --from-literal=aws-access-key-id="AKIA..." \
      --from-literal=aws-secret-access-key="..." \
      -n <namespace>

    # Example with just OpenAI and Anthropic
    kubectl create secret generic holmes-secrets \
      --from-literal=openai-api-key="sk-..." \
      --from-literal=anthropic-api-key="sk-ant-..." \
      -n <namespace>
    ```

    **Configure Helm Values:**

    ```yaml
    # values.yaml
    # Reference only the API keys you created in the secret
    additionalEnvVars:
      - name: AZURE_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: azure-api-key
      - name: ANTHROPIC_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: anthropic-api-key
      - name: AWS_ACCESS_KEY_ID
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: aws-access-key-id
      - name: AWS_SECRET_ACCESS_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: aws-secret-access-key
      - name: OPENAI_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: openai-api-key

    # Configure the model list using the environment variables
    modelList:
      # Standard OpenAI
      openai-4.1:
        api_key: "{{ env.OPENAI_API_KEY }}"
        model: openai/gpt-4.1
        temperature: 0

      # Azure OpenAI Models
      azure-41:
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
        temperature: 1 # only 1 is supported for gpt-5 models

      # Anthropic Models
      claude-sonnet-4:
        api_key: "{{ env.ANTHROPIC_API_KEY }}"
        model: claude-sonnet-4-20250514
        temperature: 1
        thinking:
          budget_tokens: 10000
          type: enabled

      claude-opus-4-1:
        api_key: "{{ env.ANTHROPIC_API_KEY }}"
        model: claude-opus-4-1-20250805
        temperature: 0

      # AWS Bedrock
      bedrock-claude:
        aws_access_key_id: "{{ env.AWS_ACCESS_KEY_ID }}"
        aws_region_name: us-east-1
        aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
        model: bedrock/anthropic.claude-sonnet-4-20250514-v1:0
        temperature: 1
        thinking:
          budget_tokens: 10000
          type: enabled
    ```

    When multiple providers are defined, users can specify the `model` parameter via the HTTP API. If deployed with Robusta, a model selector dropdown is also available in the UI.

=== "Robusta Helm Chart"

    Configure multiple models using the `modelList` parameter in your Helm values, along with the necessary environment variables. All Holmes configuration is nested under the `holmes:` key.

    **Create the Kubernetes Secret:**

    ```bash
    # Example with all providers - only include what you're using
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=openai-api-key="sk-..." \
      --from-literal=anthropic-api-key="sk-ant-..." \
      --from-literal=azure-api-key="..." \
      --from-literal=aws-access-key-id="AKIA..." \
      --from-literal=aws-secret-access-key="..." \
      -n <namespace>

    # Example with just OpenAI and Anthropic
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=openai-api-key="sk-..." \
      --from-literal=anthropic-api-key="sk-ant-..." \
      -n <namespace>
    ```

    **Configure Helm Values:**

    ```yaml
    # values.yaml
    holmes:
      # Reference only the API keys you created in the secret
      additionalEnvVars:
        - name: AZURE_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: azure-api-key
        - name: ANTHROPIC_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: anthropic-api-key
        - name: AWS_ACCESS_KEY_ID
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: aws-access-key-id
        - name: AWS_SECRET_ACCESS_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: aws-secret-access-key
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: openai-api-key

      # Configure the model list using the environment variables
      modelList:
        # Standard OpenAI
        openai-4.1:
          api_key: "{{ env.OPENAI_API_KEY }}"
          model: openai/gpt-4.1
          temperature: 0

        # Azure OpenAI Models
        azure-41:
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
          temperature: 1 # only 1 is supported for gpt-5 models

        # Anthropic Models
        claude-sonnet-4:
          api_key: "{{ env.ANTHROPIC_API_KEY }}"
          model: claude-sonnet-4-20250514
          temperature: 1
          thinking:
            budget_tokens: 10000
            type: enabled

        claude-opus-4-1:
          api_key: "{{ env.ANTHROPIC_API_KEY }}"
          model: claude-opus-4-1-20250805
          temperature: 0

        # AWS Bedrock
        bedrock-claude:
          aws_access_key_id: "{{ env.AWS_ACCESS_KEY_ID }}"
          aws_region_name: us-east-1
          aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
          model: bedrock/anthropic.claude-sonnet-4-20250514-v1:0
          temperature: 1
          thinking:
            budget_tokens: 10000
            type: enabled
    ```

    When multiple providers are defined, users can select which model to use from a dropdown in the Robusta UI, or specify a `model` parameter when using the HTTP API directly.

## Model Parameters

Each model in the list can accept any parameter supported by LiteLLM for that provider. The `model` parameter is required, while authentication requirements vary by provider. Any additional LiteLLM parameters will be passed directly through to the provider.

**Required Parameter:**

- `model`: Model identifier (provider-specific format)

**Common Parameters:**

- `api_key`: API key for authentication where required (can use `{{ env.VAR_NAME }}` syntax)
- `temperature`: Creativity level (0-2, lower is more deterministic)

**Additional Parameters:**

You can pass any LiteLLM-supported parameter for your provider. Examples include:

- **Azure**: `api_base`, `api_version`, `deployment_id`
- **Anthropic**: `thinking` (with `budget_tokens` and `type`)
- **AWS Bedrock**: `aws_access_key_id`, `aws_secret_access_key`, `aws_region_name`, `aws_session_token`
- **Google Vertex**: `vertex_project`, `vertex_location`

Refer to [LiteLLM documentation](https://docs.litellm.ai/docs/providers) for the complete list of parameters supported by each provider.

## User Experience

When multiple models are configured:

### Robusta UI
1. Users see a **model selector dropdown** in the Robusta UI
2. Each model appears with its configured name (e.g., "azure-4o", "claude-sonnet-4")
3. Users can switch between models for different investigations

### HTTP API
Clients can specify the model in their API requests:
```json
{
  "ask": "What pods are failing?",
  "model": "claude-sonnet-4"
}
```

### Robusta AI Integration
If you're a Robusta SaaS customer, you can also use [Robusta AI](robusta-ai.md) which provides access to multiple models without managing individual API keys.

## See Also

- [Environment Variables Reference](../reference/environment-variables.md)
- [UI Installation](../installation/ui-installation.md)
- [Helm Configuration](../reference/helm-configuration.md)
- Individual provider documentation for specific configuration details

# GitHub Models

Configure HolmesGPT to use GitHub's AI Models Marketplace.

## Setup

Get a [GitHub Personal Access Token](https://github.com/settings/tokens){:target="_blank"} with appropriate permissions to access GitHub Models.

!!! info "GitHub Models Marketplace"
    GitHub Models provides access to various AI models through GitHub's marketplace, including models from Meta, Microsoft, and other providers.

## Configuration

=== "Holmes CLI"

    **Using Environment Variables:**
    ```bash
    export GITHUB_API_KEY="your-github-token"
    holmes ask "what pods are failing?" --model="github/gpt-5.2"
    ```

    **Using Command Line Parameters:**

    You can also pass the API key directly as a command-line parameter:
    ```bash
    holmes ask "what pods are failing?" --model="github/gpt-5.2" --api-key="your-github-token"
    ```

    !!! note "Model Naming"
        Use `github/` prefix followed by the model name, ignoring company prefixes. For example, `openai/gpt-5.2` becomes `github/gpt-5.2`.

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-literal=github-api-key="your-github-token" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: GITHUB_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: github-api-key

    # Configure at least one model using modelList
    modelList:
      gpt-5-2:
        api_key: "{{ env.GITHUB_API_KEY }}"
        model: github/gpt-5.2
        temperature: 0

    # Optional: Set default model (use modelList key name)
    config:
      model: "gpt-5-2"  # This refers to the key name in modelList above
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-literal=github-api-key="your-github-token" \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: GITHUB_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: github-api-key

      # Configure at least one model using modelList
      modelList:
        gpt-5-2:
          api_key: "{{ env.GITHUB_API_KEY }}"
          model: github/gpt-5.2
          temperature: 0

      # Optional: Set default model (use modelList key name)
      config:
        model: "gpt-5-2"  # This refers to the key name in modelList above
    ```

## Available Models

GitHub Models provides access to various models from different providers. Example:

- `github/gpt-5.2`

!!! tip "Model Names"
    When using GitHub Models, omit the company prefix from the model name. For example, `openai/gpt-5.2` becomes `github/gpt-5.2`.

## Features

GitHub Models supports tool/function calling for compatible models.

## Example Usage

```bash
holmes ask "what pods are failing?" --model="github/gpt-5.2"
```

## Additional Resources

HolmesGPT uses the LiteLLM API to support GitHub Models provider. Refer to [LiteLLM GitHub docs](https://docs.litellm.ai/docs/providers/github){:target="_blank"} for more details.

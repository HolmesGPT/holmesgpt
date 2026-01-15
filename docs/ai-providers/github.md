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
    holmes ask "what pods are failing?" --model="github/Phi-4"
    ```

    **Using Command Line Parameters:**

    You can also pass the API key directly as a command-line parameter:
    ```bash
    holmes ask "what pods are failing?" --model="github/Phi-4" --api-key="your-github-token"
    ```

    !!! note "Model Naming"
        Use `github/` prefix followed by the model name, ignoring company prefixes. For example, `meta/Llama-3.2-11B-Vision-Instruct` becomes `github/Llama-3.2-11B-Vision-Instruct`.

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
      phi-4:
        api_key: "{{ env.GITHUB_API_KEY }}"
        model: github/Phi-4
        temperature: 0

      llama-vision:
        api_key: "{{ env.GITHUB_API_KEY }}"
        model: github/Llama-3.2-11B-Vision-Instruct
        temperature: 0

    # Optional: Set default model (use modelList key name)
    config:
      model: "phi-4"  # This refers to the key name in modelList above
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
        phi-4:
          api_key: "{{ env.GITHUB_API_KEY }}"
          model: github/Phi-4
          temperature: 0

        llama-vision:
          api_key: "{{ env.GITHUB_API_KEY }}"
          model: github/Llama-3.2-11B-Vision-Instruct
          temperature: 0

      # Optional: Set default model (use modelList key name)
      config:
        model: "phi-4"  # This refers to the key name in modelList above
    ```

## Available Models

GitHub Models provides access to various models from different providers. Common examples include:

**Meta Llama Models:**

- `github/Llama-3.1-8b-Instant`
- `github/Llama-3.1-70b-Versatile`
- `github/Llama-3.2-11B-Vision-Instruct`

**Microsoft Phi Models:**

- `github/Phi-4`

**Mistral Models:**

- `github/Mixtral-8x7b-32768`

!!! tip "Model Names"
    When using GitHub Models, omit the company prefix from the model name. For example, `meta/Llama-3.2-11B-Vision-Instruct` becomes `github/Llama-3.2-11B-Vision-Instruct`.

## Features

**Tool Calling:**
GitHub Models supports tool/function calling for compatible models, making it suitable for HolmesGPT's investigation capabilities.

**Cost:**
GitHub Models offers competitive pricing through GitHub's marketplace. Check the [GitHub Models documentation](https://github.com/marketplace/models){:target="_blank"} for current pricing.

## Example Usage

```bash
# Using Phi-4 model
holmes ask "analyze pod failures in namespace production" --model="github/Phi-4"

# Using Llama model with vision capabilities
holmes ask "what issues do you see?" --model="github/Llama-3.2-11B-Vision-Instruct"

# Using Mixtral for complex investigations
holmes ask "investigate high memory usage" --model="github/Mixtral-8x7b-32768"
```

## Additional Resources

HolmesGPT uses the LiteLLM API to support GitHub Models provider. Refer to [LiteLLM GitHub docs](https://docs.litellm.ai/docs/providers/github){:target="_blank"} for more details.

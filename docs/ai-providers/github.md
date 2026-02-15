# GitHub Models

Configure HolmesGPT to use [GitHub Models](https://github.com/marketplace/models){:target="_blank"}.

## Setup

Create a [GitHub Personal Access Token](https://github.com/settings/tokens){:target="_blank"} (fine-grained) with the **Models** permission:

![GitHub PAT Models Permission](../assets/github-models-pat-permissions.png)

Browse the full list of available models at [github.com/marketplace/models](https://github.com/marketplace/models){:target="_blank"}.

!!! warning "Verify model availability before configuring"
    Some models are listed in the GitHub Models catalog but are not actually available for your account. Before configuring a model in HolmesGPT, open the model's page in the [GitHub Models playground](https://github.com/marketplace/models){:target="_blank"}, send it any message (e.g. "hello"), and verify you get a response. If the model doesn't respond, it won't work with HolmesGPT either.

## Configuration

=== "Holmes CLI"

    **Using Environment Variables:**
    ```bash
    export GITHUB_API_KEY="your-github-token"
    holmes ask "what pods are failing?" --model="github/gpt-4.1"
    ```

    **Using Command Line Parameters:**

    ```bash
    holmes ask "what pods are failing?" --model="github/gpt-4.1" --api-key="your-github-token"
    ```

    !!! note "Model Naming"
        Use `github/` prefix followed by the model name, dropping the company prefix. For example, `openai/gpt-4.1` in the catalog becomes `github/gpt-4.1`.

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

    modelList:
      gpt-4-1:
        api_key: "{{ env.GITHUB_API_KEY }}"
        model: github/gpt-4.1
        temperature: 0

    config:
      model: "gpt-4-1"
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

      modelList:
        gpt-4o:
          api_key: "{{ env.GITHUB_API_KEY }}"
          model: github/gpt-4o
          temperature: 0

      config:
        model: "gpt-4o"
    ```

## Additional Resources

- [GitHub Models Catalog](https://github.com/marketplace/models){:target="_blank"} - browse all available models
- [LiteLLM GitHub provider docs](https://docs.litellm.ai/docs/providers/github){:target="_blank"}

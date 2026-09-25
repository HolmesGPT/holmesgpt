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

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-github \
      --from-literal=GITHUB_API_KEY="your-github-token" \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-github

    additionalEnvVars:
      - name: MODEL
        value: "gpt-4-1"

    modelList:
      gpt-4-1:
        api_key: "{{ env.GITHUB_API_KEY }}"
        model: github/gpt-4.1
        temperature: 0
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmesgpt robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-github \
      --from-literal=GITHUB_API_KEY="your-github-token" \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-github

      additionalEnvVars:
        - name: MODEL
          value: "gpt-4-1"

      modelList:
        gpt-4-1:
          api_key: "{{ env.GITHUB_API_KEY }}"
          model: github/gpt-4.1
          temperature: 0
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Additional Resources

- [GitHub Models Catalog](https://github.com/marketplace/models){:target="_blank"} - browse all available models
- [LiteLLM GitHub provider docs](https://docs.litellm.ai/docs/providers/github){:target="_blank"}

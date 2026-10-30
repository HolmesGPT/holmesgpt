# Other

HolmesGPT supports all AI providers available through [LiteLLM](https://litellm.vercel.app/docs/providers){:target="_blank"}, including 100+ different LLM providers. This guide shows how to configure any LiteLLM-supported provider using DeepSeek via Novita as an example.

## Example: MiniMax M3.1 Flash and M3

Use `MiniMax-M3.1-Flash` for a new MiniMax configuration, with `MiniMax-M3` as
an alternative. Both use the OpenAI-compatible Chat Completions API in this
example. Set `MINIMAX_API_KEY` to your API key in the Holmes process environment.

For the standalone Holmes Helm Chart, add the following to `values.yaml` and
provide the key through a Kubernetes Secret and `extraEnvVarsSecrets`, as described
in [Using Multiple Providers](using-multiple-providers.md). For the Robusta Helm
Chart, nest `modelList` under `holmes` in `generated_values.yaml`.

```yaml
modelList:
  minimax-m31-flash:
    model: openai/MiniMax-M3.1-Flash
    api_key: "{{ env.MINIMAX_API_KEY }}"
    api_base: "https://api.minimax.io/v1"
    allowed_openai_params: [reasoning_effort]
    custom_args:
      max_context_size: 1000000
  minimax-m3:
    model: openai/MiniMax-M3
    api_key: "{{ env.MINIMAX_API_KEY }}"
    api_base: "https://api.minimax.io/v1"
    custom_args:
      max_context_size: 1000000
```

For the CLI, save the entries **without the `modelList:` wrapper** in
`~/.holmes/model_list.yaml`, then select the Flash entry:

```bash
holmes ask "what pods are failing?" --model minimax-m31-flash
```

With no explicit model selection, Holmes uses the first entry in this model list.
An existing configured model or explicit selection still takes precedence; keep
saved model IDs and custom aliases when updating your configuration.

The example uses the global endpoint, `https://api.minimax.io/v1`. For a China
API account, use `https://api.minimaxi.com/v1` with that account's API key.

| Model | Context window | API input types | Chat Completions thinking |
| --- | ---: | --- | --- |
| MiniMax M3.1 Flash | 1,000,000 | Text, image, video | Adaptive only (enabled by default) |
| MiniMax M3 | 1,000,000 | Text, image, video | Adaptive by default; can be disabled |

Flash supports up to 524,288 output tokens. Holmes reserves its own output budget;
you can set `max_completion_tokens` on a model entry to choose a smaller request
limit. Thinking tokens count toward that limit.

For Flash, omit `thinking` and `reasoning_effort` to use the API defaults. To tune
thinking depth, add `reasoning_effort: low`, `medium`, `high`, `xhigh`, or `max` to
the model entry; the API default is `max`. The example's `allowed_openai_params`
lets the bundled LiteLLM version pass this parameter through for Flash.
Do not set `thinking.type: disabled`,
`reasoning_effort: none`, `reasoning_split: false`, or `service_tier` for Flash.
M3 does not tune thinking depth through `reasoning_effort`.

See the [global MiniMax API documentation](https://platform.minimax.io/docs/api-reference/text-openai-api){:target="_blank"},
[China MiniMax API documentation](https://platform.minimaxi.com/docs/api-reference/text-openai-api){:target="_blank"},
and [current API pricing](https://platform.minimax.io/docs/pricing/overview){:target="_blank"}.

## Example: DeepSeek 3.1 Terminus via Novita

Let's walk through setting up DeepSeek 3.1 Terminus using the Novita AI provider.

### Step 1: Check LiteLLM Documentation

First, visit the [LiteLLM Novita documentation](https://litellm.vercel.app/docs/providers/novita){:target="_blank"} to understand the provider requirements:

- Model naming format: `novita/<model-name>`
- Required environment variable: `NOVITA_API_KEY`

### Step 2: Get Your API Key

Sign up at [Novita AI](https://novita.ai){:target="_blank"} to obtain your API key.

### Step 3: Configure HolmesGPT

!!! warning "Model Not in LiteLLM Registry"
    Since DeepSeek 3.1 Terminus via Novita isn't in LiteLLM's model registry, you'll see warnings about using default token limits (200K input, 4k output). To fix this, set the correct values based on the model's actual capabilities when run via Novita:

    - **Context window**: 98,304 tokens
    - **Max output**: 32,768 tokens

#### Option 1: API Key Kept Out of the Configuration (Recommended)

=== "Holmes CLI"

    **Set the API key and token limits:**

    ```bash
    export NOVITA_API_KEY="your-novita-api-key"

    # Override the default token limits with correct values
    export OVERRIDE_MAX_CONTENT_SIZE=98304  # 98k context window
    export OVERRIDE_MAX_OUTPUT_TOKEN=32768   # 32k max output
    ```

    **Use the model:**

    ```bash
    holmes ask "what pods are failing?" --model="novita/deepseek/deepseek-v3.1-terminus"
    ```

    **Or pass the API key directly:**

    ```bash
    OVERRIDE_MAX_CONTENT_SIZE=98304 OVERRIDE_MAX_OUTPUT_TOKEN=32768 \
    holmes ask "what pods are failing?" \
      --model="novita/deepseek/deepseek-v3.1-terminus" \
      --api-key="your-novita-api-key"
    ```

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-other \
      --from-literal=NOVITA_API_KEY="your-novita-api-key" \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-other

    modelList:
      deepseek-terminus:
        model: novita/deepseek/deepseek-v3.1-terminus
        api_key: "{{ env.NOVITA_API_KEY }}"
        custom_args:
          max_context_size: 98304  # Override context window to 98k tokens
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-other \
      --from-literal=NOVITA_API_KEY="your-novita-api-key" \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-other

      modelList:
        deepseek-terminus:
          model: novita/deepseek/deepseek-v3.1-terminus
          api_key: "{{ env.NOVITA_API_KEY }}"
          custom_args:
            max_context_size: 98304  # Override context window to 98k tokens
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

#### Option 2: API Key in the Configuration (Less Secure)

!!! warning
    This method stores the API key in plain text in your configuration. Use Kubernetes Secrets for production environments.

=== "Holmes Helm Chart"

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    additionalEnvVars:
      - name: NOVITA_API_KEY
        value: "your-novita-api-key"  # API key directly in values file

    modelList:
      deepseek-terminus:
        model: novita/deepseek/deepseek-v3.1-terminus
        api_key: "{{ env.NOVITA_API_KEY }}"
        custom_args:
          max_context_size: 98304  # Override context window to 98k tokens
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      additionalEnvVars:
        - name: NOVITA_API_KEY
          value: "your-novita-api-key"  # API key directly in values file

      modelList:
        deepseek-terminus:
          model: novita/deepseek/deepseek-v3.1-terminus
          api_key: "{{ env.NOVITA_API_KEY }}"
          custom_args:
            max_context_size: 98304  # Override context window to 98k tokens
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## General Model Configuration Parameters

When configuring models in `modelList`, you can use these parameters:

**Core Parameters:**
```yaml
modelList:
  my-model:
    # Required: LiteLLM model identifier
    model: "provider/model-name"

    # Optional: Authentication
    api_key: "{{ env.API_KEY }}"  # Supports environment variables

    # Optional: Custom endpoints
    api_base: "https://custom-endpoint.example.com/v1"  # Custom API URL
    api_version: "2024-01-01"  # API version (e.g., for Azure)
```

**Advanced Parameters:**
```yaml
modelList:
  advanced-model:
    model: "provider/model-name"
    api_key: "{{ env.API_KEY }}"

    # GPT-5 specific
    reasoning_effort: "medium"  # Options: minimal, low, medium, high

    # Thinking mode (experimental, model-specific)
    thinking: true  # Enable thinking mode if supported by the model

    # Override context window size for any model
    custom_args:
      max_context_size: 1000000  # Override the model's context window
```

!!! info "Context Window Override"
    The `custom_args.max_context_size` parameter can be used with **any model** to override its default context window size. This is particularly useful when the model's actual context window is different from what LiteLLM reports.

    This parameter takes precedence over the `OVERRIDE_MAX_CONTENT_SIZE` environment variable, giving you per-model control when using modelList configuration.

    Example: If your model isn't recognized by LiteLLM and defaults to 200k tokens, but it actually supports 1M tokens:
    ```yaml
    modelList:
      my-model:
        model: provider/model-name
        api_key: "{{ env.API_KEY }}"
        custom_args:
          max_context_size: 1000000  # Override to 1M tokens
    ```

!!! note "Parameter Passthrough"
    Any additional parameters not listed here (like `temperature`, `max_tokens`, etc.) are passed directly to the LiteLLM completion API. Refer to your provider's documentation for supported parameters.

## Finding Other Providers

To use any other LiteLLM-supported provider, follow the same pattern:

1. Check the provider's page in [LiteLLM documentation](https://litellm.vercel.app/docs/providers){:target="_blank"}
2. Note the required environment variables and model naming format
3. Configure using the same structure shown above

## Additional Resources

- **LiteLLM Providers:** Full list at [LiteLLM Providers](https://litellm.vercel.app/docs/providers){:target="_blank"}

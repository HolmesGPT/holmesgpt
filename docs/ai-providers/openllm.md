# OpenLLM

Configure HolmesGPT to use [OpenLLM](https://www.openllm.sh){:target="_blank"}, a router for your API keys, OAuth subscription accounts and local models. It runs on your machine and extends to the cloud, so the same accounts work from your laptop, from your other devices and from a cluster or CI job.

OpenLLM speaks several API formats. HolmesGPT uses its OpenAI-compatible chat completions endpoint through LiteLLM, so models are configured with the `openai/` prefix and an `api_base`. The OpenLLM [documentation](https://docs.openllm.sh){:target="_blank"} covers the rest of its API.

## Models

OpenLLM model IDs are `<provider>/<model>`, matching the providers you have connected. It also provides three aliases that route to the best available model for your accounts:

- `ultra`: the most capable model
- `plus`: an everyday model
- `lite`: a fast, low-cost model

The aliases exist only for the providers you have connected, and you can disable them, so list what your setup exposes before you rely on one:

```bash
curl -s http://127.0.0.1:8787/v1/models | jq -r '.data[] | select(.capabilities | index("tools")) | .id'
```

HolmesGPT needs a model that supports tool calling, so keep to models whose `capabilities` include `tools`.

## Local Daemon

Use this when you run the Holmes CLI on a machine where OpenLLM is installed. The daemon listens on `127.0.0.1:8787` and uses the account you signed in with, so HolmesGPT does not need a real API key.

1. Install OpenLLM with `curl -fsSL https://www.openllm.sh/install | bash`, then run `openllm login` if you are not signed in.
2. Check that the daemon is running: `curl -s http://127.0.0.1:8787/status`
3. Create `~/.holmes/model_list.yaml`:

    ```yaml
    openllm-plus:
        api_base: "http://127.0.0.1:8787/v1"
        api_key: "none"  # LiteLLM requires a value; the daemon uses your saved login
        model: openai/plus
    openllm-lite:
        api_base: "http://127.0.0.1:8787/v1"
        api_key: "none"
        model: openai/lite
    ```

4. Ask a question, using the name of the entry:

    ```bash
    holmes ask "what pods are failing?" --model=openllm-plus --no-interactive
    ```

!!! note "Kubernetes"
    In a pod, `127.0.0.1` is the pod itself, so the daemon on your machine is not reachable. Use the cloud setup below for the Holmes Helm chart and the Robusta Helm chart.

## Cloud

Use this in Kubernetes or CI, where nothing is installed. HolmesGPT calls `https://www.openllm.sh/v1` with an OpenLLM API key, which you create on the **Devices & keys** page after signing in at [openllm.sh](https://www.openllm.sh/sign-in){:target="_blank"}.

```yaml-toolset-config
additionalEnvVars:
  # Optional: Set default model (use modelList key name)
  - name: MODEL
    value: "openllm-plus"  # This refers to the key name in modelList below

# Configure at least one model using modelList
modelList:
  openllm-plus:
    api_base: "https://www.openllm.sh/v1"
    api_key: "{{ env.OPENLLM_API_KEY }}"
    model: openai/plus

  openllm-lite:
    api_base: "https://www.openllm.sh/v1"
    api_key: "{{ env.OPENLLM_API_KEY }}"
    model: openai/lite
---
secret:
  - --from-file=OPENLLM_API_KEY=/path/to/openllm-api-key
cli: |
  **1. Create `~/.holmes/model_list.yaml`:**

  ```yaml
  openllm-plus:
      api_base: "https://www.openllm.sh/v1"
      api_key: "{{ env.OPENLLM_API_KEY }}"
      model: openai/plus
  ```

  **2. Export your key and ask a question:**

  ```bash
  export OPENLLM_API_KEY="sk-llm-..."  # your OpenLLM API key
  holmes ask "what pods are failing?" --model=openllm-plus --no-interactive
  ```
```

Models served from your API keys or included with OpenLLM work from the cloud with no device online. Models that use a subscription account run on one of your devices, so they need a device signed in to OpenLLM and online. If none is, those requests fail with HTTP 403 `subscription_requires_daemon` or `local_requires_daemon`.

## Model Limits and Cost

The `ultra`, `plus` and `lite` aliases are not in LiteLLM's model registry, so HolmesGPT logs `Couldn't find model openai/plus in litellm's model list` and falls back to a 200K context window and a 64K output budget. These defaults are conservative for the current OpenLLM models, but they do not match what the model behind the alias accepts. To use the real limits, read `context_window` and `max_output_tokens` for the model from `/v1/models` and set them as described in [Models Missing from LiteLLM](model-limits.md).

Cost tracking is also off for these names. Add `input_cost_per_token` and `output_cost_per_token` to the entry to enable it.

## Models That Reject Temperature

HolmesGPT sends a near-zero `temperature` on every request. Some models behind OpenLLM do not accept one: a `claude_code/*` model returns `native runtime can't honor temperature`. If a request fails with that error, tell HolmesGPT to send no temperature:

```bash
export TEMPERATURE=none
```

In Kubernetes, set the same variable under `additionalEnvVars`.

## Additional Resources

- [OpenLLM documentation](https://docs.openllm.sh){:target="_blank"}
- [Using Multiple Providers](using-multiple-providers.md) to combine OpenLLM with other models in one model list
- [OpenAI-Compatible Models](openai-compatible.md) for the LiteLLM settings behind this page

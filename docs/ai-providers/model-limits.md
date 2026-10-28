# Models Missing from LiteLLM

HolmesGPT looks up each model's context window and maximum output in [LiteLLM's model registry](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json){:target="_blank"}. When the model name isn't in the registry, HolmesGPT falls back to a 200K context window and a 64K output budget, whatever the model really accepts. The Robusta UI then shows a warning next to the model on the agent page and in every model picker, and HolmesGPT logs `Couldn't find model ... in litellm's model list`.

This happens for model names that only exist in your setup, for example:

- LiteLLM Proxy or API gateway aliases (`openai/team-gpt4o`)
- Azure deployment names (`azure/my-gpt4o-prod`)
- Bedrock inference profiles and self-hosted models (`hosted_vllm/qwen-72b-prod`)
- Typos in the model name

Wrong limits break investigations: if the model's real output limit is lower than 64K, the provider rejects every request, and if its real context window is smaller than 200K, long investigations fail with context length errors.

## Set the Limits for the Model

Set both values on the model's `modelList` entry, using the limits of the real model behind the name (for a proxy alias, the upstream model):

- `custom_args.max_context_size`: the model's context window, in tokens.
- `max_tokens`: the maximum output tokens HolmesGPT requests per call.

```yaml-toolset-config
modelList:
  team-gpt4o:
    model: openai/team-gpt4o  # an alias LiteLLM doesn't know
    api_base: http://litellm.internal:4000
    api_key: "{{ env.LITELLM_PROXY_KEY }}"
    max_tokens: 16384           # the real model's max output
    custom_args:
      max_context_size: 128000  # the real model's context window
---
cli: |
  Set the limits with environment variables. They apply to every model Holmes runs:

  ```bash
  export OVERRIDE_MAX_CONTENT_SIZE=128000  # context window
  export OVERRIDE_MAX_OUTPUT_TOKEN=16384   # max output
  holmes ask "what pods are failing?" --model="openai/team-gpt4o"
  ```
```

After Holmes restarts, the warning clears on the next status update.

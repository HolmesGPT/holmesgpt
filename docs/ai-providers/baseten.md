# Baseten

Configure HolmesGPT to use [Baseten Model APIs](https://docs.baseten.co/inference/model-apis/overview){:target="_blank"} for hosted open-weight models such as Kimi K3 and GLM 5.3.

## Configuration

Use LiteLLM's native `baseten/` prefix with the Baseten model slug (`baseten/<organization>/<model>`). Only `BASETEN_API_KEY` is required: LiteLLM fills in the `https://inference.baseten.co/v1` endpoint itself.

!!! warning "Do not use the `openai/` prefix"
    Baseten's endpoint is OpenAI-compatible, so `model: openai/zai-org/GLM-5.3` with `api_base: https://inference.baseten.co/v1` also answers requests. But HolmesGPT then cannot find the model in LiteLLM's registry: the registry keys Baseten models as `baseten/<slug>`, and an `openai/` model is looked up as `openai/<slug>` and `<slug>`. HolmesGPT falls back to a 200K context window, a 64K output budget and zero cost per token, which do not match the model.

=== "Holmes CLI"

    ```bash
    export BASETEN_API_KEY="..."
    holmes ask "what pods are failing?" --model="baseten/zai-org/GLM-5.3"
    ```

=== "Holmes Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic holmes-secrets \
      --from-file=baseten-api-key=/path/to/baseten-api-key \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: BASETEN_API_KEY
        valueFrom:
          secretKeyRef:
            name: holmes-secrets
            key: baseten-api-key

    modelList:
      glm-5-3:
        model: baseten/zai-org/GLM-5.3
      kimi-k3:
        model: baseten/moonshotai/Kimi-K3
        # Kimi K3 is not in LiteLLM's registry yet; see "Models missing from LiteLLM" below.
        input_cost_per_token: 0.000003
        output_cost_per_token: 0.000015
        custom_args:
          max_context_size: 1048576

    config:
      model: "glm-5-3"  # modelList key name
    ```

=== "Robusta Helm Chart"

    **Create Kubernetes Secret:**
    ```bash
    kubectl create secret generic robusta-holmes-secret \
      --from-file=baseten-api-key=/path/to/baseten-api-key \
      -n <namespace>
    ```

    **Configure Helm Values:**
    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: BASETEN_API_KEY
          valueFrom:
            secretKeyRef:
              name: robusta-holmes-secret
              key: baseten-api-key

      modelList:
        glm-5-3:
          model: baseten/zai-org/GLM-5.3
        kimi-k3:
          model: baseten/moonshotai/Kimi-K3
          # Kimi K3 is not in LiteLLM's registry yet; see "Models missing from LiteLLM" below.
          input_cost_per_token: 0.000003
          output_cost_per_token: 0.000015
          custom_args:
            max_context_size: 1048576

      config:
        model: "glm-5-3"  # modelList key name
    ```

## Models missing from LiteLLM

LiteLLM's registry covers only some Baseten models, and most of its Baseten entries carry pricing but no context window. HolmesGPT downloads the registry from GitHub at startup, so it sees entries added after the bundled LiteLLM release; `baseten/zai-org/GLM-5.3` is one of those. When HolmesGPT falls back to the bundled copy (GitHub unreachable and no [`LITELLM_MODEL_COST_MAP_URL`](../reference/environment-variables.md#litellm_model_cost_map_url) mirror, or `LITELLM_LOCAL_MODEL_COST_MAP=true`), GLM-5.3 needs the overrides below as well. When HolmesGPT logs `Couldn't find model ... in litellm's model list` or `has no entry in litellm's cost map`, set the values on the model's `modelList` entry:

- `custom_args.max_context_size`: the model's context window, in tokens.
- `input_cost_per_token` and `output_cost_per_token`: USD per token. Both must be set.

Take the numbers from Baseten's model catalog, which reports `context_length`, `max_completion_tokens` and `pricing` per model:

```bash
curl -s https://inference.baseten.co/v1/models \
  -H "Authorization: Bearer $BASETEN_API_KEY" | jq '.data[] | {id, context_length, max_completion_tokens, pricing}'
```

## Rate limits

Baseten enforces requests-per-minute and tokens-per-minute limits per account tier and returns `429 Too Many Requests` when either is exceeded; an unverified Basic account gets 15 requests and 100,000 tokens per minute. An investigation sends the whole conversation on every step, so a few tool-calling steps can exhaust the token budget of a low tier within a minute. See [Baseten's pricing and limits](https://docs.baseten.co/inference/model-apis/pricing-and-limits){:target="_blank"} for the tiers and how to raise them.

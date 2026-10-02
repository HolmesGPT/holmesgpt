# Robusta AI

Access multiple AI models from different providers through Robusta's unified API, without managing individual API keys.

!!! info "Robusta Feature"
    Robusta AI is available for [Robusta](../installation/ui-installation.md) customers. It provides access to various AI models through a single managed endpoint.

## Overview

Robusta AI simplifies AI model access by:

- **Multi-provider access**: Access a wide variety of models from different providers (OpenAI, Anthropic, and others) through a single interface
- **No API key management**: Use models from multiple providers without managing individual API keys

## Prerequisites

1. **Robusta account**: You must have an active Robusta platform subscription
2. **Kubernetes deployment**: Robusta AI is only available when running HolmesGPT as a server in Kubernetes (not available in CLI mode)
3. **Robusta platform integration**: Your cluster must be connected to the Robusta platform with a valid `robusta_sink` token
4. **Robusta version**: Requires Robusta version 0.22.0 or higher
5. **Robusta UI sink enabled**: The [Robusta UI sink must be configured](https://docs.robusta.dev/master/configuration/sinks/RobustaUI.html) and operational

## Configuration

Robusta AI is automatically enabled when:

1. HolmesGPT is deployed in [Kubernetes via the Robusta Helm chart](https://docs.robusta.dev/master/setup-robusta/installation/index.html)
2. [A valid Robusta sink is configured in the Robusta Helm Chart](https://docs.robusta.dev/master/configuration/sinks/RobustaUI.html)
3. The `ROBUSTA_AI` environment variable is set to `true`

### Quick Setup

The simplest way to enable HolmesGPT with Robusta AI is to add this to your Robusta Helm values:

```yaml
# Add to generated_values.yaml
enableHolmesGPT: true
```

This automatically:

1. Deploys HolmesGPT as a server in Kubernetes
2. Enables Robusta AI integration
3. Sets up the necessary authentication

### Manual Configuration

For more granular control, you can manually configure Robusta AI:

```yaml
# Add to generated_values.yaml
holmes:
  additionalEnvVars:
    - name: ROBUSTA_AI
      value: "true"
```

### Using Existing Robusta Tokens in Secrets

If your Robusta token is already stored in a Kubernetes secret (common in existing Robusta deployments), you can reference it in HolmesGPT configuration:

```yaml
# Add to generated_values.yaml
holmes:
  additionalEnvVars:
    - name: ROBUSTA_TOKEN
      valueFrom:
        secretKeyRef:
          name: robusta-token-secret
          key: token
    - name: ROBUSTA_AI
      value: "true"
```

Common scenarios for existing secrets:

 - **Existing Robusta UI sink**: If you already have a `robusta_sink` configured, the token is typically stored in a secret named `robusta-token` or similar
 - **Multi-environment deployments**: Use the same secret across different namespaces or clusters
 - **GitOps workflows**: Reference existing secrets managed by ArgoCD or Flux

In most cases, no additional configuration is needed. If you have a valid Robusta deployment, HolmesGPT will automatically:

1. Authenticate with the Robusta platform
2. Fetch available models for your account
3. Make them available for selection

### Disabling Robusta AI

To explicitly disable Robusta AI (for example, if you prefer using your own API keys):

```yaml
# Add to generated_values.yaml
holmes:
  additionalEnvVars:
    - name: ROBUSTA_AI
      value: "false"
```

### Selecting a Region

The Robusta platform is hosted in multiple regions. HolmesGPT defaults to the US endpoint. If your Robusta account lives in the EU or AP region, set `ROBUSTA_API_ENDPOINT` to the matching API URL — pick your region below:

```robusta-region {lang=yaml}
# Add to generated_values.yaml
holmes:
  additionalEnvVars:
    - name: ROBUSTA_AI
      value: "true"
    - name: ROBUSTA_API_ENDPOINT
      value: "https://api.robusta.dev"
```

The endpoint must match the region your cluster is connected to in the Robusta platform — using the wrong endpoint will cause authentication and model-discovery failures.

## How It Works

1. **Authentication**: HolmesGPT reads your Robusta token from the cluster configuration

2. **Session creation**: A session token is created with the Robusta platform

3. **Model discovery**: Available models are fetched from `${ROBUSTA_API_ENDPOINT}/api/llm/models/v3` - pick your region:

    ```robusta-region
    https://api.robusta.dev/api/llm/models/v3
    ```

    A self-hosted platform that does not serve this endpoint yet answers 404; the fetch fails and HolmesGPT falls back to its single legacy Robusta model, without the account's model catalog, until the platform is upgraded.

4. **Proxy access**: Models are accessed through Robusta's proxy endpoint at `${ROBUSTA_API_ENDPOINT}/llm/{model_name}` - pick your region:

    ```robusta-region
    https://api.robusta.dev/llm/{model_name}
    ```

5. **Automatic refresh**: Authentication tokens are automatically refreshed when they expire

## Account-level opt-out

An account can turn Robusta-hosted models off for everyone in it, from **Settings > LLM Models** in the Robusta platform. This is an account setting, not a cluster one: it applies to every cluster connected to the account and cannot be overridden from a cluster's own configuration.

With it set:

- Model discovery returns no Robusta-hosted models, so HolmesGPT loads only the models configured on the cluster itself (`MODEL`, `MODEL_LIST_FILE_LOCATION`, or the model list) - `ROBUSTA_AI: "true"` does not put it back.
- A cluster with no models of its own has nothing to run on, and each request fails with an error naming that: no models are configured and Robusta-hosted models are disabled for the account.
- A call the platform refuses on a Robusta-hosted model fails with the platform's own message. `/api/chat` answers with the platform's status (401 or 403) when `stream` is false; a streamed chat has already answered 200, so the refusal arrives as an `error` event whose `error_code` is 5206 for a 401 and 5207 for a 403.
- Turning the setting back off is picked up by the periodic model refresh - running agents do not need a restart.
- An agent that could not reach the platform when it started loads its legacy Robusta model until the first refresh that does reach it. In that window the platform refuses each call on that model, so no data leaves the account; the refresh then drops the model.
- Agents older than the release that added this keep their previous behaviour: they still load Robusta-hosted models, and the platform refuses each call they make on one.

## Available Models

The specific models available depend on your Robusta subscription plan. Typically includes:

- OpenAI models (GPT-4o, GPT-4.1, GPT-5, etc.)
- Anthropic models (Claude 4.0 Sonnet, etc.)

## Usage

When Robusta AI is enabled, models appear in the model selector dropdown in the Robusta UI. Users can select any available model for their investigations.

![Model Selection with Robusta AI](../assets/robusta-ai-model-selection-ui.png)

## Troubleshooting

### Models not appearing

Check that:

1. Your Robusta token is valid and not expired
2. HolmesGPT can reach the Robusta API endpoint for your region (`api.robusta.dev`, `api.eu.robusta.dev`, or `api.ap.robusta.dev`)
3. `ROBUSTA_API_ENDPOINT` matches the region your Robusta account is in (see [Selecting a Region](#selecting-a-region))
4. `ROBUSTA_AI` is set to `true`
5. Check logs for authentication errors

## Environment Variables

| Variable | Description | Default |
|----------|------------|---------|
| `ROBUSTA_AI` | Enable/disable Robusta AI | Auto-detected |
| `ROBUSTA_API_ENDPOINT` | Robusta API endpoint. Set per region (`https://api.robusta.dev`, `https://api.eu.robusta.dev`, `https://api.ap.robusta.dev`) or to your on-premise URL. See [Selecting a Region](#selecting-a-region). | `https://api.robusta.dev` |

## See Also

- [Using Multiple Providers](using-multiple-providers.md) - Configure multiple AI providers
- [Kubernetes Installation](../installation/kubernetes-installation.md) - Deploy HolmesGPT in Kubernetes
- [Robusta Platform Documentation](https://docs.robusta.dev) - Learn more about Robusta platform integration

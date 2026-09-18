# CLI vs Server Toolset Behavior

The CLI and server handle toolset enablement differently. Understanding this difference helps you move smoothly from local development to production deployment.

## How It Works

**CLI (opt-out model):** All toolsets whose prerequisites are met are automatically enabled. You can disable specific toolsets in your config. This makes the CLI useful immediately with zero configuration.

**Server (opt-in model):** All toolsets are disabled by default. You must explicitly enable each toolset in `config.yaml`. This ensures only intended toolsets are active in production.

| Aspect | CLI (`holmes ask`) | Server (API/Kubernetes) |
|--------|-------------------|------------------------|
| Default state | Enabled if prerequisites pass | Disabled |
| Configuration | Optional | Required |
| Toolset tags | `core`, `cli` | `core`, `cluster` |

## The Problem

You develop locally with the CLI — everything works. You deploy to the server, and toolsets that were auto-enabled on the CLI are now disabled because the server requires explicit configuration.

## Solution: Generate Helm Values from CLI

The `generate-config` command inspects which toolsets are currently enabled in your CLI environment, detects your configured AI model, and generates Helm values ready for deployment. You must specify the target chart format:

=== "HolmesGPT Helm Chart"

    ```bash
    # Print to stdout
    holmes toolset generate-config holmes-helm

    # Write to a file
    holmes toolset generate-config holmes-helm -o values-generated.yaml

    # Include disabled toolsets as comments (for reference)
    holmes toolset generate-config holmes-helm --include-disabled
    ```

    **Example output:**

    ```yaml
    additionalEnvVars:
    - name: OPENAI_API_KEY
      value: <your-openai-api-key>
    modelList:
      gpt-4.1:
        model: gpt-4.1
        temperature: 0
        api_key: '{{ env.OPENAI_API_KEY }}'
    toolsets:
      kubernetes/core:
        enabled: true
      kubernetes/logs:
        enabled: true
      prometheus/metrics:
        enabled: true
    ```

=== "Robusta Helm Chart"

    ```bash
    # Print to stdout
    holmes toolset generate-config robusta-helm

    # Write to a file
    holmes toolset generate-config robusta-helm -o values-generated.yaml

    # Include disabled toolsets as comments (for reference)
    holmes toolset generate-config robusta-helm --include-disabled
    ```

    **Example output:**

    ```yaml
    holmes:
      additionalEnvVars:
      - name: OPENAI_API_KEY
        value: <your-openai-api-key>
      modelList:
        gpt-4.1:
          model: gpt-4.1
          temperature: 0
          api_key: '{{ env.OPENAI_API_KEY }}'
      toolsets:
        kubernetes/core:
          enabled: true
        kubernetes/logs:
          enabled: true
        prometheus/metrics:
          enabled: true
    ```

The command auto-detects your AI provider from the configured model and generates the appropriate `additionalEnvVars` and `modelList` entries. Replace the placeholder API key value with your actual key, or switch to a `secretKeyRef`.

Merge the output into your Helm values and run `helm upgrade`.

## Manual Configuration

You can also configure toolsets manually. Enable a toolset by adding it to the `toolsets` section of your config:

```yaml
toolsets:
  kubernetes/core:
    enabled: true
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: http://prometheus-server.monitoring:9090
```

To explicitly disable a toolset that would otherwise be enabled:

```yaml
toolsets:
  bash:
    enabled: false
```

## Verifying Your Setup

After configuring your server, verify the toolsets are loaded:

```bash
# CLI: list enabled toolsets
holmes toolset list

# CLI: force refresh and list
holmes toolset refresh
```

For the server, check the startup logs — enabled toolsets are logged during initialization.

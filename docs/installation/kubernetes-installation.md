# Install Helm Chart

Deploy HolmesGPT as a service in your Kubernetes cluster with an HTTP API.

!!! warning "When to use the Helm chart?"

    Most users should use the [CLI](cli-installation.md) or [UI/TUI](ui-installation.md) instead. Using the Helm chart is only recommended if you're building a custom integration over an HTTP API.

## Prerequisites

- Kubernetes cluster
- Helm
- kubectl configured to access your cluster
- Supported [AI Provider](../ai-providers/index.md) API key.

!!! info "RBAC Permissions"
    The Helm chart automatically creates a ServiceAccount with ClusterRole permissions required for HolmesGPT to analyze your cluster. For details on required permissions, see [Kubernetes Permissions](../reference/kubernetes-permissions.md).

## Installation

1. **Add the Helm repository:**
   ```bash
   helm repo add robusta https://robusta-charts.storage.googleapis.com
   helm repo update
   ```

2. **Create `values.yaml` file:**

    Create a `values.yaml` file to configure HolmesGPT with your models using the `modelList` approach:

    === "OpenAI"
        ```yaml
        # values.yaml
        additionalEnvVars:
        - name: OPENAI_API_KEY
          value: "your-openai-api-key"
        # Or load from secret:
        # - name: OPENAI_API_KEY
        #   valueFrom:
        #     secretKeyRef:
        #       name: holmes-secrets
        #       key: openai-api-key

        modelList:
          gpt-4.1:
            api_key: "{{ env.OPENAI_API_KEY }}"
            model: openai/gpt-4.1
            temperature: 0
          gpt-5:
            api_key: "{{ env.OPENAI_API_KEY }}"
            model: openai/gpt-5
        ```

    === "Anthropic"
        ```yaml
        # values.yaml
        additionalEnvVars:
        - name: ANTHROPIC_API_KEY
          value: "your-anthropic-api-key"
        # Or load from secret:
        # - name: ANTHROPIC_API_KEY
        #   valueFrom:
        #     secretKeyRef:
        #       name: holmes-secrets
        #       key: anthropic-api-key

        modelList:
          claude-sonnet:
            api_key: "{{ env.ANTHROPIC_API_KEY }}"
            model: anthropic/claude-sonnet-4-20250514
            temperature: 0
        ```

    === "Azure OpenAI"
        ```yaml
        # values.yaml
        additionalEnvVars:
        - name: AZURE_API_KEY
          value: "your-azure-api-key"
        - name: AZURE_API_BASE
          value: "https://your-resource.openai.azure.com/"
        - name: AZURE_API_VERSION
          value: "2024-02-15-preview"
        # Or load from secret:
        # - name: AZURE_API_KEY
        #   valueFrom:
        #     secretKeyRef:
        #       name: holmes-secrets
        #       key: azure-api-key
        # - name: AZURE_API_BASE
        #   valueFrom:
        #     secretKeyRef:
        #       name: holmes-secrets
        #       key: azure-api-base

        modelList:
          azure-gpt4:
            api_key: "{{ env.AZURE_API_KEY }}"
            model: azure/your-deployment-name
            api_base: "{{ env.AZURE_API_BASE }}"
            api_version: "{{ env.AZURE_API_VERSION }}"
            temperature: 0
        ```

    === "Multiple Providers"
        ```yaml
        # values.yaml
        additionalEnvVars:
        - name: OPENAI_API_KEY
          value: "your-openai-api-key"
        - name: ANTHROPIC_API_KEY
          value: "your-anthropic-api-key"
        # Or load from secrets (recommended)

        modelList:
          gpt-4.1:
            api_key: "{{ env.OPENAI_API_KEY }}"
            model: openai/gpt-4.1
            temperature: 0
          claude-sonnet:
            api_key: "{{ env.ANTHROPIC_API_KEY }}"
            model: anthropic/claude-sonnet-4-20250514
            temperature: 0
          gpt-5:
            api_key: "{{ env.OPENAI_API_KEY }}"
            model: openai/gpt-5
        ```

        > **Configuration Guide:** Each AI provider requires different environment variables. See the [AI Providers documentation](../ai-providers/index.md) for the specific environment variables needed for your chosen provider, then add them to the `additionalEnvVars` section as shown above. For a complete list of all environment variables, see the [Environment Variables Reference](../reference/environment-variables.md). For advanced multiple provider setup, see [Using Multiple Providers](../ai-providers/using-multiple-providers.md).

3. **Install HolmesGPT:**
   ```bash
   helm install holmesgpt robusta/holmes -f values.yaml
   ```

## Optional: OpenTelemetry instrumentation

You can enable optional OpenTelemetry (OTLP) instrumentation to send traces to an OpenTelemetry Collector or compatible backend (e.g. Jaeger, Tempo, Grafana Cloud). When enabled, the chart runs the server under `opentelemetry-instrument`, which instruments the FastAPI app, HTTP clients, and MCP; the application also enables LiteLLM’s OpenTelemetry callback when an OTLP endpoint is set.

To enable:

1. Set **`otlp.enabled`** to `true` and set **`otlp.endpoint`** to your collector URL (e.g. `http://otel-collector.observability.svc:4317`).
2. Ensure your Holmes image includes the OpenTelemetry dependencies (the default image does).

Add (or merge) the following into your `values.yaml`:

```yaml
# values.yaml (merge with your existing modelList, additionalEnvVars, etc.)
otlp:
  enabled: true
  service: holmesgpt          # OTEL_SERVICE_NAME
  endpoint: "http://otel-collector.observability.svc:4317"
  metrics: "none"             # optional; set to "otlp" to export metrics
  logs: "none"                # optional; set to "otlp" to export logs
  # protocol: "grpc"          # optional; default is grpc, use "http/protobuf" for HTTP
  # insecure: "true"          # optional; set for plaintext (e.g. same-cluster collector)
  # headers: "key1=value1,key2=value2"   # optional; auth or tenant headers
  # excludedUrls: optional; defaults to "/healthz,/readyz". Set to override or "" to trace all paths.
```

Then install or upgrade as usual:

```bash
helm upgrade --install holmesgpt robusta/holmes -f values.yaml
```

If **`otlp.enabled`** is `false` or **`otlp.endpoint`** is empty, the server runs without the OpenTelemetry wrapper and no OTLP env vars are set.

## Usage

After installation, test the service with a simple API call:

```bash
# Port forward to access the service locally
# Note: Service name is {release-name}-holmes
kubectl port-forward svc/holmesgpt-holmes 8080:80

# If you used a different release name or namespace:
# kubectl port-forward svc/{your-release-name}-holmes 8080:80 -n {your-namespace}

# Test with a basic question using a model name from your modelList
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"ask": "list pods in namespace default?", "model": "gpt-4.1"}'

# Using a different model from your modelList
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"ask": "list pods in namespace default?", "model": "claude-sonnet"}'
```

> **Note**: Responses may take some time when HolmesGPT needs to gather large amounts of data to answer your question. Streaming APIs are coming soon to stream results.

For complete API documentation, see the [HTTP API Reference](../reference/http-api.md).


## Upgrading

```bash
helm repo update
helm upgrade holmesgpt robusta/holmes -f values.yaml
```

## Uninstalling

```bash
helm uninstall holmesgpt
```

## Next Steps

- **[Recommended Setup](../data-sources/recommended-setup.md)** - Connect metrics, logs, and cloud providers to unlock deeper investigations
- **[All Data Sources](../data-sources/index.md)** - Browse the full list of 38+ built-in integrations

## Need Help?

- **[Join our Slack](https://cloud-native.slack.com/archives/C0A1SPQM5PZ){:target="_blank"}** - Get help from the community
- **[Request features on GitHub](https://github.com/HolmesGPT/holmesgpt/issues){:target="_blank"}** - Suggest improvements or report bugs
- **[Troubleshooting guide](../reference/troubleshooting.md)** - Common issues and solutions

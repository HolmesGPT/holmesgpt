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

    === "Azure AI Foundry"
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


## Serving the API over HTTPS (TLS)

By default the pod serves the API over plain HTTP. HolmesGPT can serve **HTTPS directly from the pod** (in-app TLS) — no ingress, gateway, or sidecar proxy is required. This is disabled by default and is fully backward compatible: existing installs are unchanged until you enable it.

When enabled, the chart mounts your TLS secret into the pod, points the server at it via the `HOLMES_SSL_*` environment variables, and switches the Service `appProtocol` to HTTPS automatically. The liveness/readiness probes also switch to HTTPS — except when `caCertsSecretKey` is set for mTLS, in which case the chart falls back to `tcpSocket` probes because the kubelet cannot present a client certificate.

**Provide your own certificate.** The chart does not generate a self-signed certificate — supply a Kubernetes TLS secret containing `tls.crt` and `tls.key`. You can create it with [cert-manager](https://cert-manager.io/) or manually:

```bash
kubectl create secret tls holmes-tls \
  --cert=/path/to/tls.crt \
  --key=/path/to/tls.key \
  -n {your-namespace}
```

**Enable TLS in `values.yaml`:**

```yaml
tls:
  enabled: true
  secretName: holmes-tls               # required: secret with tls.crt and tls.key
  # keyfilePasswordSecretKey: ""       # optional: key in the secret with the encrypted-key password
  # caCertsSecretKey: ca.crt           # optional: a key in the same secret; enables mTLS
```

To require client certificates (**mutual TLS**), add a CA bundle under an extra key in the same secret (e.g. `ca.crt`) and set `caCertsSecretKey` to that key name. Clients without a certificate signed by that CA are rejected.

> **Note**: Enabling `tls` without `secretName` fails the Helm render with a clear error, so a misconfiguration can't silently downgrade to HTTP. The server reads the certificate once at startup and does not hot-reload it — after rotating the certificate/secret, restart the pod (`kubectl rollout restart deployment/{release-name}-holmes`).

After enabling, port-forward and call the service over `https` (use `-k` for a self-signed/private-CA certificate):

```bash
kubectl port-forward svc/holmesgpt-holmes 8080:80
curl -k https://localhost:8080/api/chat -H "Content-Type: application/json" \
  -d '{"ask": "list pods in namespace default?", "model": "anthropic/claude-sonnet-4-5-20250929"}'
```

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

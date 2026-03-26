# Install HTTP Server (Docker)

Run the HolmesGPT HTTP API server locally using Docker Compose — no Kubernetes required.

To deploy the HTTP server on Kubernetes, see the [Helm Chart](kubernetes-installation.md) instead.

## Prerequisites

- Docker and Docker Compose
- Supported [AI Provider](../ai-providers/index.md) API key

## Installation

1. **Clone the repository** (or just download `docker-compose.yaml`):
   ```bash
   git clone https://github.com/HolmesGPT/holmesgpt.git
   cd holmesgpt
   ```

2. **Set your API key:**
   ```bash
   export OPENAI_API_KEY="your-api-key"
   ```

3. **Start the server:**
   ```bash
   docker compose up
   ```

4. **Verify it's running:**
   ```bash
   curl http://localhost:5050/healthz
   ```

The API is available at `http://localhost:5050`.

## Configuration

Edit `docker-compose.yaml` to configure your setup:

- **LLM provider**: Uncomment the environment variables for your provider (Anthropic, Gemini, Azure, AWS Bedrock)
- **Kubernetes access**: The compose file mounts `~/.kube/config` so Holmes can query your cluster
- **Cloud credentials**: AWS and GCloud credential directories are mounted read-only
- **Holmes config**: `~/.holmes` is mounted for custom configuration

!!! info "Kubeconfig with localhost clusters"

    If your kubeconfig points to `127.0.0.1` or `localhost` (common with Docker Desktop, minikube, kind), the container automatically rewrites the Kubernetes API server address to `host.docker.internal` on startup so the cluster is reachable. Remote clusters (EKS, GKE, AKS, etc.) are not affected.

## API Reference

See the [HTTP API Reference](../reference/http-api.md) for full documentation on available endpoints, request/response formats, and usage examples.

## Next Steps

### Customize Holmes Settings

The Docker Compose file mounts `~/.holmes` into the container. Create `~/.holmes/config.yaml` to customize Holmes behavior:

```yaml
# Change the LLM model
model: "anthropic/claude-sonnet-4-5-20250929"

# Limit the number of tool-calling steps per investigation
max_steps: 100

# Enable a builtin integration (e.g. Confluence)
toolsets:
  confluence:
    enabled: true
    config:
      api_url: "https://yourcompany.atlassian.net"
      user: "your-email@example.com"
      api_key: "your-api-token"
```

For configuring additional data sources, see [Toolset Configuration](../data-sources/builtin-toolsets/index.md).

After editing, restart the container to apply changes:

```bash
docker compose restart
```

For the full list of environment variables and options, see the [Environment Variables](../reference/environment-variables.md) reference.

### Learn More

- **[HTTP API Reference](../reference/http-api.md)** — Full API documentation
- **[Helm Chart](kubernetes-installation.md)** — Deploy the HTTP server on Kubernetes
- **[CLI Installation](cli-installation.md)** — Run HolmesGPT as a command-line tool instead

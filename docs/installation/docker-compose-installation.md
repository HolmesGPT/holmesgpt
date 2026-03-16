# Install HTTP Server (Docker Compose)

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

## API Endpoints

The server exposes these main endpoints:

- `POST /api/chat` — Chat with tool calling
- `POST /api/investigate` — Investigate an issue
- `GET /api/model` — List available models
- `GET /healthz` — Health check
- `GET /readyz` — Readiness check

## Next Steps

- **[Helm Chart](kubernetes-installation.md)** — Deploy the HTTP server on Kubernetes
- **[CLI Installation](cli-installation.md)** — Run HolmesGPT as a command-line tool instead

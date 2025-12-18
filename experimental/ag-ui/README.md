# AG-UI - Experimental HolmesGPT Server

AG-UI is an experimental extension to HolmesGPT that demonstrates [AG-UI](https://docs.ag-ui.com/introduction) capabilities (page context and state sharing, front-end tools execution, etc) through a specialized `/api/agui/chat` endpoint and a web-based "ExampleOps✨" observability demo interface. The AG-UI compatible `server.py` is adapted from the [existing server.py implementation](../../server.py)

_⚠️ **Disclaimer**: AG-UI is experimental within HolmesGPT. APIs and interfaces may change as the project evolves. The demonstration server and frontend is not intended for production use._

![ExampleOps demo video](https://github.com/kylehounslow/holmesgpt/blob/docs/experimental/ag-ui/docs/holmesgpt-agui-demo-1.gif?raw=true)

## Known Limitations:

1. Front-end tool auto-discovery and integration with LLM is not yet supported. Front-end tool discovery and execution is handled statically in the back-end.
1. Front-end tool results are not yet supported. No-op 200 response is returned to front-end.
1. Tested with Anthropic Claude Sonnet 3.7 and 4.0 on AWS Bedrock only.
1. `TodoWrite` tool rendering is not properly supported.

## 🛠️ Quick Start

### **0. Prerequisites**

- **HolmesGPT** with experimental AG-UI server.
- **Data Sources**: Prometheus (`:9090`) and/or OpenSearch (`:9200`)
  - Recommended to run [opentelemetry-demo](https://github.com/open-telemetry/opentelemetry-demo) via docker-compose.
- **Node.js** 20+ (for frontend demonstration)

### **1. Set up datasources**

A simple way to generate synthetic Prometheus metrics, OpenSearch logs, traces and more, is to use: [opentelemetry-demo](https://github.com/open-telemetry/opentelemetry-demo)

```bash
git clone git@github.com:open-telemetry/opentelemetry-demo.git
cd opentelemetry-demo
docker compose up -d
```

### **2. Start HolmesGPT AG-UI Server on port 5050**

Assumes there is a local Prometheus server (containing metrics) at `localhost:9090` (e.g. [opentelemetry-demo](https://github.com/open-telemetry/opentelemetry-demo)) and using AWS Bedrock as LLM. See [HolmesGPT documentation](https://holmesgpt.dev/) for alternate configurations.

```bash
# Start HolmesGPT AG-UI compatible server
cd holmesgpt
export HOLMES_PORT=5050
export PROMETHEUS_URL=http://localhost:9090
export AWS_PROFILE=default
export AWS_REGION=us-east-1
export MODEL=bedrock/us.anthropic.claude-sonnet-4-20250514-v1:0
poetry run python experimental/ag-ui/server-agui.py
```

### **3. Run "ExampleOps✨" Demo Frontend**

ExampleOps✨ is a lightweight observability frontend that demonstrates AG-UI capabilities (page context, state sharing, front-end tools execution, etc).
Create .env file at `experimental/ag-ui/front-end/.env`. See example below and replace Prometheus/OpenSearch urls as needed:

```env
# AG-UI Agent Configuration
HOLMES_PORT=5050
AGENT_URL=http://localhost:${HOLMES_PORT}
# Prometheus Configuration
REACT_APP_PROMETHEUS_URL=http://localhost:9090

# OpenSearch Configuration
REACT_APP_OPENSEARCH_URL=http://localhost:9200
REACT_APP_OPENSEARCH_USER=user
REACT_APP_OPENSEARCH_PASSWORD=pass
```

```bash
cd experimental/ag-ui/front-end
npm install && npm start
```

## Running HolmesGPT in OpenSearch Dashboards
OpenSearch Dashboards (>=3.3) supports AG-UI compatible agents for its AI chat. For an example HolmesGPT configuration,
see [gist](https://gist.github.com/kylehounslow/07290ee15768a5b15a924831f7759217).

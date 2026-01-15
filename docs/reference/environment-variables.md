# Environment Variables

This page documents all environment variables that can be used to configure HolmesGPT behavior.

## AI Provider Configuration

### OpenAI
- `OPENAI_API_KEY` - API key for OpenAI models

### Anthropic
- `ANTHROPIC_API_KEY` - API key for Anthropic Claude models

### Google
- `GEMINI_API_KEY` - API key for Google Gemini models
- `GOOGLE_API_KEY` - Alternative API key for Google services

### Azure OpenAI
- `AZURE_API_KEY` - API key for Azure OpenAI service
- `AZURE_API_BASE` - Base URL for Azure OpenAI endpoint
- `AZURE_API_VERSION` - API version to use (e.g., "2024-02-15-preview")

### AWS Bedrock
- `AWS_ACCESS_KEY_ID` - AWS access key ID
- `AWS_SECRET_ACCESS_KEY` - AWS secret access key
- `AWS_DEFAULT_REGION` - AWS region for Bedrock

### Google Vertex AI
- `VERTEXAI_PROJECT` - Google Cloud project ID
- `VERTEXAI_LOCATION` - Vertex AI location (e.g., "us-central1")
- `GOOGLE_APPLICATION_CREDENTIALS` - Path to service account key JSON file

## LLM Tool Calling Configuration

### LLMS_WITH_STRICT_TOOL_CALLS
**Default:** `"azure/gpt-4.1, openai/*"`

Comma-separated list of model patterns that support strict tool calling. When a model matches one of these patterns, HolmesGPT will:
- Enable the `strict` flag for function definitions
- Set `additionalProperties: false` in tool parameter schemas
- Enforce stricter schema validation for tool calls

This improves reliability of tool calling for supported models by ensuring the LLM adheres more strictly to the defined tool schemas.

**Example:**
```bash
export LLMS_WITH_STRICT_TOOL_CALLS="azure/gpt-4.1,openai/*,anthropic/claude-sonnet-4*"
```

### TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS
**Default:** `false`

When set to `true`, removes the `parameters` object from tool schemas when a tool has no parameters. This is specifically required for Google Gemini models which don't expect a parameters object for parameterless functions.

**Example:**
```bash
export TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS=true
```

**Note:** This setting is typically only needed when using Gemini models. Other providers handle empty parameter objects correctly.

## CORS Configuration

Cross-Origin Resource Sharing (CORS) can be enabled to allow the HolmesGPT API to be accessed from web applications running on different origins.

### CORS_ENABLED
**Default:** `false`

When set to `true`, enables CORS middleware on the FastAPI server.

**Example:**
```bash
export CORS_ENABLED=true
```

### CORS_ALLOW_ORIGINS
**Default:** `"*"`

Comma-separated list of origins that are allowed to make cross-origin requests. Use specific origins in production for security.

**Example:**
```bash
# Allow specific origins
export CORS_ALLOW_ORIGINS="https://myapp.example.com,https://admin.example.com"

# Allow all origins (not recommended for production)
export CORS_ALLOW_ORIGINS="*"
```

### CORS_ALLOW_CREDENTIALS
**Default:** `true`

Whether to allow credentials (cookies, authorization headers) in cross-origin requests.

**Example:**
```bash
export CORS_ALLOW_CREDENTIALS=true
```

### CORS_ALLOW_METHODS
**Default:** `"*"`

Comma-separated list of HTTP methods allowed for cross-origin requests.

**Example:**
```bash
export CORS_ALLOW_METHODS="GET,POST,PUT,DELETE"
```

### CORS_ALLOW_HEADERS
**Default:** `"*"`

Comma-separated list of HTTP headers allowed in cross-origin requests.

**Example:**
```bash
export CORS_ALLOW_HEADERS="Content-Type,Authorization,X-Custom-Header"
```

!!! warning "Security Note"
    In production environments, always specify explicit origins rather than using `"*"`. 

!!! note "Automatic Credential Adjustment"
    If `CORS_ALLOW_ORIGINS="*"` (wildcard) is used with `CORS_ALLOW_CREDENTIALS=true`, the server will automatically set `allow_credentials` to `false` and log a warning. This is because browsers reject this combination for security reasons.

## HolmesGPT Configuration

### HOLMES_CONFIG_PATH
Path to a custom HolmesGPT configuration file. If not set, defaults to `~/.holmes/config.yaml`.

**Example:**
```bash
export HOLMES_CONFIG_PATH="/path/to/custom/config.yaml"
```

### HOLMES_LOG_LEVEL
Controls the logging verbosity of HolmesGPT.

**Values:** `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
**Default:** `INFO`

**Example:**
```bash
export HOLMES_LOG_LEVEL="DEBUG"
```

### HOLMES_CACHE_DIR
Directory for caching HolmesGPT data and temporary files.

## Data Source Configuration

### Prometheus
- `PROMETHEUS_URL` - URL of the Prometheus server

### Confluence
- `CONFLUENCE_BASE_URL` - Base URL of Confluence instance
- `CONFLUENCE_EMAIL` - Email for Confluence authentication
- `CONFLUENCE_API_KEY` - API key for Confluence

### GitHub
- `GITHUB_TOKEN` - Personal access token for GitHub API

### Datadog
- `DATADOG_APP_KEY` - Datadog application key
- `DATADOG_API_KEY` - Datadog API key

### AWS
- `AWS_ACCESS_KEY_ID` - AWS access key (also used for AWS toolset)
- `AWS_SECRET_ACCESS_KEY` - AWS secret key (also used for AWS toolset)
- `AWS_DEFAULT_REGION` - Default AWS region

### MongoDB Atlas
- `MONGODB_ATLAS_PUBLIC_KEY` - Public key for MongoDB Atlas API
- `MONGODB_ATLAS_PRIVATE_KEY` - Private key for MongoDB Atlas API

### Slab
- `SLAB_API_KEY` - API key for Slab integration

## Testing and Development

### RUN_LIVE
When set to `true`, enables live execution of commands in tests instead of using mocked responses. Strongly recommended for accurate test results.

**Example:**
```bash
export RUN_LIVE=true
```

### MODEL
Override the default LLM model for testing.

**Example:**
```bash
export MODEL="anthropic/claude-sonnet-4-20250514"
```

### CLASSIFIER_MODEL
Model to use for scoring test answers (defaults to MODEL if not set). Required when using Anthropic models as the primary model since Anthropic models cannot be used as classifiers.

**Example:**
```bash
export CLASSIFIER_MODEL="gpt-4.1"
```

### ITERATIONS
Number of times to run each test for reliability testing.

**Example:**
```bash
export ITERATIONS=10
```

### BRAINTRUST_API_KEY
API key for Braintrust integration to track test results.

### BRAINTRUST_ORG
Braintrust organization name (default: "robustadev").

### EXPERIMENT_ID
Custom experiment name for tracking test runs in Braintrust.

### ASK_HOLMES_TEST_TYPE
Controls message building flow in ask_holmes tests:
- `cli` (default) - Uses CLI-style message building
- `server` - Uses server-style message building with ChatRequest

## Usage Examples

### Basic Setup
```bash
# Set up OpenAI
export OPENAI_API_KEY="sk-..."
export HOLMES_LOG_LEVEL="INFO"

# Run HolmesGPT
holmes ask "what pods are failing?"
```

### Gemini Configuration
```bash
# Configure for Gemini models
export GEMINI_API_KEY="your-key"
export TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS=true

holmes ask "analyze cluster health" --model="gemini/gemini-1.5-pro"
```

### Testing with Strict Tool Calling
```bash
# Enable strict tool calling for additional models
export LLMS_WITH_STRICT_TOOL_CALLS="azure/gpt-4.1,openai/*,custom/model-*"
export RUN_LIVE=true
export MODEL="custom/model-v2"

poetry run pytest tests/llm/ -n 6
```

## See Also

- [AI Providers](../ai-providers/index.md) - Detailed configuration for each AI provider
- [CLI Installation](../installation/cli-installation.md) - Setting up the CLI with environment variables
- [Helm Configuration](./helm-configuration.md) - Kubernetes deployment configuration

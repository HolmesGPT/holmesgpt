# Environment Variables

This page documents all environment variables that can be used to configure HolmesGPT behavior.

## AI Provider Configuration

### OpenAI
- `OPENAI_API_KEY` - API key for OpenAI models
- `OPENAI_API_BASE` - Custom base URL for OpenAI-compatible APIs (e.g., LiteLLM proxy, local inference servers). See [OpenAI-Compatible](../ai-providers/openai-compatible.md) for details.

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

### HOLMES_DISABLE_STRICT_TOOL_CALLS
**Default:** `false`

When set to `true`, disables strict tool calling for all models. By default, strict mode is enabled universally — HolmesGPT sets `strict: true` and `additionalProperties: false` on all tool schemas. This prevents LLMs from hallucinating parameter names or sending malformed arguments.

Tools with dynamic-key parameters (`additionalProperties` with a schema, e.g., filter maps) are automatically excluded from strict mode on a per-tool basis, since both OpenAI and Anthropic require `additionalProperties: false` on all objects in strict mode.

**Example:**
```bash
export HOLMES_DISABLE_STRICT_TOOL_CALLS=true
```

### TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS
**Default:** `false`

When set to `true`, removes the `parameters` object from tool schemas when a tool has no parameters. This is specifically required for Google Gemini models which don't expect a parameters object for parameterless functions.

**Example:**
```bash
export TOOL_SCHEMA_NO_PARAM_OBJECT_IF_NO_PARAMS=true
```

**Note:** This setting is typically only needed when using Gemini models. Other providers handle empty parameter objects correctly.

## SSL/TLS

### CERTIFICATE

Base64-encoded custom CA certificate for outbound HTTPS requests. When set, the certificate is appended to the default CA bundle so that HolmesGPT trusts your private CA for all connections (LLM APIs, Elasticsearch, Prometheus, etc.).

=== "Holmes CLI"

    ```bash
    export CERTIFICATE="$(base64 -w0 /path/to/ca.crt)"
    ```

=== "Holmes Helm Chart"

    ```yaml
    certificate: "<base64-encoded CA cert>"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      certificate: "<base64-encoded CA cert>"
    ```

## Tool Result Size Limits

These variables control how HolmesGPT handles large tool responses that exceed the LLM context window.

When a tool returns more data than the context window can hold, Holmes can either save the result to disk (so the LLM can access it via `cat`, `grep`, etc.) or drop the data with an error asking the LLM to narrow its query.

### HOLMES_TOOL_RESULT_STORAGE_ENABLED
**Default:** `true`

Controls whether large tool results are saved to the filesystem. When enabled, oversized results are written to a temp directory and the LLM receives a file path it can read with bash commands. When disabled, oversized results are dropped and the LLM is asked to retry with a narrower query.

**Example:**
```bash
# Disable filesystem storage for large results
export HOLMES_TOOL_RESULT_STORAGE_ENABLED=false
```

### HOLMES_TOOL_RESULT_STORAGE_PATH
**Default:** System temp directory + `/.holmes` (e.g., `/tmp/.holmes`)

Directory where large tool results are saved. Each chat session creates a subdirectory that is automatically cleaned up when the session ends.

**Example:**
```bash
export HOLMES_TOOL_RESULT_STORAGE_PATH="/var/holmes/tool_results"
```

### TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT
**Default:** `15`

Maximum percentage of the LLM context window that a single tool response can use. If a tool result exceeds this limit, it triggers the large-result handling (filesystem storage or truncation). Set to `0` or a value above `100` to disable percentage-based limiting.

**Example:**
```bash
# Allow each tool response to use up to 25% of context window
export TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT=25
```

### TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_TOKENS
**Default:** `25000`

Absolute maximum tokens for a single tool response, regardless of context window size. The effective limit is the **minimum** of this value and the percentage-based limit above.

**Example:**
```bash
# Raise the absolute cap to 50,000 tokens
export TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_TOKENS=50000
```

## HolmesGPT Configuration

### MODEL_LIST_FILE_LOCATION
Path to a YAML file that defines named model configurations. When set, you can reference models by name using `--model=<name>` in the CLI or the `model` parameter in the HTTP API, instead of specifying the full model identifier and credentials each time.

**Example:**
```bash
export MODEL_LIST_FILE_LOCATION="/path/to/model_list.yaml"
```

See [Using Multiple Providers](../ai-providers/using-multiple-providers.md) for the model list file format and usage.

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

### HOLMES_PASSTHROUGH_BLOCKED_HEADERS
**Default:** `"authorization,cookie,set-cookie"`

Comma-separated list of HTTP header names that should **not** be forwarded from incoming requests to toolsets via `request_context`. Case-insensitive.

**Example:**
```bash
# Also block a custom internal header
export HOLMES_PASSTHROUGH_BLOCKED_HEADERS="authorization,cookie,set-cookie,x-internal-only"
```

See [HTTP Header Propagation](../data-sources/header-propagation.md) for details.

## Data Source Configuration

### Prometheus
- `PROMETHEUS_URL` - URL of the Prometheus server

### Confluence

- `CONFLUENCE_API_URL` - Base URL of Confluence instance (e.g., `https://mycompany.atlassian.net`)
- `CONFLUENCE_USER` - User email (Cloud) or username (Data Center) for authentication
- `CONFLUENCE_API_KEY` - API token (Cloud) or password (Data Center)
- `CONFLUENCE_PAT` - Personal Access Token (Data Center, used with `auth_type: bearer`)

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
Enables live execution of commands in tests. Defaults to `true`.

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

## See Also

- [AI Providers](../ai-providers/index.md) - Detailed configuration for each AI provider
- [CLI Installation](../installation/cli-installation.md) - Setting up the CLI with environment variables
- [Helm Configuration](./helm-configuration.md) - Kubernetes deployment configuration

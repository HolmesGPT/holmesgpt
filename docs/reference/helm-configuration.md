# Helm Configuration

Configuration reference for HolmesGPT Helm chart.

**Quick Links:**

- [Installation Tutorial](../installation/kubernetes-installation.md) - Step-by-step setup guide
- [values.yaml](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml) - Complete configuration reference
- [HTTP API Reference](../reference/http-api.md) - Test your deployment

## Basic Configuration

```yaml
# values.yaml
# Image settings
image: holmes:0.0.0
registry: robustadev

# Logging level
logLevel: INFO

# send exceptions to sentry
enableTelemetry: true

# Resource limits
resources:
  requests:
    cpu: 100m
    memory: 1024Mi
  limits:
    memory: 1024Mi

# Enabled/disable/customize specific toolsets
toolsets:
  kubernetes/core:
    enabled: true
  kubernetes/logs:
    enabled: true
  robusta:
    enabled: true
  internet:
    enabled: true
  prometheus/metrics:
    enabled: true
  ...
```

## Configuration Options

### Essential Settings

| Parameter | Description | Default |
|-----------|-------------|---------|
| `additionalEnvVars` | Environment variables (API keys, etc.) | `[]` |
| `extraEnvVarsSecrets` | List of Kubernetes Secret names whose keys are auto-mounted as env vars on the Holmes pod. Enables the `envRef:VAR` sugar in `modelList`. | `[]` |
| `toolsets` | Enable/disable specific toolsets | (see values.yaml) |
| `modelList` | Configure multiple AI models for UI selection. See [Using Multiple Providers](../ai-providers/using-multiple-providers.md) | `{}` |
| `openshift` | Enable OpenShift compatibility mode | `false` |
| `image` | HolmesGPT image name | `holmes:0.0.0` |
| `registry` | Container registry | `robustadev` |
| `logLevel` | Log level (DEBUG, INFO, WARN, ERROR) | `INFO` |
| `enableTelemetry` | Send exception reports to sentry | `true` |
| `certificate` | Base64 encoded custom CA certificate for outbound HTTPS requests (e.g., LLM API via proxy) | `""` |
| `sentryDSN` | Sentry DSN for telemetry | (see values.yaml) |

#### API Key Configuration

The most important configuration is setting up API keys for your chosen AI provider:

```yaml
additionalEnvVars:
- name: OPENAI_API_KEY
  value: "your-api-key"
# Or load from secret:
# - name: OPENAI_API_KEY
#   valueFrom:
#     secretKeyRef:
#       name: holmes-secrets
#       key: openai-api-key
```

#### Simplified API Key Configuration (`extraEnvVarsSecrets` + `envRef:` sugar)

When you configure several models in `modelList`, listing every key twice — once
in `additionalEnvVars` and once in the model config — becomes noisy. The
`extraEnvVarsSecrets` field mounts one or more Kubernetes Secrets onto the
Holmes pod with a single line each, and the `envRef:VAR` shorthand in `modelList`
is rewritten at chart-render time to the runtime template `{{ env.VAR }}`.

Create the Secret once, with one key per API key you need:

```bash
kubectl create secret generic holmes-secrets \
  --from-literal=OPENAI_API_KEY=sk-... \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  -n <namespace>
```

Then reference it in `values.yaml`:

```yaml
extraEnvVarsSecrets:
  - holmes-secrets

modelList:
  gpt-4.1:
    model: openai/gpt-4.1
    api_key: envRef:OPENAI_API_KEY   # → "{{ env.OPENAI_API_KEY }}" at render time
    temperature: 0
  claude-sonnet-4:
    model: anthropic/claude-sonnet-4-5-20250929
    api_key: envRef:ANTHROPIC_API_KEY
    temperature: 1
```

**Notes:**

- `extraEnvVarsSecrets` is a list — pass multiple Secret names to split keys
  across secrets (e.g. one Secret per provider). Every listed Secret is mounted
  via `envFrom.secretRef`, sharing a single env-var namespace.
- The Secret keys must be valid env-var names (`[A-Za-z_][A-Za-z0-9_]*`) — they
  become environment variables verbatim via `envFrom.secretRef`.
- `envRef:` sugar can be used on any string field in `modelList`, not just
  `api_key` (e.g. `aws_access_key_id: envRef:AWS_ACCESS_KEY_ID`).
- Existing configs using `additionalEnvVars` + `{{ env.OPENAI_API_KEY }}` keep
  working unchanged — `extraEnvVarsSecrets` is opt-in.
- `extraEnvVarsSecrets` can coexist with `additional_env_froms`; both blocks
  are merged into the pod's `envFrom`.

#### Toolset Configuration

Control which capabilities HolmesGPT has access to:

```yaml
toolsets:
  kubernetes/core:
    enabled: true      # Core Kubernetes functionality
  kubernetes/logs:
    enabled: true      # Kubernetes logs access
  robusta:
    enabled: true      # Robusta platform integration
  internet:
    enabled: true      # Internet access for documentation
  prometheus/metrics:
    enabled: true      # Prometheus metrics access
```

### Service Account Configuration

```yaml
# Create service account (default: true)
createServiceAccount: true

# Use custom service account name
customServiceAccountName: ""

# Service account settings
serviceAccount:
  imagePullSecrets: []
  annotations: {}

# Custom RBAC rules
customClusterRoleRules: []
```

For detailed information about the required Kubernetes permissions, see [Kubernetes Permissions](kubernetes-permissions.md).

### Resource Configuration

```yaml
resources:
  requests:
    cpu: 100m
    memory: 1024Mi
  limits:
    cpu: 100m        # Optional CPU limit
    memory: 1024Mi
```

Holmes also enforces a separate **per-subprocess** virtual memory cap on every tool command it runs (via `ulimit -v`), controlled by the `TOOL_MEMORY_LIMIT_MB` environment variable. This cap operates *inside* the pod's memory limit, so the two must be coordinated — keep `TOOL_MEMORY_LIMIT_MB` comfortably below `resources.limits.memory` to leave headroom for Holmes itself. If you raise the pod limit to support larger tool outputs, raise `TOOL_MEMORY_LIMIT_MB` in lockstep. See [Tool Execution Safety](../data-sources/tool-execution-safety.md) for the full mechanism and tuning guidance.

### Toolset Configuration

Enable or disable specific toolsets:

```yaml
toolsets:
  kubernetes/core:
    enabled: true      # Core Kubernetes functionality
  kubernetes/logs:
    enabled: true      # Kubernetes logs access
  robusta:
    enabled: true      # Robusta platform integration
  internet:
    enabled: true      # Internet access for documentation
  prometheus/metrics:
    enabled: true      # Prometheus metrics access
```

### Advanced Configuration

#### Scheduling

```yaml
# Node selection
# nodeSelector:
#   kubernetes.io/os: linux

# Pod affinity/anti-affinity
affinity: {}

# Tolerations
tolerations: []

# Priority class
priorityClassName: ""
```

#### Additional Configuration

```yaml
# Additional environment variables
additionalEnvVars: []
additional_env_vars: []  # Legacy, use additionalEnvVars instead

# Image pull secrets
imagePullSecrets: []

# Additional volumes
additionalVolumes: []

# Additional volume mounts
additionalVolumeMounts: []

# OpenShift compatibility mode
openshift: false

# Account creation
enableAccountsCreate: true

# MCP servers configuration
mcp_servers: {}

# Model list configuration for multiple AI providers (UI only)
# See: https://holmesgpt.dev/ai-providers/using-multiple-providers/
modelList: {}
```

## Example Configurations

### Minimal Setup

```yaml
# values.yaml
image: holmes:0.0.0
registry: robustadev
logLevel: INFO
enableTelemetry: false

resources:
  requests:
    cpu: 100m
    memory: 512Mi
  limits:
    memory: 512Mi

toolsets:
  kubernetes/core:
    enabled: true
  kubernetes/logs:
    enabled: true
  robusta:
    enabled: false
  internet:
    enabled: false
  prometheus/metrics:
    enabled: false
```

### Multiple AI Providers Setup

```yaml
# values.yaml
additionalEnvVars:
  - name: OPENAI_API_KEY
    valueFrom:
      secretKeyRef:
        name: holmes-secrets
        key: openai-api-key
  - name: ANTHROPIC_API_KEY
    valueFrom:
      secretKeyRef:
        name: holmes-secrets
        key: anthropic-api-key
  - name: AWS_ACCESS_KEY_ID
    valueFrom:
      secretKeyRef:
        name: holmes-secrets
        key: aws-access-key-id
  - name: AWS_SECRET_ACCESS_KEY
    valueFrom:
      secretKeyRef:
        name: holmes-secrets
        key: aws-secret-access-key

modelList:
  gpt-4.1:
    api_key: "{{ env.OPENAI_API_KEY }}"
    model: openai/gpt-4.1
    temperature: 0
  claude-sonnet-4-5:
    api_key: "{{ env.ANTHROPIC_API_KEY }}"
    model: anthropic/claude-sonnet-4-5-20250929
    temperature: 1
    thinking:
      budget_tokens: 10000
      type: enabled
  bedrock-sonnet-4-5:
    aws_access_key_id: "{{ env.AWS_ACCESS_KEY_ID }}"
    aws_region_name: us-east-1
    aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
    model: bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0
    temperature: 1
    thinking:
      budget_tokens: 10000
      type: enabled
```

The same setup using `extraEnvVarsSecrets` + the `envRef:` sugar (~40% fewer lines
of YAML, and adding another key never requires a new `additionalEnvVars` entry):

```yaml
# values.yaml
# Create the secret once:
#   kubectl create secret generic holmes-secrets \
#     --from-literal=OPENAI_API_KEY=sk-... \
#     --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
#     --from-literal=AWS_ACCESS_KEY_ID=... \
#     --from-literal=AWS_SECRET_ACCESS_KEY=... \
#     -n <namespace>
extraEnvVarsSecrets:
  - holmes-secrets

modelList:
  gpt-4.1:
    model: openai/gpt-4.1
    api_key: envRef:OPENAI_API_KEY
    temperature: 0
  claude-sonnet-4-5:
    model: anthropic/claude-sonnet-4-5-20250929
    api_key: envRef:ANTHROPIC_API_KEY
    temperature: 1
    thinking:
      budget_tokens: 10000
      type: enabled
  bedrock-sonnet-4-5:
    model: bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0
    aws_access_key_id: envRef:AWS_ACCESS_KEY_ID
    aws_secret_access_key: envRef:AWS_SECRET_ACCESS_KEY
    aws_region_name: us-east-1
    temperature: 1
    thinking:
      budget_tokens: 10000
      type: enabled
```


### OpenShift Setup

```yaml
# values.yaml
openshift: true
createServiceAccount: true

resources:
  requests:
    cpu: 100m
    memory: 1024Mi
  limits:
    memory: 1024Mi

toolsets:
  kubernetes/core:
    enabled: true
  kubernetes/logs:
    enabled: true
```

## Configuration Validation

```bash
# Validate configuration
helm template holmesgpt robusta/holmes -f values.yaml

# Dry run installation
helm install holmesgpt robusta/holmes -f values.yaml --dry-run

# Check syntax
yamllint values.yaml
```

## Complete Reference

For the complete and up-to-date configuration reference, see the actual [`values.yaml`](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml) file in the repository.

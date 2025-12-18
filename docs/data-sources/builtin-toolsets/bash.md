# Bash Toolset

The bash toolset provides secure execution of common command-line tools used for troubleshooting and system analysis. It replaces multiple YAML-based toolsets with a single, comprehensive toolset that includes safety validation and command parsing.

**⚠️ Security Note**: This toolset executes commands on the system where Holmes is running. Only validated, safe commands are allowed, and the toolset is disabled by default for security reasons.

## Supported Commands

The bash toolset supports the following categories of commands:

### Cloud Providers

**AWS CLI (`aws`)**

- Supports various AWS services and operations
- Commands are validated for safety before execution

**Azure CLI (`az`)**

- Supports Azure operations including AKS management
- Network and account operations

### Kubernetes Tools

**kubectl**

- Standard Kubernetes operations: get, describe, logs, events
- Resource management and cluster inspection
- Live metrics via `kubectl top`

**Helm**

- Helm chart operations
- Repository management
- Release inspection

**ArgoCD**

- Application management
- Deployment status checking

### Container Tools

**Docker**

- Container inspection and management
- Image operations
- Basic Docker commands

### Text Processing Utilities

**Data Processing**

- `grep` - Text searching and pattern matching
- `jq` - JSON processing and querying
- `sed` - Stream editing and text transformation
- `awk` - Pattern scanning and text processing

**File Utilities**

- `cut` - Column extraction
- `sort` - Data sorting
- `uniq` - Duplicate removal
- `head` - Show first lines
- `tail` - Show last lines
- `wc` - Word, line, and character counting

**Text Transformation**

- `tr` - Character translation and deletion
- `base64` - Base64 encoding/decoding

### Special Tools

**kubectl_run_image**

Creates temporary debug pods in Kubernetes clusters for diagnostic commands:

- Runs commands in specified container images
- Automatically cleans up temporary pods
- Supports custom namespaces and timeouts
- Useful for network debugging, DNS resolution, and environment inspection

## Command Validation

All commands undergo security validation before execution:

- Only whitelisted commands and options are allowed
- Dangerous operations are blocked (file writes, system calls, etc.)
- Commands are parsed and validated for safety
- Pipe operations between supported commands are allowed

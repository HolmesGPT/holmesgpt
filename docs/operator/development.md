# Development Guide

This guide covers local development workflow for building, modifying, and testing the Holmes Operator.

## Development Prerequisites

Before starting development, ensure you have:

- **Python 3.11+** with Poetry installed
- **Docker** for building container images
- **kubectl** configured to access a Kubernetes cluster
- **Helm 3** installed
- **Local Kubernetes cluster** (kind, minikube, or Docker Desktop)
- **Git** for version control

## Local Development Environment Setup

### Clone the Repository

```bash
git clone https://github.com/HolmesGPT/holmesgpt.git
cd holmesgpt
```

### Install Dependencies

```bash
# Install Python dependencies with Poetry
poetry install

# Activate virtual environment
poetry shell

# Install pre-commit hooks
poetry run pre-commit install
```

### Verify Development Environment

```bash
# Run operator tests
poetry run pytest tests/holmes_operator/ -v

```

## Building Operator Images

### Build Docker Image Locally

The operator uses `Dockerfile.operator` for building images:

```bash
# Build operator image with custom tag
docker build -f Dockerfile.operator -t holmes-operator:dev .

# Build with specific version tag
docker build -f Dockerfile.operator -t holmes-operator:1.0.0-dev .

# View build output and layers
docker history holmes-operator:dev
```

## Modifying Helm Manifests

### Operator Template Files

The operator Helm templates are located in `helm/holmes/templates/`:

**operator-deployment.yaml**

Defines the operator Deployment:

```yaml
# Location: helm/holmes/templates/operator-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "holmes.fullname" . }}-operator
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: operator
          image: "{{ .Values.operator.registry }}/{{ .Values.operator.image }}"
          # ... other configuration
```

**operator-rbac.yaml**

Defines ServiceAccount, ClusterRole, and ClusterRoleBinding:

```yaml
# Location: helm/holmes/templates/operator-rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "holmes.fullname" . }}-operator
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ include "holmes.fullname" . }}-operator
rules:
  # CRD permissions
  # ...
```

### CRD Definitions

CRDs are in `helm/holmes/crds/`:

- `healthcheck.yaml` - HealthCheck CRD
- `scheduledhealthcheck.yaml` - ScheduledHealthCheck CRD

**Editing CRDs:**

!!! warning "CRD Updates"

    Kubernetes does not automatically update CRDs via Helm upgrade. After modifying CRDs, you must manually apply them:

    ```bash
    kubectl apply -f helm/holmes/crds/healthcheck.yaml
    kubectl apply -f helm/holmes/crds/scheduledhealthcheck.yaml
    ```

### Testing Manifest Changes

Test Helm template rendering without installing:

```bash
# Render templates with default values
helm template holmesgpt helm/holmes --set operator.enabled=true

# Render with custom values
helm template holmesgpt helm/holmes -f your-values.yaml --set operator.enabled=true

# Render only operator templates
helm template holmesgpt helm/holmes \
  --set operator.enabled=true \
  --show-only templates/operator-deployment.yaml

# Render and pipe to kubectl diff
helm template holmesgpt helm/holmes -f your-values.yaml | kubectl diff -f -
```

## Installing Local Changes

### Installing with Local Image

Create a custom `values-dev.yaml`:

```yaml
operator:
  enabled: true
  image: holmes-operator:dev  # Your local image
  registry: ""  # Empty for local images
  imagePullPolicy: Never  # Use local image only

  # Development settings
  logLevel: DEBUG
  holmesApiTimeout: 600

  resources:
    requests:
      memory: 256Mi
      cpu: 100m
```

Install or upgrade:

```bash
# Install new release
helm install holmesgpt helm/holmes -f values-dev.yaml

# Upgrade existing release
helm upgrade holmesgpt helm/holmes -f values-dev.yaml

# Verify deployment
kubectl get pods -l app.kubernetes.io/name=holmes-operator
kubectl logs -l app.kubernetes.io/name=holmes-operator --tail=50
```

### Installing from Local Helm Chart

Test changes to the Helm chart itself:

```bash
# Lint Helm chart
helm lint helm/holmes

# Install from local path
helm install holmesgpt ./helm/holmes -f values-dev.yaml

# Upgrade from local path
helm upgrade holmesgpt ./helm/holmes -f values-dev.yaml
```

### Applying CRD Changes

After modifying CRDs:

```bash
# Apply updated CRDs
kubectl apply -f helm/holmes/crds/healthcheck.yaml
kubectl apply -f helm/holmes/crds/scheduledhealthcheck.yaml

# Verify CRD versions
kubectl get crd healthchecks.holmesgpt.dev -o yaml | grep version -A 5
```

## Running Operator Locally (Outside Cluster)

For rapid development, run the operator on your local machine:

### Setup Local Environment

```bash
# Set environment variables
export HOLMES_API_URL="http://localhost:8080"  # Port forward to Holmes API
export LOG_LEVEL="DEBUG"
export MAX_HISTORY_ITEMS="5"

# Port forward to Holmes API
kubectl port-forward svc/holmes-api 8080:80 &

# Verify API is accessible
curl http://localhost:8080/health
```

### Run Operator

```bash
# Activate virtual environment
poetry shell

# Run operator directly
python -m holmes_operator.operator

# Or with custom config
HOLMES_API_URL=http://localhost:8080 \
LOG_LEVEL=DEBUG \
python -m holmes_operator.operator
```

## Testing Changes

### Running Unit Tests

```bash
# Run all operator tests
poetry run pytest tests/holmes_operator/ -v

# Run specific test file
poetry run pytest tests/holmes_operator/test_healthcheck_component.py -v

# Run with coverage
poetry run pytest tests/holmes_operator/ --cov=holmes_operator
```

### Creating Test Resources

Create sample HealthCheck:

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  name: dev-test-check
  namespace: default
  labels:
    environment: development
spec:
  query: "Test query: Are there any pods in default namespace?"
  timeout: 60
  mode: monitor
```

Create sample ScheduledHealthCheck:

```yaml
apiVersion: holmesgpt.dev/v1alpha1
kind: ScheduledHealthCheck
metadata:
  name: dev-test-schedule
  namespace: default
spec:
  schedule: "*/2 * * * *"  # Every 2 minutes for testing
  query: "Test: Are all pods running?"
  enabled: true
```

### Monitoring Test Executions

```bash
# Watch for new HealthChecks created by schedule
kubectl get hc --watch

# View operator logs in real-time
kubectl logs -l app.kubernetes.io/name=holmes-operator --follow

# Check schedule status
kubectl describe shc dev-test-schedule

# View history
kubectl get shc dev-test-schedule -o jsonpath='{.status.history}' | jq
```

## Debugging

### Enable Debug Logging

In your local environment:

```bash
export LOG_LEVEL="DEBUG"
python -m holmes_operator.operator
```

In cluster:

```yaml
operator:
  logLevel: DEBUG
```

### Common Development Issues

**Issue: Operator can't reach Holmes API**

Solution:

```bash
# Verify service DNS resolution
kubectl exec -it deployment/holmes-operator -- nslookup holmes-api

# Check service exists
kubectl get svc holmes-api

# Test connectivity
kubectl exec -it deployment/holmes-operator -- curl http://holmes-api:80/health
```

See the main [CLAUDE.md](../../CLAUDE.md) for full contribution guidelines.

## Additional Resources

- [Kopf Documentation](https://kopf.readthedocs.io/) - Kubernetes operator framework
- [APScheduler Documentation](https://apscheduler.readthedocs.io/) - Job scheduling library
- [Kubernetes Custom Resources](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)
- [Helm Chart Development](https://helm.sh/docs/chart_template_guide/)

## Next Steps

- [Configuration](configuration.md) - Understand operator configuration options
- [Architecture Documentation](../adr/operator-initial-architecture.md) - Detailed design decisions

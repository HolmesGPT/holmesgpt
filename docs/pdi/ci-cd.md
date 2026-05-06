# CI/CD

All CI/CD runs on GitHub Actions. Workflows live in `.github/workflows/`.

## Pipeline Overview

| Workflow | Trigger | Action |
|---|---|---|
| `pdi-lint.yaml` | Every PR | 6 parallel checks: Python lint, SAST, frontend lint, IaC scan, secrets scan, container scan |
| `pdi-build.yaml` | Every PR | Build Docker image, push to dev ECR, comment image tag on PR |
| `pdi-iac.yaml` | PR + push | PR: tofu plan (both envs). Push to main: apply dev. Push to release/**: apply prod |
| `pdi-deploy.yaml` | Push to main/release | Build, push, rollout restart, health check |

## Deployment Flow

- Push to `master` deploys to **dev**
- Push to `release/**` deploys to **prod**
- Manual deployment is available via `workflow_dispatch` with environment selection

The deploy workflow builds the Docker image, pushes it to the target ECR registry, updates the Kubernetes deployment, and runs a health check against the `/healthz` endpoint.

## Required GitHub Secrets

| Secret | Description |
|---|---|
| `PDI_DEV_AWS_ACCESS_KEY_ID` | Dev AWS access key |
| `PDI_DEV_AWS_SECRET_ACCESS_KEY` | Dev AWS secret key |
| `PDI_PROD_AWS_ACCESS_KEY_ID` | Prod AWS access key |
| `PDI_PROD_AWS_SECRET_ACCESS_KEY` | Prod AWS secret key |
| `PDI_DEV_ECR_REGISTRY` | Dev ECR registry URL |
| `PDI_PROD_ECR_REGISTRY` | Prod ECR registry URL |
| `PDI_DEV_TF_STATE_BUCKET` | Dev S3 bucket for Terraform state |
| `PDI_PROD_TF_STATE_BUCKET` | Prod S3 bucket for Terraform state |
| `TF_VAR_anthropic_api_key` | AI gateway token |
| Various `TF_VAR_*` | Integration-specific secrets (Okta, PagerDuty, DBADash, etc.) |

## Hotfix Deploy

For emergency deployments that bypass CI:

```bash
# Direct deploy (emergency only)
aws ecr get-login-password --region us-east-1 --profile <PROFILE> | docker login --username AWS --password-stdin <ECR_REGISTRY>
docker build -t <ECR_REGISTRY>/holmesgpt:<SHA> -f infra/Dockerfile.frontend .
docker push <ECR_REGISTRY>/holmesgpt:<SHA>
kubectl set image deployment/holmes-holmes holmes=<ECR_REGISTRY>/holmesgpt:<SHA> -n holmesgpt
kubectl rollout status deployment/holmes-holmes -n holmesgpt --timeout=120s
```

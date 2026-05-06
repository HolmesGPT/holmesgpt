# Infrastructure

OpenTofu (Terraform) manages all PDI HolmesGPT infrastructure across dev and prod environments.

## Prerequisites

- AWS CLI with SSO profiles configured: `pdi-platform-dev`, `pdi-platform-all`
- OpenTofu 1.6+
- kubectl
- Docker

## Environment Setup

```bash
# Dev
cd infra && tofu init -backend-config=envs/backend-dev.hcl
tofu plan -var-file=envs/dev.tfvars
tofu apply -var-file=envs/dev.tfvars

# Prod
cd infra && tofu init -backend-config=envs/backend-prod.hcl -reconfigure
tofu plan -var-file=envs/prod.tfvars
tofu apply -var-file=envs/prod.tfvars
```

## Key Terraform Resources

| Resource | File | Notes |
|---|---|---|
| EKS cluster | `eks.tf` | Kubernetes 1.32, managed node groups |
| Helm release | `helm.tf` | Holmes app + ALB ingress controller |
| Secrets Manager | `secrets.tf` | Anthropic, Okta, MCP, and integration credentials |
| DynamoDB | `dynamodb.tf` | Single-table config store (projects, users, API keys) |
| ECR | `ecr.tf` | Container image registry |
| IAM | `iam.tf` | IRSA roles for Holmes pod and AWS MCP sidecar |
| ALB + WAF | `alb.tf`, `waf.tf` | Application load balancer with rate limiting |
| Route53 | `route53.tf` | DNS CNAME pointing to ALB |

## Secret Flow

Secrets Manager stores all sensitive values. Terraform reads them and creates `kubernetes_secret` resources in the cluster. Pods reference individual keys via `secretKeyRef` in their environment variable definitions. To rotate a secret: update it in Secrets Manager, run `tofu apply`, then restart the pods.

## Scaling

| Setting | Dev | Prod |
|---|---|---|
| Node instance type | t3.medium | t3.medium |
| Node count | 2 | 3 |
| Holmes replicas | 1 | 2 |

To change scaling, update these variables in the appropriate `tfvars` file: `node_min_size`, `node_desired_size`, `holmes_replicas`.

## Docker Image Operations

```bash
# Login to ECR
aws ecr get-login-password --region us-east-1 --profile <PROFILE> | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com

# Build and push
docker build -t <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/holmesgpt:latest -f infra/Dockerfile.frontend .
docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/holmesgpt:latest

# Restart pods after push
kubectl rollout restart deployment/holmes-holmes -n holmesgpt
kubectl rollout status deployment/holmes-holmes -n holmesgpt --timeout=120s
```

## Common Operations

```bash
# Get kubeconfig
aws eks update-kubeconfig --name holmesgpt-<ENV> --region us-east-1 --profile <PROFILE>

# Check pod status
kubectl get pods -n holmesgpt

# View logs
kubectl logs -n holmesgpt deployment/holmes-holmes --tail=100 -f

# Check env vars on running pod
kubectl exec -n holmesgpt deployment/holmes-holmes -- env | grep MODEL

# Force restart (memory issues)
kubectl delete pod -n holmesgpt <POD_NAME> --grace-period=30
```

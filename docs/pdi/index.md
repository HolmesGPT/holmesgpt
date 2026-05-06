# PDI HolmesGPT

AI-powered infrastructure troubleshooting for PDI platform teams.

## Environments

| Environment | Account | URL | Cluster |
|---|---|---|---|
| Dev | 717423812395 (pdi-platform-dev) | https://holmesgpt.dev.platform.pditechnologies.com | holmesgpt-dev |
| Prod | 827852520868 (pdi-platform-all) | https://holmesgpt.shared.platform.pditechnologies.com | holmesgpt-prod |

## Architecture

Each environment runs an EKS cluster with a Helm chart deploying two containers: the Holmes API server and an AWS MCP sidecar that provides cross-account AWS access. Holmes connects to 39 AWS accounts via `HolmesReadOnly` IAM roles assumed through cross-account trust policies.

Authentication is handled by Okta OIDC using the PKCE flow. The React frontend redirects to Okta for login and receives JWT tokens that the backend validates on every request.

Application state lives in DynamoDB using a single-table design that stores projects, users, and API keys. All sensitive credentials (Anthropic API key, Okta client secrets, integration tokens) are stored in AWS Secrets Manager and injected into pods as environment variables via Terraform-managed Kubernetes secrets.

## Pages

- [Projects & Integrations](projects.md) -- Projects, instances, tag filtering, and webhook routing
- [Users, Roles & API Keys](users-and-auth.md) -- Okta SSO, RBAC roles, and API key management
- [AWS Cross-Account Setup](aws-accounts.md) -- Onboarding AWS accounts, trust policies, and IRSA
- [Infrastructure](infrastructure.md) -- IaC setup, Terraform resources, scaling, and common operations
- [CI/CD](ci-cd.md) -- GitHub Actions workflows, deployment flow, and hotfix procedures

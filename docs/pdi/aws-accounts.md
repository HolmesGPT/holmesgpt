# AWS Cross-Account Setup

Holmes runs in a platform account and investigates other AWS accounts by assuming a `HolmesReadOnly` IAM role in each target account via `sts:AssumeRole`. The trust chain works through IRSA (IAM Roles for Service Accounts): the AWS MCP server pod receives temporary credentials from an IRSA role, then uses those credentials to assume `HolmesReadOnly` in the target account.

## Key Identifiers

| | Dev | Prod |
|---|---|---|
| Platform Account | `717423812395` | `827852520868` |
| IRSA Role | `holmesgpt-dev-aws-mcp` | `holmesgpt-prod-aws-mcp` |
| EKS OIDC ID | `067D7295FD86C99EE25FE9F026B73ABE` | `5532725EB6AD249CA444DB2140B80A6B` |

## Onboarding a New AWS Account

### Step 1: Create the HolmesReadOnly role in the target account

Log into the target account's SSO profile, then run the trust policy script:

```bash
aws sso login --profile <ACCOUNT_PROFILE>
bash scripts/update_holmes_trust_policy.sh <ACCOUNT_PROFILE>

# For EU-region accounts:
bash scripts/update_holmes_trust_policy.sh <ACCOUNT_PROFILE> eu-central-1
```

This creates (or updates) the `HolmesReadOnly` IAM role with a trust policy that allows both dev and prod IRSA roles to assume it. The script also registers the prod EKS OIDC provider if not already present.

### Step 2: Add the account to Terraform variables

Add an entry to the `logistics_accounts` block in **both** `infra/envs/dev.tfvars` and `infra/envs/prod.tfvars`:

```hcl
new-account-name = {
  account_id = "123456789012"
  role_arn   = "arn:aws:iam::123456789012:role/HolmesReadOnly"
  region     = "us-east-1"
}
```

Set `region` to the account's primary region (`us-east-1` or `eu-central-1`). The MCP server uses this as the default region for AWS CLI commands targeting that account.

### Step 3: Confirm aws_mcp_enabled is set

Both environment files should already have `aws_mcp_enabled = true`. Verify this is present.

### Step 4: Apply to both environments

```bash
cd infra

# Dev
tofu init -backend-config=envs/backend-dev.hcl
tofu apply -var-file=envs/dev.tfvars

# Prod (reconfigure backend for different state bucket)
tofu init -backend-config=envs/backend-prod.hcl -reconfigure
tofu apply -var-file=envs/prod.tfvars
```

## Verification

```bash
# From the MCP pod -- proves the IRSA role chain works end-to-end
kubectl exec -n holmesgpt deployment/holmes-aws-mcp-server -- \
  aws sts assume-role \
    --role-arn arn:aws:iam::<ACCOUNT_ID>:role/HolmesReadOnly \
    --role-session-name verify \
    --query "Credentials.AccessKeyId" --output text

# Bulk verification across all onboarded accounts
python3 scripts/verify_trust.py
```

## Trust Policy Reference

The `update_holmes_trust_policy.sh` script applies this trust policy to the `HolmesReadOnly` role in each target account. It contains three statements:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowHolmesMCPAssumeRole",
      "Effect": "Allow",
      "Principal": {
        "AWS": [
          "arn:aws:iam::717423812395:role/holmesgpt-dev-aws-mcp",
          "arn:aws:iam::827852520868:role/holmesgpt-prod-aws-mcp"
        ]
      },
      "Action": "sts:AssumeRole"
    },
    {
      "Sid": "AllowHolmesMCPWebIdentityDev",
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<TARGET_ACCOUNT_ID>:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/067D7295FD86C99EE25FE9F026B73ABE"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "oidc.eks.us-east-1.amazonaws.com/id/067D7295FD86C99EE25FE9F026B73ABE:aud": "sts.amazonaws.com",
          "oidc.eks.us-east-1.amazonaws.com/id/067D7295FD86C99EE25FE9F026B73ABE:sub": "system:serviceaccount:holmesgpt:aws-api-mcp-sa"
        }
      }
    },
    {
      "Sid": "AllowHolmesMCPWebIdentityProd",
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<TARGET_ACCOUNT_ID>:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/5532725EB6AD249CA444DB2140B80A6B"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "oidc.eks.us-east-1.amazonaws.com/id/5532725EB6AD249CA444DB2140B80A6B:aud": "sts.amazonaws.com",
          "oidc.eks.us-east-1.amazonaws.com/id/5532725EB6AD249CA444DB2140B80A6B:sub": "system:serviceaccount:holmesgpt:aws-api-mcp-sa"
        }
      }
    }
  ]
}
```

**AllowHolmesMCPAssumeRole** lets both dev and prod IRSA roles call `sts:AssumeRole` on this role. **AllowHolmesMCPWebIdentityDev/Prod** allows direct `AssumeRoleWithWebIdentity` from the EKS pod's service account token, scoped to the specific OIDC provider and Kubernetes service account (`holmesgpt:aws-api-mcp-sa`).

## Alternative: Terraform Module

For teams that prefer IaC over the shell script, use the Terraform module in `infra/logistics-cross-account/`. This creates the same `HolmesReadOnly` role, registers OIDC providers, and attaches a scoped read-only triage policy.

```bash
cd infra/logistics-cross-account
tofu init
tofu apply \
  -var="aws_profile=<ACCOUNT_PROFILE>" \
  -var='holmes_mcp_role_arns=["arn:aws:iam::717423812395:role/holmesgpt-dev-aws-mcp","arn:aws:iam::827852520868:role/holmesgpt-prod-aws-mcp"]' \
  -var='eks_oidc_providers=["oidc.eks.us-east-1.amazonaws.com/id/067D7295FD86C99EE25FE9F026B73ABE","oidc.eks.us-east-1.amazonaws.com/id/5532725EB6AD249CA444DB2140B80A6B"]'
```

To onboard multiple accounts in one pass, use the batch script:

```bash
bash scripts/run_cross_account.sh
```

This iterates through all configured accounts, running `tofu apply` with per-account state files.

## Troubleshooting

```bash
# Check if the IRSA role annotation is set on the MCP service account
kubectl get sa aws-api-mcp-sa -n holmesgpt -o jsonpath='{.metadata.annotations.eks\.amazonaws\.com/role-arn}'

# Verify the MCP pod can reach STS
kubectl exec -n holmesgpt deployment/holmes-aws-mcp-server -- aws sts get-caller-identity

# Check if the target account's trust policy includes both IRSA ARNs
aws iam get-role --role-name HolmesReadOnly --profile <TARGET_PROFILE> \
  --query "Role.AssumeRolePolicyDocument" --output json

# Verify OIDC provider is registered in the target account
aws iam list-open-id-connect-providers --profile <TARGET_PROFILE> --output table
```

#!/bin/bash
# update_holmes_trust_policy.sh
#
# Updates the HolmesReadOnly IAM role trust policy to allow both
# dev and prod HolmesGPT environments to assume the role.
#
# Usage:
#   aws sso login --profile <ACCOUNT_PROFILE>
#   bash update_holmes_trust_policy.sh <ACCOUNT_PROFILE>
#
# Or with a specific region (default: us-east-1):
#   bash update_holmes_trust_policy.sh <ACCOUNT_PROFILE> eu-central-1

set -euo pipefail

PROFILE="${1:?Usage: $0 <AWS_PROFILE> [REGION]}"
REGION="${2:-us-east-1}"
ROLE_NAME="HolmesReadOnly"

# HolmesGPT platform roles (dev + prod)
DEV_ROLE_ARN="arn:aws:iam::717423812395:role/holmesgpt-dev-aws-mcp"
PROD_ROLE_ARN="arn:aws:iam::827852520868:role/holmesgpt-prod-aws-mcp"

# EKS OIDC provider IDs
DEV_OIDC_ID="067D7295FD86C99EE25FE9F026B73ABE"
PROD_OIDC_ID="5532725EB6AD249CA444DB2140B80A6B"

echo "━━━ Updating $ROLE_NAME in profile=$PROFILE region=$REGION ━━━"

# Get current account ID
ACCOUNT_ID=$(aws sts get-caller-identity --profile "$PROFILE" --region "$REGION" --query "Account" --output text)
echo "Account: $ACCOUNT_ID"

# Check if role exists
if ! aws iam get-role --role-name "$ROLE_NAME" --profile "$PROFILE" --region "$REGION" > /dev/null 2>&1; then
  echo "ERROR: Role $ROLE_NAME does not exist in account $ACCOUNT_ID"
  exit 1
fi

# Build the new trust policy
TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowHolmesMCPAssumeRole",
      "Effect": "Allow",
      "Principal": {
        "AWS": [
          "$DEV_ROLE_ARN",
          "$PROD_ROLE_ARN"
        ]
      },
      "Action": "sts:AssumeRole"
    },
    {
      "Sid": "AllowHolmesMCPWebIdentityDev",
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/${DEV_OIDC_ID}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "oidc.eks.us-east-1.amazonaws.com/id/${DEV_OIDC_ID}:aud": "sts.amazonaws.com",
          "oidc.eks.us-east-1.amazonaws.com/id/${DEV_OIDC_ID}:sub": "system:serviceaccount:holmesgpt:aws-api-mcp-sa"
        }
      }
    },
    {
      "Sid": "AllowHolmesMCPWebIdentityProd",
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/${PROD_OIDC_ID}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "oidc.eks.us-east-1.amazonaws.com/id/${PROD_OIDC_ID}:aud": "sts.amazonaws.com",
          "oidc.eks.us-east-1.amazonaws.com/id/${PROD_OIDC_ID}:sub": "system:serviceaccount:holmesgpt:aws-api-mcp-sa"
        }
      }
    }
  ]
}
EOF
)

# Update the trust policy
aws iam update-assume-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-document "$TRUST_POLICY" \
  --profile "$PROFILE" \
  --region "$REGION"

echo "✓ Trust policy updated for $ROLE_NAME in $ACCOUNT_ID"

# Also register the prod OIDC provider if not already present
PROD_OIDC_URL="oidc.eks.us-east-1.amazonaws.com/id/${PROD_OIDC_ID}"
if aws iam list-open-id-connect-providers --profile "$PROFILE" --region "$REGION" \
    --query "OpenIDConnectProviderList[?ends_with(Arn, '/${PROD_OIDC_URL}')].Arn" \
    --output text | grep -q "$PROD_OIDC_URL"; then
  echo "✓ Prod OIDC provider already registered"
else
  echo "Registering prod OIDC provider..."
  aws iam create-open-id-connect-provider \
    --url "https://${PROD_OIDC_URL}" \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 06b25927c42a721631c1efd9431e648fa62e1e39 \
    --profile "$PROFILE" \
    --region "$REGION" \
    --tags Key=ManagedBy,Value=holmesgpt Key=Application,Value=holmesgpt 2>&1
  echo "✓ Prod OIDC provider registered"
fi

echo "━━━ Done ━━━"

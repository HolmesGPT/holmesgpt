#!/bin/bash
set -e

cd "C:/Codebase/holmesgpt-pdi/infra/logistics-cross-account"

ROLE_ARNS='["arn:aws:iam::717423812395:role/holmesgpt-dev-aws-mcp","arn:aws:iam::827852520868:role/holmesgpt-prod-aws-mcp"]'
OIDC_PROVIDERS='["oidc.eks.us-east-1.amazonaws.com/id/067D7295FD86C99EE25FE9F026B73ABE","oidc.eks.us-east-1.amazonaws.com/id/5532725EB6AD249CA444DB2140B80A6B"]'

# Map: profile|region|state_file_suffix
ACCOUNTS=(
  "pdi-logistics-ci|us-east-1|logistics-ci"
  "pdi-logistics-dev|us-east-1|logistics-dev"
  "pdi-logistics-stage|us-east-1|logistics-stage"
  "pdi-logistics-sandbox|us-east-1|logistics-sandbox"
  "pdi-logistics-prod|eu-central-1|logistics-prod"
  "AWSAdministratorAccess-689863073433|eu-central-1|pdi-pos-dev"
  "AWSAdministratorAccess-803964703583|eu-central-1|pdi-pos-prod"
  "AWSAdministratorAccess-415641701024|eu-central-1|pdi-pos-stage"
  "AWSAdministratorAccess-100161908138|eu-central-1|pdi-pos-legacy-prod"
  "AWSAdministratorAccess-294818304262|eu-central-1|pdi-pos-legacy-uat"
  "AWSAdministratorAccess-226168396949|eu-central-1|pdi-pos-legacy-demo"
  "AWSAdministratorAccess-896521799855|us-east-1|gasbuddy"
  "gasbuddy-staging|us-east-1|gasbuddy-staging"
  "gasbuddy-marketing|us-east-1|gasbuddy-marketing"
  "AWSAdministratorAccess-607378507561|us-east-1|gb-bp-client"
  "AWSAdministratorAccess-386397235394|us-east-1|ce-cstore-essentials-prod"
)

PASSED=0
FAILED=0
ERRORS=""

for entry in "${ACCOUNTS[@]}"; do
  IFS='|' read -r profile region name <<< "$entry"
  STATE_FILE="terraform-${name}.tfstate"
  
  echo "━━━ $name ($profile, $region) ━━━"
  
  # Check credentials first
  if ! aws sts get-caller-identity --profile "$profile" --region "$region" > /dev/null 2>&1; then
    echo "  ⚠ SKIP - SSO expired or no access"
    FAILED=$((FAILED + 1))
    ERRORS="$ERRORS\n  $name: SSO expired"
    continue
  fi
  
  # Run tofu apply with separate state file per account
  if tofu apply -auto-approve \
    -state="$STATE_FILE" \
    -var="aws_profile=$profile" \
    -var="aws_region=$region" \
    -var="holmes_mcp_role_arns=$ROLE_ARNS" \
    -var="eks_oidc_providers=$OIDC_PROVIDERS" \
    2>&1 | tail -5; then
    echo "  ✓ $name done"
    PASSED=$((PASSED + 1))
  else
    echo "  ✗ $name FAILED"
    FAILED=$((FAILED + 1))
    ERRORS="$ERRORS\n  $name: apply failed"
  fi
  echo ""
done

echo "━━━ SUMMARY ━━━"
echo "Passed: $PASSED"
echo "Failed: $FAILED"
if [ -n "$ERRORS" ]; then
  echo -e "Errors:$ERRORS"
fi

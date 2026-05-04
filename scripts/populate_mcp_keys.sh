#!/usr/bin/env bash
# Copy MCP API keys from the source secret into holmesgpt-<env>/mcp-api-keys.
#
# Source: arn:aws:secretsmanager:us-east-1:717423812395:secret:mcp-readonly-api-keys-L63NWI
#   Contains { ado, atlassian, salesforce, jenkins } — read-only PDI gateway keys.
#
# Destination: holmesgpt-<env>/mcp-api-keys
#   Renames to { MCP_ADO_API_KEY, MCP_ATLASSIAN_API_KEY, MCP_SALESFORCE_API_KEY, MCP_JENKINS_API_KEY }.
#
# Usage:  bash scripts/populate_mcp_keys.sh dev
#         bash scripts/populate_mcp_keys.sh prod
#
# Idempotent. Prints SET/EMPTY summary (no key values).

set -euo pipefail

ENV="${1:?Usage: $0 <dev|prod>}"
case "$ENV" in
  dev)
    DEST_PROFILE="pdi-platform-dev"
    DEST_SECRET="holmesgpt-dev/mcp-api-keys"
    ;;
  prod)
    DEST_PROFILE="pdi-platform-all"
    DEST_SECRET="holmesgpt-prod/mcp-api-keys"
    ;;
  *)
    echo "ERROR: unknown environment '$ENV' (expected dev|prod)" >&2
    exit 1
    ;;
esac

# Source lives in the dev account (717423812395) and is readable from both profiles,
# but we use dev profile unconditionally for clarity.
SOURCE_PROFILE="pdi-platform-dev"
SOURCE_SECRET="arn:aws:secretsmanager:us-east-1:717423812395:secret:mcp-readonly-api-keys-L63NWI"
REGION="us-east-1"

echo "Reading source secret..."
SOURCE_JSON=$(aws secretsmanager get-secret-value \
  --secret-id "$SOURCE_SECRET" \
  --profile "$SOURCE_PROFILE" \
  --region "$REGION" \
  --query SecretString --output text)

if [ -z "$SOURCE_JSON" ] || [ "$SOURCE_JSON" = "None" ]; then
  echo "ERROR: source secret returned empty string" >&2
  exit 1
fi

# Extract each key; fall back to empty string if missing.
extract() {
  python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('$1',''))" <<<"$SOURCE_JSON"
}

ADO=$(extract ado)
ATLASSIAN=$(extract atlassian)
SALESFORCE=$(extract salesforce)
JENKINS=$(extract jenkins)

# Warn on any missing keys but proceed.
for name in ado atlassian salesforce jenkins; do
  val=$(extract "$name")
  if [ -z "$val" ]; then
    echo "WARN: source secret is missing key '$name' — will write empty string to destination" >&2
  fi
done

DEST_JSON=$(python3 - "$ADO" "$ATLASSIAN" "$SALESFORCE" "$JENKINS" <<'PY'
import json, sys
ado, atlassian, salesforce, jenkins = sys.argv[1:5]
print(json.dumps({
    "MCP_ADO_API_KEY":        ado,
    "MCP_ATLASSIAN_API_KEY":  atlassian,
    "MCP_SALESFORCE_API_KEY": salesforce,
    "MCP_JENKINS_API_KEY":    jenkins,
}))
PY
)

echo "Writing to $DEST_SECRET (profile=$DEST_PROFILE)..."
aws secretsmanager put-secret-value \
  --secret-id "$DEST_SECRET" \
  --profile "$DEST_PROFILE" \
  --region "$REGION" \
  --secret-string "$DEST_JSON" \
  --output text \
  --query 'VersionId' >/dev/null

echo "Done. Summary:"
python3 - <<PY
import json
d = json.loads('''$DEST_JSON''')
for k, v in d.items():
    print(f"  {k}: {'SET (' + str(len(v)) + ' chars)' if v else 'EMPTY'}")
PY

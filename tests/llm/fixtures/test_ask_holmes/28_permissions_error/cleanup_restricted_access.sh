#!/bin/bash
set -e

# Clean up the restricted access resources
kubectl delete clusterrole test-clusterrole-28 --ignore-not-found
kubectl delete secret restricted-holmes-sa-token -n 28-test --ignore-not-found
kubectl delete clusterrolebinding restricted-holmes-binding-28 --ignore-not-found
kubectl delete clusterrole restricted-holmes-role-28 --ignore-not-found
kubectl delete serviceaccount restricted-holmes-sa -n 28-test --ignore-not-found

# Delete the test namespace
kubectl delete namespace 28-test --ignore-not-found

# Clean up temporary directory and kubeconfig (cross-platform)
TEMP_BASE="${TMPDIR:-/tmp}"
TEMP_DIR="$TEMP_BASE/holmes-test-28-permissions"
rm -rf "$TEMP_DIR" 2>/dev/null || true

#!/bin/bash
# Build ArgoCD binary with Go 1.25.7+ to fix CVE-2025-68121.
# ArgoCD v3.3.4 ships with Go 1.25.5 which is vulnerable.
# This should be reverted when ArgoCD releases a version built with Go >= 1.25.7.
#
# Prerequisites: Go 1.25.7+ installed locally
# Usage: ./scripts/build_go_binaries.sh

set -euo pipefail

ARGOCD_VERSION=v3.3.4
ARGOCD_VERSION_NO_V="${ARGOCD_VERSION#v}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
OUTDIR="$REPO_ROOT/bin/go-cve-rebuild"
TMPDIR=$(mktemp -d)

trap "rm -rf $TMPDIR" EXIT

echo "Output directory: $OUTDIR"
mkdir -p "$OUTDIR"/{amd64,arm64}

echo "==> Cloning ArgoCD $ARGOCD_VERSION..."
git clone --depth 1 --branch "$ARGOCD_VERSION" https://github.com/argoproj/argo-cd.git "$TMPDIR/argo-cd"

echo "==> Building ArgoCD for linux/amd64..."
cd "$TMPDIR/argo-cd"
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
  -ldflags "-X github.com/argoproj/argo-cd/v3/common.version=$ARGOCD_VERSION_NO_V" \
  -o "$OUTDIR/amd64/argocd" ./cmd

echo "==> Building ArgoCD for linux/arm64..."
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build \
  -ldflags "-X github.com/argoproj/argo-cd/v3/common.version=$ARGOCD_VERSION_NO_V" \
  -o "$OUTDIR/arm64/argocd" ./cmd

echo "==> Compressing binaries..."
gzip -f "$OUTDIR/amd64/argocd"
gzip -f "$OUTDIR/arm64/argocd"

echo ""
echo "Done! Compressed binaries:"
ls -lh "$OUTDIR/amd64/"
ls -lh "$OUTDIR/arm64/"

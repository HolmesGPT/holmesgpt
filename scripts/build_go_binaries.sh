#!/bin/bash
# Build CVE-patched Go binaries for the holmes Docker image.
#
# ArgoCD: built with Go 1.25.7+ to fix CVE-2025-68121.
#   ArgoCD v3.3.4 ships with Go 1.25.5 which is vulnerable.
#   Revert when ArgoCD releases a version built with Go >= 1.25.7.
#
# Helm: built with Go 1.25.9+ to fix stdlib CVE-2026-32280/32281/32283/25679,
#   and grpc replaced to v1.79.3 to fix CVE-2026-33186.
#   Helm v3.20.2 ships with Go 1.25.8 + grpc 1.72.2 which are vulnerable.
#   Revert when Helm releases a version built with Go >= 1.25.9 and grpc >= 1.79.3.
#
# Prerequisites: Go 1.25.9+ installed locally
# Usage: ./scripts/build_go_binaries.sh

set -euo pipefail

ARGOCD_VERSION=v3.3.4
ARGOCD_VERSION_NO_V="${ARGOCD_VERSION#v}"
HELM_VERSION=v3.20.2
GRPC_PATCHED_VERSION=v1.79.3
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

echo "==> Cloning Helm $HELM_VERSION..."
git clone --depth 1 --branch "$HELM_VERSION" https://github.com/helm/helm.git "$TMPDIR/helm"

echo "==> Pinning grpc to $GRPC_PATCHED_VERSION (CVE-2026-33186)..."
cd "$TMPDIR/helm"
go mod edit -replace="google.golang.org/grpc=google.golang.org/grpc@$GRPC_PATCHED_VERSION"
# Skip 'go mod tidy' — it re-resolves the full graph and pulls test-only transitives
# that fail to build (e.g. otel/sdk/internal/internaltest removed in newer otel releases).
# GOFLAGS=-mod=mod lets 'go build' fetch only what the binary actually needs.

HELM_LDFLAGS="-w -s -X helm.sh/helm/v3/internal/version.version=$HELM_VERSION"

echo "==> Building Helm for linux/amd64..."
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOFLAGS=-mod=mod go build \
  -ldflags "$HELM_LDFLAGS" \
  -o "$OUTDIR/amd64/helm" ./cmd/helm

echo "==> Building Helm for linux/arm64..."
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 GOFLAGS=-mod=mod go build \
  -ldflags "$HELM_LDFLAGS" \
  -o "$OUTDIR/arm64/helm" ./cmd/helm

echo "==> Compressing binaries..."
gzip -f "$OUTDIR/amd64/argocd"
gzip -f "$OUTDIR/arm64/argocd"
gzip -f "$OUTDIR/amd64/helm"
gzip -f "$OUTDIR/arm64/helm"

echo ""
echo "Done! Compressed binaries:"
ls -lh "$OUTDIR/amd64/"
ls -lh "$OUTDIR/arm64/"

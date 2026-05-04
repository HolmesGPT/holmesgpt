#!/bin/bash
# Build CVE-patched Go binaries for the holmes Docker image.
#
# ArgoCD: rebuilt from v3.3.9 source with otel/sdk replaced to v1.43.0 to fix
#   CVE-2026-39883. ArgoCD v3.3.9 already ships with patched grpc 1.79.3 + go-jose
#   4.1.4 + spdystream 0.5.1 + Go 1.26.2 (so stdlib + CVE-2026-33186/34986/35469
#   + CVE-2025-68121 are clean upstream); only otel/sdk still needs the replace.
#   Revert to plain upstream binary when ArgoCD ships otel/sdk >= 1.43.0.
#
# Helm: built with Go 1.25.9+ to fix stdlib CVE-2026-32280/32281/32283/25679,
#   and grpc replaced to v1.79.3 to fix CVE-2026-33186.
#   Helm v3.20.2 ships with Go 1.25.8 + grpc 1.72.2 which are vulnerable.
#   Revert when Helm releases a version built with Go >= 1.25.9 and grpc >= 1.79.3.
#
# kube-lineage: built with Go 1.25.9+ to fix stdlib CVE-2026-32280/32281/32283/25679,
#   with grpc replaced to v1.79.3 (CVE-2026-33186) and spdystream replaced to v0.5.1
#   (CVE-2026-35469).
#   robusta-dev/kube-lineage v2.2.5 ships with Go 1.24.13 + grpc 1.64.1 + spdystream 0.5.0.
#   Revert when kube-lineage releases a version built with Go >= 1.25.9, grpc >= 1.79.3,
#   and spdystream >= 0.5.1.
#
# Prerequisites: Go 1.25.9+ installed locally
# Usage: ./scripts/build_go_binaries.sh

set -euo pipefail

MIN_GO_VERSION="1.25.9"
CURRENT_GO_VERSION="$(go env GOVERSION 2>/dev/null | sed 's/^go//')"
if [ -z "$CURRENT_GO_VERSION" ]; then
  echo "Go is not installed or not on PATH. Go ${MIN_GO_VERSION}+ is required." >&2
  exit 1
fi
if ! printf '%s\n%s\n' "$MIN_GO_VERSION" "$CURRENT_GO_VERSION" | sort -V -C; then
  echo "Go ${MIN_GO_VERSION}+ is required (found ${CURRENT_GO_VERSION}). Please upgrade." >&2
  exit 1
fi

assert_module_version() {
  local module="$1"
  local expected="$2"
  local actual
  # Resolve via the replace directive if one is present, otherwise the require version.
  actual="$(go list -m -f '{{if .Replace}}{{.Replace.Version}}{{else}}{{.Version}}{{end}}' "$module" 2>/dev/null)"
  if [ "$actual" != "$expected" ]; then
    echo "ERROR: Expected $module=$expected, got ${actual:-<missing>}" >&2
    exit 1
  fi
}

ARGOCD_VERSION=v3.3.9
ARGOCD_VERSION_NO_V="${ARGOCD_VERSION#v}"
OTEL_SDK_PATCHED_VERSION=v1.43.0
HELM_VERSION=v3.20.2
GRPC_PATCHED_VERSION=v1.79.3
KUBE_LINEAGE_VERSION=v2.2.5
SPDYSTREAM_PATCHED_VERSION=v0.5.1
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
OUTDIR="$REPO_ROOT/bin/go-cve-rebuild"
TMPDIR=$(mktemp -d)

trap "rm -rf $TMPDIR" EXIT

echo "Output directory: $OUTDIR"
mkdir -p "$OUTDIR"/{amd64,arm64}

echo "==> Cloning ArgoCD $ARGOCD_VERSION..."
git clone --depth 1 --branch "$ARGOCD_VERSION" https://github.com/argoproj/argo-cd.git "$TMPDIR/argo-cd"

echo "==> Pinning otel/sdk to $OTEL_SDK_PATCHED_VERSION (CVE-2026-39883)..."
cd "$TMPDIR/argo-cd"
go mod edit -replace="go.opentelemetry.io/otel/sdk=go.opentelemetry.io/otel/sdk@$OTEL_SDK_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "go.opentelemetry.io/otel/sdk" "$OTEL_SDK_PATCHED_VERSION"

ARGOCD_LDFLAGS="-X github.com/argoproj/argo-cd/v3/common.version=$ARGOCD_VERSION_NO_V"

echo "==> Building ArgoCD for linux/amd64..."
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOFLAGS=-mod=mod go build \
  -ldflags "$ARGOCD_LDFLAGS" \
  -o "$OUTDIR/amd64/argocd" ./cmd

echo "==> Building ArgoCD for linux/arm64..."
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 GOFLAGS=-mod=mod go build \
  -ldflags "$ARGOCD_LDFLAGS" \
  -o "$OUTDIR/arm64/argocd" ./cmd

echo "==> Cloning Helm $HELM_VERSION..."
git clone --depth 1 --branch "$HELM_VERSION" https://github.com/helm/helm.git "$TMPDIR/helm"

echo "==> Pinning grpc to $GRPC_PATCHED_VERSION (CVE-2026-33186)..."
cd "$TMPDIR/helm"
go mod edit -replace="google.golang.org/grpc=google.golang.org/grpc@$GRPC_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "google.golang.org/grpc" "$GRPC_PATCHED_VERSION"
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

echo "==> Cloning kube-lineage $KUBE_LINEAGE_VERSION..."
git clone --depth 1 --branch "$KUBE_LINEAGE_VERSION" https://github.com/robusta-dev/kube-lineage.git "$TMPDIR/kube-lineage"

echo "==> Pinning grpc to $GRPC_PATCHED_VERSION (CVE-2026-33186) and spdystream to $SPDYSTREAM_PATCHED_VERSION (CVE-2026-35469)..."
cd "$TMPDIR/kube-lineage"
go mod edit -replace="google.golang.org/grpc=google.golang.org/grpc@$GRPC_PATCHED_VERSION"
go mod edit -replace="github.com/moby/spdystream=github.com/moby/spdystream@$SPDYSTREAM_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "google.golang.org/grpc" "$GRPC_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/moby/spdystream" "$SPDYSTREAM_PATCHED_VERSION"

echo "==> Building kube-lineage for linux/amd64..."
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOFLAGS=-mod=mod go build \
  -o "$OUTDIR/amd64/kube-lineage" ./cmd/kube-lineage

echo "==> Building kube-lineage for linux/arm64..."
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 GOFLAGS=-mod=mod go build \
  -o "$OUTDIR/arm64/kube-lineage" ./cmd/kube-lineage

echo "==> Compressing binaries..."
gzip -f "$OUTDIR/amd64/argocd"
gzip -f "$OUTDIR/arm64/argocd"
gzip -f "$OUTDIR/amd64/helm"
gzip -f "$OUTDIR/arm64/helm"
gzip -f "$OUTDIR/amd64/kube-lineage"
gzip -f "$OUTDIR/arm64/kube-lineage"

echo "==> Generating SHA-256 checksums..."
if command -v sha256sum >/dev/null 2>&1; then
  SHA256_CMD="sha256sum"
else
  # macOS fallback
  SHA256_CMD="shasum -a 256"
fi
for arch in amd64 arm64; do
  (cd "$OUTDIR/$arch" && for f in argocd.gz helm.gz kube-lineage.gz; do
    $SHA256_CMD "$f" > "$f.sha256"
  done)
done

echo ""
echo "Done! Compressed binaries:"
ls -lh "$OUTDIR/amd64/"
ls -lh "$OUTDIR/arm64/"

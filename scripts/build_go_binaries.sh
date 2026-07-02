#!/bin/bash
# Build CVE-patched Go binaries for the holmes Docker image.
#
# All binaries built by this script are built with Go 1.26.4 to fix stdlib
# CVE-2026-42499/33814/39836/33811/39820 (fixed in Go 1.26.3).
#
# ArgoCD: rebuilt from v3.3.11 source with go-git replaced to v5.19.1 and
#   go-billy replaced to v5.9.0. ArgoCD pins go-git v5.14.0 upstream
#   ("DO NOT BUMP UNTIL go-git/go-git#1551 is fixed" — an SSH-push regression
#   that holmes never hits, since argocd is used as a read-only API client).
#   go-git v5.14.0 is vulnerable to CVE-2026-41506 (fixed 5.18.0),
#   CVE-2026-45022 (fixed 5.19.0), CVE-2026-45570/45571 (fixed 5.19.1);
#   go-billy v5.6.2 is vulnerable to CVE-2026-44973 (fixed 5.9.0).
#   v3.3.11 already ships otel/sdk 1.43.0 so the old otel replace was dropped.
#   Revert to plain upstream binary when ArgoCD ships go-git >= 5.19.1 and
#   go-billy >= 5.9.0 (blocked on go-git/go-git#1551 upstream).
#
# Helm: built from v3.21.0, which already ships grpc v1.80.0 (the old grpc
#   replace for CVE-2026-33186 was dropped), with containerd replaced to
#   v1.7.32 (CVE-2026-46680; v3.21.0 ships v1.7.30).
#   Revert to upstream binary when Helm releases a version built with
#   Go >= 1.26.3 and containerd >= 1.7.32.
#
# kube-lineage: built with grpc replaced to v1.79.3 (CVE-2026-33186),
#   spdystream replaced to v0.5.1 (CVE-2026-35469), containerd replaced
#   to v1.7.32 (CVE-2026-46680), and helm replaced to v3.20.2 (CVE-2026-35206).
#   robusta-dev/kube-lineage v2.2.5 ships with Go 1.24.13 + grpc 1.64.1 + spdystream 0.5.0.
#   Revert when kube-lineage releases a version built with Go >= 1.26.3,
#   grpc >= 1.79.3, spdystream >= 0.5.1, containerd >= 1.7.32, and helm >= 3.20.2.
#
# kubectl is NOT built here — the official dl.k8s.io binary (kubectl v1.36.2,
#   pinned in the Dockerfile) is already built with Go 1.26.4, so it carries the
#   stdlib CVE fixes without a from-source rebuild. Earlier releases were not:
#   check a candidate with `go version <(curl -sL
#   https://dl.k8s.io/release/<ver>/bin/linux/amd64/kubectl)` before bumping the
#   Dockerfile pin, since the 1.34/1.35 lines are still on a vulnerable Go.
#
# Prerequisites: Go 1.21+ installed locally (GOTOOLCHAIN auto-downloads the
#   pinned build toolchain below)
# Usage: ./scripts/build_go_binaries.sh

set -euo pipefail

# Pin the build toolchain: Go >= 1.26.3 fixes stdlib
# CVE-2026-42499/33814/39836/33811/39820. Go auto-downloads it if the locally
# installed go is older (requires local go >= 1.21).
export GOTOOLCHAIN=go1.26.4

MIN_GO_VERSION="1.26.3"
# Minimum *local* Go that can bootstrap the GOTOOLCHAIN auto-download above
# (the GOTOOLCHAIN mechanism landed in Go 1.21). Intentionally lower than
# MIN_GO_VERSION: the pinned build toolchain is fetched automatically, so the
# locally installed go only needs to be new enough to honor GOTOOLCHAIN.
MIN_BOOTSTRAP_GO_VERSION="1.21"
# Check for the go binary first: under `set -e` the command substitution below
# would otherwise abort the script before the empty-string check could run.
if ! command -v go >/dev/null 2>&1; then
  echo "Go is not installed or not on PATH. Go ${MIN_BOOTSTRAP_GO_VERSION}+ is required (GOTOOLCHAIN downloads ${GOTOOLCHAIN#go})." >&2
  exit 1
fi
CURRENT_GO_VERSION="$(go env GOVERSION 2>/dev/null | sed 's/^go//')"
if [ -z "$CURRENT_GO_VERSION" ]; then
  echo "Unable to determine Go version from 'go env GOVERSION'." >&2
  exit 1
fi
# Portable version comparison (avoids GNU-only `sort -V`): sort min+current
# numerically by dotted component; if the smallest isn't MIN_GO_VERSION, current
# is older. Works on both GNU and BSD/macOS sort.
if [ "$(printf '%s\n%s\n' "$MIN_GO_VERSION" "$CURRENT_GO_VERSION" | sort -t. -k1,1n -k2,2n -k3,3n | head -n1)" != "$MIN_GO_VERSION" ]; then
  echo "Go ${MIN_GO_VERSION}+ is required (found ${CURRENT_GO_VERSION}). GOTOOLCHAIN switch failed?" >&2
  exit 1
fi
echo "Building with Go ${CURRENT_GO_VERSION}"

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

ARGOCD_VERSION=v3.3.11
ARGOCD_VERSION_NO_V="${ARGOCD_VERSION#v}"
GO_GIT_PATCHED_VERSION=v5.19.1
GO_BILLY_PATCHED_VERSION=v5.9.0
HELM_VERSION=v3.21.0
GRPC_PATCHED_VERSION=v1.79.3
KUBE_LINEAGE_VERSION=v2.2.5
SPDYSTREAM_PATCHED_VERSION=v0.5.1
CONTAINERD_PATCHED_VERSION=v1.7.32
HELM_IN_LINEAGE_PATCHED_VERSION=v3.20.2
SLACK_GO_PATCHED_VERSION=v0.23.1
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
OUTDIR="$REPO_ROOT/bin/go-cve-rebuild"
TMPDIR=$(mktemp -d)

trap "rm -rf $TMPDIR" EXIT

echo "Output directory: $OUTDIR"
mkdir -p "$OUTDIR"/{amd64,arm64}

echo "==> Cloning ArgoCD $ARGOCD_VERSION..."
git clone --depth 1 --branch "$ARGOCD_VERSION" https://github.com/argoproj/argo-cd.git "$TMPDIR/argo-cd"

echo "==> Pinning go-git to $GO_GIT_PATCHED_VERSION (CVE-2026-41506/45022/45570/45571) and go-billy to $GO_BILLY_PATCHED_VERSION (CVE-2026-44973)..."
cd "$TMPDIR/argo-cd"
go mod edit -replace="github.com/go-git/go-git/v5=github.com/go-git/go-git/v5@$GO_GIT_PATCHED_VERSION"
go mod edit -replace="github.com/go-git/go-billy/v5=github.com/go-git/go-billy/v5@$GO_BILLY_PATCHED_VERSION"
# slack-go v0.16.0 has GHSA-gxhx-2686-5h9g (Medium); fixed in v0.23.1
go mod edit -replace="github.com/slack-go/slack=github.com/slack-go/slack@$SLACK_GO_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/go-git/go-git/v5" "$GO_GIT_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/go-git/go-billy/v5" "$GO_BILLY_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/slack-go/slack" "$SLACK_GO_PATCHED_VERSION"

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

cd "$TMPDIR/helm"
# Helm v3.21.0 already ships grpc v1.80.0 (>= the CVE-2026-33186 fix in
# v1.79.3); only containerd still needs a replace.
echo "==> Pinning containerd to $CONTAINERD_PATCHED_VERSION (CVE-2026-46680)..."
go mod edit -replace="github.com/containerd/containerd=github.com/containerd/containerd@$CONTAINERD_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/containerd/containerd" "$CONTAINERD_PATCHED_VERSION"

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

echo "==> Pinning grpc to $GRPC_PATCHED_VERSION (CVE-2026-33186), spdystream to $SPDYSTREAM_PATCHED_VERSION (CVE-2026-35469), and containerd to $CONTAINERD_PATCHED_VERSION (CVE-2026-46680)..."
cd "$TMPDIR/kube-lineage"
go mod edit -replace="google.golang.org/grpc=google.golang.org/grpc@$GRPC_PATCHED_VERSION"
go mod edit -replace="github.com/moby/spdystream=github.com/moby/spdystream@$SPDYSTREAM_PATCHED_VERSION"
go mod edit -replace="github.com/containerd/containerd=github.com/containerd/containerd@$CONTAINERD_PATCHED_VERSION"
# embedded helm v3.19.0 has CVE-2026-35206 (Medium); fixed in v3.20.2
go mod edit -replace="helm.sh/helm/v3=helm.sh/helm/v3@$HELM_IN_LINEAGE_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "google.golang.org/grpc" "$GRPC_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/moby/spdystream" "$SPDYSTREAM_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/containerd/containerd" "$CONTAINERD_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "helm.sh/helm/v3" "$HELM_IN_LINEAGE_PATCHED_VERSION"

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

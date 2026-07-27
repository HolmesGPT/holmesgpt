#!/bin/bash
# Build CVE-patched Go binaries for the holmes Docker image.
#
# All binaries built by this script are built with Go 1.26.5 to fix stdlib
# CVE-2026-39822 (High) and CVE-2026-42505 (Medium), both fixed in Go 1.26.5,
# on top of the earlier CVE-2026-42499/33814/39836/33811/39820 fixes (Go 1.26.3).
#
# Two x/* replaces are applied to every binary that pulls them in, because the
# same advisories hit all of them:
#   golang.org/x/net    -> v0.57.0  CVE-2026-33814 (fixed 0.53.0) plus
#                                   CVE-2026-25681/27136/39821 (High) and
#                                   CVE-2026-25680/42502/42506 (Medium, >60d),
#                                   all fixed in 0.55.0; 0.56.0 adds the
#                                   CVE-2026-46600 fix.
#   golang.org/x/crypto -> v0.54.0  CVE-2026-39828/39829/39830/39831/39832/39835/
#                                   42508/46595/46597 (High) and CVE-2026-39827/
#                                   39833/39834/46598 (Medium, >60d), all fixed
#                                   in 0.52.0. Pinned to 0.54.0 rather than
#                                   0.52.0 so the replace does not drag x/crypto
#                                   back below what x/net v0.57.0 requires.
# Bumping x/net to 0.57.0 also drags x/sys to 0.47.0 and x/text to 0.40.0 through
# MVS, which clears CVE-2026-39824 (x/sys) and CVE-2026-56852 (x/text).
#
# ArgoCD: rebuilt from v3.3.11 source with go-git replaced to v5.19.1 and
#   go-billy replaced to v5.9.0. ArgoCD pins go-git v5.14.0 upstream
#   ("DO NOT BUMP UNTIL go-git/go-git#1551 is fixed" — an SSH-push regression
#   that holmes never hits, since argocd is used as a read-only API client).
#   go-git v5.14.0 is vulnerable to CVE-2026-41506 (fixed 5.18.0),
#   CVE-2026-45022 (fixed 5.19.0), CVE-2026-45570/45571 (fixed 5.19.1);
#   go-billy v5.6.2 is vulnerable to CVE-2026-44973 (fixed 5.9.0).
#   v3.3.11 already ships otel/sdk 1.43.0 so the old otel replace was dropped.
#   Also replaced: grpc -> v1.82.1 (GHSA-hrxh-6v49-42gf), oras-go -> v2.6.2
#   (CVE-2026-50151/50163), mongo-driver -> v1.17.7 (CVE-2026-2303, Medium, >60d).
#   Revert to plain upstream binary when ArgoCD ships go-git >= 5.19.1 and
#   go-billy >= 5.9.0 (blocked on go-git/go-git#1551 upstream).
#
# Helm: built from v3.21.0 with containerd replaced to v1.7.33 (CVE-2026-53488
#   High + CVE-2026-47262; v3.21.0 ships v1.7.30), grpc replaced to v1.82.1
#   (GHSA-hrxh-6v49-42gf; v3.21.0 ships v1.80.0) and oras-go replaced to v2.6.2
#   (CVE-2026-50151/50163).
#   Revert to upstream binary when Helm releases a version built with
#   Go >= 1.26.5, containerd >= 1.7.33, grpc >= 1.82.1 and oras-go >= 2.6.2.
#
# kube-lineage: built with grpc replaced to v1.82.1 (GHSA-hrxh-6v49-42gf),
#   spdystream replaced to v0.5.1 (CVE-2026-35469), containerd replaced
#   to v1.7.33 (CVE-2026-53488), oras-go replaced to v2.6.2
#   (CVE-2026-50151/50163), and helm replaced to v3.20.2 (CVE-2026-35206).
#   robusta-dev/kube-lineage v2.2.5 ships with Go 1.24.13 + grpc 1.64.1 + spdystream 0.5.0.
#   Revert when kube-lineage releases a version built with Go >= 1.26.5,
#   grpc >= 1.82.1, spdystream >= 0.5.1, containerd >= 1.7.33, oras-go >= 2.6.2,
#   and helm >= 3.20.2.
#
# kubectl: rebuilt from kubernetes/kubernetes v1.36.3 with the x/net and
#   x/crypto replaces above. The official dl.k8s.io v1.36.3 binary is already
#   built with Go 1.26.5, but every 1.36.x release still vendors x/net v0.49.0,
#   which carries four High advisories (see the x/net note above) with no
#   upstream release that fixes them. Drop this build and go back to the
#   dl.k8s.io binary in the Dockerfile as soon as a kubectl release ships
#   x/net >= 0.56.0 (fixes every advisory the current replace clears,
#   including CVE-2026-46600) — check a candidate with:
#     go version -m <(curl -sL https://dl.k8s.io/release/<ver>/bin/linux/amd64/kubectl)
#
# Known-unfixable finding left in the image (no upstream fix exists):
#   GO-2026-5932  (x/crypto/openpgp) — the package is unmaintained by design.
#   Not reachable from any code path holmes uses; x/crypto has no release that
#   clears it because upstream will not fix openpgp.
#
# Prerequisites: Go 1.21+ installed locally (GOTOOLCHAIN auto-downloads the
#   pinned build toolchain below)
# Usage: ./scripts/build_go_binaries.sh

set -euo pipefail

# Pin the build toolchain: Go >= 1.26.5 fixes stdlib CVE-2026-39822 (High) and
# CVE-2026-42505, on top of the CVE-2026-42499/33814/39836/33811/39820 fixes in
# 1.26.3. Go auto-downloads it if the locally installed go is older (requires
# local go >= 1.21).
export GOTOOLCHAIN=go1.26.5

MIN_GO_VERSION="1.26.5"
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

# golang.org/x/net and golang.org/x/crypto carry the same advisories in every
# binary we ship, so every tool gets the same two replaces. Run from the module
# root of the tool being built.
apply_x_replaces() {
  echo "==> Pinning x/net to $X_NET_PATCHED_VERSION (CVE-2026-33814/25681/27136/39821) and x/crypto to $X_CRYPTO_PATCHED_VERSION (CVE-2026-39828/39829/39830/39831/39832/39835/42508/46595/46597)..."
  go mod edit -replace="golang.org/x/net=golang.org/x/net@$X_NET_PATCHED_VERSION"
  go mod edit -replace="golang.org/x/crypto=golang.org/x/crypto@$X_CRYPTO_PATCHED_VERSION"
}

assert_x_replaces() {
  assert_module_version "golang.org/x/net" "$X_NET_PATCHED_VERSION"
  assert_module_version "golang.org/x/crypto" "$X_CRYPTO_PATCHED_VERSION"
}

ARGOCD_VERSION=v3.3.11
ARGOCD_VERSION_NO_V="${ARGOCD_VERSION#v}"
GO_GIT_PATCHED_VERSION=v5.19.1
GO_BILLY_PATCHED_VERSION=v5.9.0
HELM_VERSION=v3.21.0
GRPC_PATCHED_VERSION=v1.82.1
KUBE_LINEAGE_VERSION=v2.2.5
KUBECTL_VERSION=v1.36.3
SPDYSTREAM_PATCHED_VERSION=v0.5.1
CONTAINERD_PATCHED_VERSION=v1.7.33
HELM_IN_LINEAGE_PATCHED_VERSION=v3.20.2
SLACK_GO_PATCHED_VERSION=v0.23.1
X_NET_PATCHED_VERSION=v0.57.0
X_CRYPTO_PATCHED_VERSION=v0.54.0
ORAS_GO_PATCHED_VERSION=v2.6.2
MONGO_DRIVER_PATCHED_VERSION=v1.17.7
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
apply_x_replaces
go mod edit -replace="google.golang.org/grpc=google.golang.org/grpc@$GRPC_PATCHED_VERSION"
go mod edit -replace="oras.land/oras-go/v2=oras.land/oras-go/v2@$ORAS_GO_PATCHED_VERSION"
# mongo-driver v1.17.6 has CVE-2026-2303 (Medium, published 2026-02-10); fixed in v1.17.7
go mod edit -replace="go.mongodb.org/mongo-driver=go.mongodb.org/mongo-driver@$MONGO_DRIVER_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/go-git/go-git/v5" "$GO_GIT_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/go-git/go-billy/v5" "$GO_BILLY_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/slack-go/slack" "$SLACK_GO_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_x_replaces
GOFLAGS=-mod=mod assert_module_version "google.golang.org/grpc" "$GRPC_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "oras.land/oras-go/v2" "$ORAS_GO_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "go.mongodb.org/mongo-driver" "$MONGO_DRIVER_PATCHED_VERSION"

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
echo "==> Pinning containerd to $CONTAINERD_PATCHED_VERSION (CVE-2026-53488/47262), grpc to $GRPC_PATCHED_VERSION (GHSA-hrxh-6v49-42gf), and oras-go to $ORAS_GO_PATCHED_VERSION (CVE-2026-50151/50163)..."
go mod edit -replace="github.com/containerd/containerd=github.com/containerd/containerd@$CONTAINERD_PATCHED_VERSION"
go mod edit -replace="google.golang.org/grpc=google.golang.org/grpc@$GRPC_PATCHED_VERSION"
go mod edit -replace="oras.land/oras-go/v2=oras.land/oras-go/v2@$ORAS_GO_PATCHED_VERSION"
apply_x_replaces
GOFLAGS=-mod=mod assert_module_version "github.com/containerd/containerd" "$CONTAINERD_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "google.golang.org/grpc" "$GRPC_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "oras.land/oras-go/v2" "$ORAS_GO_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_x_replaces

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

echo "==> Pinning grpc to $GRPC_PATCHED_VERSION (GHSA-hrxh-6v49-42gf), spdystream to $SPDYSTREAM_PATCHED_VERSION (CVE-2026-35469), containerd to $CONTAINERD_PATCHED_VERSION (CVE-2026-53488/47262), and oras-go to $ORAS_GO_PATCHED_VERSION (CVE-2026-50151/50163)..."
cd "$TMPDIR/kube-lineage"
go mod edit -replace="google.golang.org/grpc=google.golang.org/grpc@$GRPC_PATCHED_VERSION"
go mod edit -replace="github.com/moby/spdystream=github.com/moby/spdystream@$SPDYSTREAM_PATCHED_VERSION"
go mod edit -replace="github.com/containerd/containerd=github.com/containerd/containerd@$CONTAINERD_PATCHED_VERSION"
go mod edit -replace="oras.land/oras-go/v2=oras.land/oras-go/v2@$ORAS_GO_PATCHED_VERSION"
# embedded helm v3.19.0 has CVE-2026-35206 (Medium); fixed in v3.20.2
go mod edit -replace="helm.sh/helm/v3=helm.sh/helm/v3@$HELM_IN_LINEAGE_PATCHED_VERSION"
apply_x_replaces
GOFLAGS=-mod=mod assert_module_version "google.golang.org/grpc" "$GRPC_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/moby/spdystream" "$SPDYSTREAM_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "github.com/containerd/containerd" "$CONTAINERD_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "oras.land/oras-go/v2" "$ORAS_GO_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_module_version "helm.sh/helm/v3" "$HELM_IN_LINEAGE_PATCHED_VERSION"
GOFLAGS=-mod=mod assert_x_replaces

echo "==> Building kube-lineage for linux/amd64..."
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOFLAGS=-mod=mod go build \
  -o "$OUTDIR/amd64/kube-lineage" ./cmd/kube-lineage

echo "==> Building kube-lineage for linux/arm64..."
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 GOFLAGS=-mod=mod go build \
  -o "$OUTDIR/arm64/kube-lineage" ./cmd/kube-lineage

echo "==> Cloning Kubernetes $KUBECTL_VERSION..."
git clone --depth 1 --branch "$KUBECTL_VERSION" https://github.com/kubernetes/kubernetes.git "$TMPDIR/kubernetes"

cd "$TMPDIR/kubernetes"
# kubernetes/kubernetes ships a go.work covering the staging modules; -mod=mod
# is rejected in workspace mode, and the workspace would also shadow our
# replaces. GOWORK=off falls back to the root go.mod, whose own replace
# directives already point k8s.io/* at ./staging/src/k8s.io/*.
export GOWORK=off
apply_x_replaces
GOFLAGS=-mod=mod assert_x_replaces

# Mirror what k/k's hack/lib/version.sh stamps, so `kubectl version --client`
# reports the real release instead of an empty version.
KUBECTL_COMMIT="$(git rev-parse HEAD)"
KUBECTL_BUILD_DATE="$(git show -s --format=%cI HEAD)"
KUBECTL_LDFLAGS=""
for pkg in k8s.io/client-go/pkg/version k8s.io/component-base/version; do
  KUBECTL_LDFLAGS="$KUBECTL_LDFLAGS -X $pkg.gitVersion=$KUBECTL_VERSION"
  KUBECTL_LDFLAGS="$KUBECTL_LDFLAGS -X $pkg.gitCommit=$KUBECTL_COMMIT"
  KUBECTL_LDFLAGS="$KUBECTL_LDFLAGS -X $pkg.gitTreeState=clean"
  KUBECTL_LDFLAGS="$KUBECTL_LDFLAGS -X $pkg.buildDate=$KUBECTL_BUILD_DATE"
done

echo "==> Building kubectl for linux/amd64..."
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOFLAGS=-mod=mod go build \
  -ldflags "$KUBECTL_LDFLAGS" \
  -o "$OUTDIR/amd64/kubectl" ./cmd/kubectl

echo "==> Building kubectl for linux/arm64..."
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 GOFLAGS=-mod=mod go build \
  -ldflags "$KUBECTL_LDFLAGS" \
  -o "$OUTDIR/arm64/kubectl" ./cmd/kubectl

unset GOWORK

echo "==> Compressing binaries..."
for arch in amd64 arm64; do
  for f in argocd helm kube-lineage kubectl; do
    gzip -f "$OUTDIR/$arch/$f"
  done
done

echo "==> Generating SHA-256 checksums..."
if command -v sha256sum >/dev/null 2>&1; then
  SHA256_CMD="sha256sum"
else
  # macOS fallback
  SHA256_CMD="shasum -a 256"
fi
for arch in amd64 arm64; do
  (cd "$OUTDIR/$arch" && for f in argocd.gz helm.gz kube-lineage.gz kubectl.gz; do
    $SHA256_CMD "$f" > "$f.sha256"
  done)
done

echo ""
echo "Done! Compressed binaries:"
ls -lh "$OUTDIR/amd64/"
ls -lh "$OUTDIR/arm64/"

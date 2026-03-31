---
name: patch-cve
description: Use this skill when the user asks to "patch a CVE", "fix a CVE", "handle a CVE", "check a CVE", or discusses Docker image vulnerabilities, docker scout findings, or Go/dependency CVE remediation in the Dockerfile.
version: 0.1.0
---

# Patching CVEs in the Docker Image

This skill provides the workflow for patching CVEs found in the HolmesGPT Docker image. It covers identifying the source, checking if upstream fixes exist, applying patches, and validating the fix.

## Workflow Overview

```
1. Identify  →  2. Check upstream  →  3. Apply fix  →  4. Build  →  5. Validate  →  6. Cleanup
```

## Step 1: Identify the CVE Source

Run `docker scout cves` on the current image to understand which package/binary introduces the CVE:

```bash
docker scout cves <image> 2>&1 | grep -B20 -A5 "CVE-XXXX-XXXXX"
```

Key info to extract:
- **Affected package**: e.g., `stdlib 1.25.5` (Go standard library), a system package, or a Python dependency
- **Affected range**: e.g., `>=1.25.0-0, <1.25.7`
- **Fixed version**: e.g., `1.25.7`
- **Which binary**: identify which binary in the image pulls in the vulnerable dependency

## Step 2: Check if Upstream Fixes Exist

Before rebuilding from source, always check if a newer release of the affected tool already includes the fix.

### For Go-based tools (ArgoCD, Helm, etc.)

1. **Check the latest release version**:
   ```bash
   curl -s https://api.github.com/repos/<org>/<repo>/releases/latest | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])"
   ```

2. **Check Go version in that release** — download the binary and inspect:
   ```bash
   # For tools that show version info with Go version
   ./<tool> version
   ```
   Or check `go.mod` in the release tag:
   ```bash
   curl -s https://raw.githubusercontent.com/<org>/<repo>/<tag>/go.mod | head -5
   ```
   > **Important**: `go.mod` shows the minimum Go version, but the binary may be compiled with a newer Go toolchain. Always prefer checking the actual binary's version output.

3. **Check upstream PRs/issues** for the CVE — it may be merged to `master` but not yet in a release branch. Search:
   ```
   https://github.com/<org>/<repo>/pulls?q=CVE-XXXX-XXXXX
   ```

### For system packages (apt)

Check if a newer version is available:
```bash
apt-get update && apt-cache policy <package>
```

### For Python dependencies

Check if a newer version fixes the CVE:
```bash
pip index versions <package>
```

## Step 3: Apply the Fix

Choose the appropriate strategy based on findings:

### Strategy A: Upgrade to a fixed upstream release (preferred)

If a newer release includes the fix, simply update the version in the Dockerfile:
```dockerfile
ARG TOOL_VERSION=vX.Y.Z  # Updated from vX.Y.W to fix CVE-XXXX-XXXXX
```

### Strategy B: Rebuild from source with patched compiler/dependency

If upstream hasn't released a fix yet, rebuild the tool from source. For Go CVEs:

```dockerfile
FROM golang:<fixed-version> AS go-builder
RUN git clone --depth 1 --branch <version> <repo> /build/<tool>
WORKDIR /build/<tool>
RUN CGO_ENABLED=0 go build -o /go/bin/<tool> <build-path>
```

Or use the pre-built binary approach (see Strategy C).

### Strategy C: Pre-build binaries locally (fastest Docker builds)

For tools that are slow to compile, build locally for both architectures and commit to the repo:

1. Create a build script in `scripts/` (see `scripts/build_go_binaries.sh` for reference)
2. Build for both `linux/amd64` and `linux/arm64` using cross-compilation:
   ```bash
   CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o bin/<tool>/amd64/<tool> <build-path>
   CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -o bin/<tool>/arm64/<tool> <build-path>
   ```
3. Use `ARG TARGETARCH` in Dockerfile to select the right binary:
   ```dockerfile
   ARG TARGETARCH
   COPY bin/<tool>/${TARGETARCH}/<tool> /usr/local/bin/<tool>
   ```
4. Consider using Git LFS for large binaries (~200MB+)

### Strategy D: Pin system package version

For apt packages:
```dockerfile
RUN apt-get install -y <package>=<fixed-version>
```

## Step 4: Build the Docker Image

Build for the target platform:

```bash
docker build --platform linux/amd64 -t holmes-cve-test:latest .
```

> **Note**: For a quick validation, building for a single platform is sufficient. Multi-platform builds (`linux/amd64,linux/arm64`) can be done after the fix is confirmed.

## Step 5: Validate the Fix

This is the most critical step. **Never skip validation.**

### 5a: Check the specific CVE is gone

```bash
docker scout cves holmes-cve-test:latest 2>&1 | grep -A5 "CVE-XXXX-XXXXX"
```

- **If no output** → CVE is fixed
- **If still present** → the fix didn't work, go back to Step 2

### 5b: Run a full scan and review remaining CVEs

```bash
docker scout cves holmes-cve-test:latest 2>&1 | grep -E "✗ (CRITICAL|HIGH)"
```

Check if the fix introduced any new vulnerabilities or if there are other CVEs that should be addressed.

### 5c: Verify the tool still works

```bash
docker run --rm holmes-cve-test:latest <tool> version
docker run --rm holmes-cve-test:latest <tool> --help
```

## Step 6: Document and Clean Up

1. **Add comments in the Dockerfile** explaining the CVE patch and when it can be reverted:
   ```dockerfile
   # Rebuilt with Go 1.25.7 to fix CVE-2025-68121.
   # Revert when <tool> releases a version built with Go >= 1.25.7.
   ```

2. **Remove temporary workarounds** when upstream releases a fix. Keep track of what needs reverting.

3. **Clean up test images**:
   ```bash
   docker rmi holmes-cve-test:latest
   ```

## Common Patterns

### Go stdlib CVEs

These are the most common. A CVE in Go's standard library affects every Go binary in the image. Steps:
1. Identify all Go binaries in the image and their Go versions
2. Check if newer releases of each tool use a fixed Go version
3. Only rebuild the ones that still use a vulnerable Go version

### Multi-binary images

When the image contains multiple Go binaries (e.g., ArgoCD + Helm), each may use a different Go version. Check and fix each independently — don't assume fixing one fixes all.

## Checklist

- [ ] Identified which binary/package introduces the CVE
- [ ] Checked latest upstream releases for a fix
- [ ] Checked upstream PRs/issues for pending fixes
- [ ] Applied the fix (upgrade or rebuild)
- [ ] Built the Docker image successfully
- [ ] Ran `docker scout cves` and confirmed the CVE is gone
- [ ] Verified the patched tool still works (`--help`, `version`)
- [ ] Added Dockerfile comments explaining the patch and revert conditions
- [ ] Confirmed both architectures are supported (amd64 + arm64)

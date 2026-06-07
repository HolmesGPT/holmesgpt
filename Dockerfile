# Alpine-based holmes image (switched from Debian bookworm).
#
# Why Alpine: the Debian base carried HIGH/CRITICAL CVEs with no fix available
# in any Debian release — most notably the perl group (CVE-2026-42496 Critical
# + CVE-2026-42497/48959/48961/48962/9538), which is unremovable on Debian
# (perl-base is Essential, git depends on perl). Alpine's git does not depend
# on perl, so the entire group is absent, and Alpine typically patches lib
# CVEs (curl, expat, ncurses, ...) within days. Scan result at switch time:
# 0 CRITICAL / 0 HIGH / 2 MEDIUM (aiohttp, blocked on litellm >= 1.84).
#
# Notable differences vs the previous Debian image:
# - kubectl is a CVE-rebuilt static binary (see scripts/build_go_binaries.sh),
#   not an apt/apk package
# - msodbcsql18 (azure/sql toolset) is installed from Microsoft's Alpine .apk
#   and is AMD64-ONLY — Microsoft does not ship a working aarch64 Alpine
#   package, so the azure/sql toolset is unavailable on arm64
# - confluent-kafka has no musllinux wheels and is built from source against
#   Alpine's librdkafka-dev in the builder stage
# - argocd / helm / kube-lineage are the same CGO_ENABLED=0 static binaries
#   as before (libc-independent)

# Build stage
FROM python:3.11-alpine AS builder
ENV PATH="/root/.local/bin/:$PATH"

# build-base + librdkafka-dev: confluent-kafka builds from source on musl (no musllinux wheels)
# libffi-dev / openssl-dev: source builds for any remaining non-wheel deps
# unixodbc-dev: pyodbc headers (pyodbc itself ships musllinux wheels; kept for safety)
# librdkafka-dev comes from edge: confluent-kafka 2.14.0 requires librdkafka >= 2.14.0,
# but Alpine 3.23 stable ships 2.12.1. For production, pin the version or build
# librdkafka from source instead of tracking edge.
RUN apk add --no-cache \
    curl \
    git \
    gnupg \
    unzip \
    build-base \
    libffi-dev \
    openssl-dev \
    unixodbc-dev \
    cyrus-sasl-dev \
    && apk add --no-cache \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/main \
    librdkafka-dev

WORKDIR /

# Create and activate virtual environment.
# Upgrade wheel to >= 0.46.2 to fix CVE-2026-24049 (path traversal); the version
# pulled in by --upgrade-deps (0.45.1) is vulnerable.
# Upgrade pip to >= 26.1 to fix CVE-2026-3219/6357 (and 25.3 for CVE-2025-8869).
RUN python -m venv /venv --upgrade-deps && \
    /venv/bin/pip install --upgrade 'wheel>=0.46.2' 'pip>=26.1' && \
    . /venv/bin/activate

ENV VIRTUAL_ENV=/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Set up kubectl. Rebuilt from source with Go 1.26.4 (see
# scripts/build_go_binaries.sh) because every published kubectl release is
# compiled with a Go toolchain vulnerable to stdlib
# CVE-2026-42499/33814/39836/33811/39820/39823/39825/39826/42504.
# Revert to the dl.k8s.io binary when a release is built with Go >= 1.26.3.
ARG TARGETARCH
COPY bin/go-cve-rebuild/${TARGETARCH}/kubectl.gz /tmp/kubectl.gz
COPY bin/go-cve-rebuild/${TARGETARCH}/kubectl.gz.sha256 /tmp/kubectl.gz.sha256
RUN cd /tmp && sha256sum -c kubectl.gz.sha256 \
    && gunzip /tmp/kubectl.gz && mv /tmp/kubectl /usr/local/bin/kubectl && chmod +x /usr/local/bin/kubectl \
    && rm -f /tmp/kubectl.gz.sha256 \
    && kubectl version --client

# Download and signature-verify Microsoft ODBC driver for the final stage.
# Required for the azure/sql toolset. Microsoft only publishes a working Alpine
# apk for amd64 (the _arm64-named file contains arch x86_64 metadata and is
# uninstallable on aarch64), so the driver — and the azure/sql toolset — is
# amd64-only on Alpine. An empty marker file is created on other arches so the
# final-stage COPY succeeds.
ARG MSODBCSQL_VERSION=18.5.1.1-1
RUN if [ "${TARGETARCH}" = "amd64" ]; then \
    curl -fsSLO "https://download.microsoft.com/download/fae28b9a-d880-42fd-9b98-d779f0fdd77f/msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.apk" \
    && curl -fsSLO "https://download.microsoft.com/download/fae28b9a-d880-42fd-9b98-d779f0fdd77f/msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.sig" \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --import - \
    && gpg --verify "msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.sig" "msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.apk" \
    && mv "msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.apk" /msodbcsql18.apk \
    && rm -f "msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.sig"; \
    else \
    echo "msodbcsql18: no aarch64 Alpine package from Microsoft; azure/sql toolset unavailable on ${TARGETARCH}" \
    && touch /msodbcsql18.apk; \
    fi

# Set up kube-lineage / ArgoCD / Helm — identical CVE-patched static binaries
# as the Debian image (see Dockerfile + scripts/build_go_binaries.sh for the
# CVE list and revert conditions).
COPY bin/go-cve-rebuild/${TARGETARCH}/kube-lineage.gz /tmp/kube-lineage.gz
COPY bin/go-cve-rebuild/${TARGETARCH}/kube-lineage.gz.sha256 /tmp/kube-lineage.gz.sha256
RUN cd /tmp && sha256sum -c kube-lineage.gz.sha256 \
    && gunzip /tmp/kube-lineage.gz && mv /tmp/kube-lineage /kube-lineage && chmod +x /kube-lineage \
    && rm -f /tmp/kube-lineage.gz.sha256
RUN /kube-lineage --version

COPY bin/go-cve-rebuild/${TARGETARCH}/argocd.gz /tmp/argocd.gz
COPY bin/go-cve-rebuild/${TARGETARCH}/argocd.gz.sha256 /tmp/argocd.gz.sha256
RUN cd /tmp && sha256sum -c argocd.gz.sha256 \
    && gunzip /tmp/argocd.gz && mv /tmp/argocd /argocd && chmod +x /argocd \
    && rm -f /tmp/argocd.gz.sha256

COPY bin/go-cve-rebuild/${TARGETARCH}/helm.gz /tmp/helm.gz
COPY bin/go-cve-rebuild/${TARGETARCH}/helm.gz.sha256 /tmp/helm.gz.sha256
RUN cd /tmp && sha256sum -c helm.gz.sha256 \
    && gunzip /tmp/helm.gz && mv /tmp/helm /helm && chmod +x /helm \
    && rm -f /tmp/helm.gz.sha256

# Set up poetry
ARG PRIVATE_PACKAGE_REGISTRY="none"
RUN if [ "${PRIVATE_PACKAGE_REGISTRY}" != "none" ]; then \
    pip config set global.index-url "${PRIVATE_PACKAGE_REGISTRY}"; \
    fi \
    && pip install poetry
ARG POETRY_REQUESTS_TIMEOUT
RUN poetry config virtualenvs.create false
COPY pyproject.toml poetry.lock /
RUN if [ "${PRIVATE_PACKAGE_REGISTRY}" != "none" ]; then \
    poetry source add --priority=primary artifactory "${PRIVATE_PACKAGE_REGISTRY}"; \
    fi \
    && poetry install --no-interaction --no-ansi --no-root --with otel


# Final stage
FROM python:3.11-alpine

ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"
ENV PYTHONPATH=$PYTHONPATH:.:/app/holmes

WORKDIR /app

COPY --from=builder /venv /venv

# Runtime packages. Note: git on Alpine does NOT depend on perl — the Debian
# image's unfixable perl CVE group (CVE-2026-42496 et al.) is absent here.
# librdkafka: runtime lib for the source-built confluent-kafka binding.
# libstdc++ / libgcc: required by several compiled wheels.
# krb5-libs + unixodbc: runtime deps of msodbcsql18 (azure/sql toolset).
# apk upgrade picks up security fixes for base-image packages (e.g. xz-libs
# CVE-2026-34743) that Alpine has already shipped.
RUN apk upgrade --no-cache && apk add --no-cache \
    curl \
    jq \
    git \
    tcpdump \
    libstdc++ \
    libgcc \
    unixodbc \
    krb5-libs \
    && apk add --no-cache \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/main \
    librdkafka

# Microsoft ODBC for Azure SQL. Required for azure/sql toolset (amd64 only on
# Alpine — see builder stage). The apk was signature-verified in the builder
# stage; --allow-untrusted is needed because the package is not in an Alpine
# repository.
ARG TARGETARCH
COPY --from=builder /msodbcsql18.apk /tmp/msodbcsql18.apk
RUN if [ "${TARGETARCH}" = "amd64" ]; then \
    apk add --no-cache --allow-untrusted /tmp/msodbcsql18.apk; \
    fi && rm /tmp/msodbcsql18.apk

# Set up kubectl
COPY --from=builder /usr/local/bin/kubectl /usr/local/bin/kubectl
RUN kubectl version --client

# Set up kube lineage
COPY --from=builder /kube-lineage /usr/local/bin
RUN kube-lineage --version

# Set up ArgoCD
COPY --from=builder /argocd /usr/local/bin/argocd
RUN argocd --help

# Set up Helm
COPY --from=builder /helm /usr/local/bin/helm
RUN helm version

ARG AWS_DEFAULT_PROFILE
ARG AWS_DEFAULT_REGION
ARG AWS_PROFILE
ARG AWS_REGION

# Patching CVE-2024-32002
RUN git config --global core.symlinks false

# Upgrade wheel + setuptools in the base image's system Python to fix
# CVE-2026-24049 (wheel 0.45.1 path traversal), mirroring the Debian image,
# and pip to >= 26.1 (CVE-2026-3219/6357, CVE-2025-8869, CVE-2026-1703).
RUN /usr/local/bin/pip install --upgrade --no-cache-dir 'wheel>=0.46.2' 'setuptools>=80.0.0' 'pip>=26.1'

COPY ./experimental/ag-ui/server-agui.py /app/experimental/ag-ui/server-agui.py
COPY ./holmes /app/holmes
COPY ./server.py /app/server.py
COPY ./holmes_cli.py /app/holmes_cli.py

ENTRYPOINT ["python", "holmes_cli.py"]

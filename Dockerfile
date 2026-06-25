# Alpine-based image (switched from Debian bookworm to drop unfixable perl
# CVEs; Alpine git has no perl dependency).

# Build stage
FROM python:3.11-alpine AS builder
ENV PATH="/root/.local/bin/:$PATH"

# build-base/*-dev: source builds for deps without musllinux wheels (confluent-kafka, etc.).
# librdkafka-dev from edge: confluent-kafka 2.14.0 needs librdkafka >= 2.14.0; Alpine 3.23 ships 2.12.1.
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

# Create venv; upgrade wheel (CVE-2026-24049) and pip (CVE-2026-3219/6357, CVE-2025-8869).
RUN python -m venv /venv --upgrade-deps && \
    /venv/bin/pip install --upgrade 'wheel>=0.46.2' 'pip>=26.1' && \
    . /venv/bin/activate

ENV VIRTUAL_ENV=/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# kubectl: CVE-rebuilt from source with Go 1.26.4 (see scripts/build_go_binaries.sh);
# published releases use a Go toolchain with stdlib CVEs. Revert to dl.k8s.io when built with Go >= 1.26.3.
ARG TARGETARCH
COPY bin/go-cve-rebuild/${TARGETARCH}/kubectl.gz /tmp/kubectl.gz
COPY bin/go-cve-rebuild/${TARGETARCH}/kubectl.gz.sha256 /tmp/kubectl.gz.sha256
RUN cd /tmp && sha256sum -c kubectl.gz.sha256 \
    && gunzip /tmp/kubectl.gz && mv /tmp/kubectl /usr/local/bin/kubectl && chmod +x /usr/local/bin/kubectl \
    && rm -f /tmp/kubectl.gz.sha256 \
    && kubectl version --client

# Download + signature-verify Microsoft ODBC driver (azure/sql toolset) for the
# final stage. 18.6.2.1 ships genuine amd64 + aarch64 Alpine apks (the 18.5.x
# arm64-named apk was mislabeled x86_64 and uninstallable on aarch64).
ARG MSODBCSQL_VERSION=18.6.2.1-1
ARG MSODBCSQL_DOWNLOAD=https://download.microsoft.com/download/0b3d5518-b4a7-4a2b-afc7-7ee9e967f93c
RUN curl -fsSLO "${MSODBCSQL_DOWNLOAD}/msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.apk" \
    && curl -fsSLO "${MSODBCSQL_DOWNLOAD}/msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.sig" \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --import - \
    && gpg --verify "msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.sig" "msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.apk" \
    && mv "msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.apk" /msodbcsql18.apk \
    && rm -f "msodbcsql18_${MSODBCSQL_VERSION}_${TARGETARCH}.sig"

# kube-lineage / ArgoCD / Helm: CVE-patched static binaries (see scripts/build_go_binaries.sh).
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

# poetry itself is installed into /venv (virtualenvs.create=false) and the whole
# venv is copied to the final stage, so poetry's dep CacheControl drags in
# msgpack 1.1.2 (GHSA-6v7p-g79w-8964, use-after-free, HIGH). It's not in
# poetry.lock since it's a build-tool dep, so upgrade it directly.
# Revert when poetry's CacheControl ships msgpack >= 1.2.1.
RUN pip install --upgrade --no-cache-dir 'msgpack>=1.2.1'


# Final stage
FROM python:3.11-alpine

ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"
ENV PYTHONPATH=$PYTHONPATH:.:/app/holmes

WORKDIR /app

COPY --from=builder /venv /venv

# Runtime packages. librdkafka: confluent-kafka binding; libstdc++/libgcc:
# compiled wheels; krb5-libs/unixodbc: msodbcsql18 (azure/sql). apk upgrade
# pulls Alpine security fixes for base-image packages.
#
# bash + GNU coreutils/findutils/grep/gawk/sed/gzip: the bash toolset allowlist
# (default_lists.py) lets the LLM run grep/find/sort/date/head/stat/sed/zgrep/etc.
# with prefix-only validation (any flags pass). Alpine's busybox applets reject
# the GNU flags LLMs reflexively emit (grep -P, date -d "1 hour ago",
# find -printf, head -n -5, sed -i). These packages replace the busybox applets,
# restoring the GNU behavior the previous Debian image provided.
# bind-tools (dig/nslookup) + tcpdump: network/DNS troubleshooting, including the
# dig-based API-server reachability check in the kubernetes toolset.
RUN apk upgrade --no-cache && apk add --no-cache \
    git \
    bash \
    coreutils \
    findutils \
    grep \
    gawk \
    sed \
    gzip \
    bind-tools \
    tcpdump \
    libstdc++ \
    libgcc \
    unixodbc \
    krb5-libs \
    && apk add --no-cache \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/main \
    librdkafka \
    curl \
    jq

# Microsoft ODBC for Azure SQL. The apk was signature-verified in the builder
# stage; --allow-untrusted since it's not in an Alpine repo.
COPY --from=builder /msodbcsql18.apk /tmp/msodbcsql18.apk
RUN apk add --no-cache --allow-untrusted /tmp/msodbcsql18.apk && rm /tmp/msodbcsql18.apk

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

# Upgrade base-image system Python's wheel/setuptools/pip (CVE-2026-24049,
# CVE-2026-3219/6357, CVE-2025-8869, CVE-2026-1703).
RUN /usr/local/bin/pip install --upgrade --no-cache-dir 'wheel>=0.46.2' 'setuptools>=80.0.0' 'pip>=26.1'

COPY ./experimental/ag-ui/server-agui.py /app/experimental/ag-ui/server-agui.py
COPY ./holmes /app/holmes
COPY ./server.py /app/server.py
COPY ./holmes_cli.py /app/holmes_cli.py

ENTRYPOINT ["python", "holmes_cli.py"]

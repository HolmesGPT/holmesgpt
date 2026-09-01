# Alpine-based image (switched from Debian bookworm to drop unfixable perl
# CVEs; Alpine git has no perl dependency).

# Build-tooling version floors, shared across both build stages (re-declared
# with a bare `ARG` inside each stage that uses them). CVE fixes:
#   wheel >= 0.46.2     CVE-2026-24049
#   pip >= 26.1         CVE-2026-3219/6357, CVE-2025-8869
#   setuptools >= 80.0  CVE-2026-1703 (final-stage system Python only)
ARG PIP_MIN_VERSION=26.1
ARG WHEEL_MIN_VERSION=0.46.2
ARG SETUPTOOLS_MIN_VERSION=80.0.0

# Build stage
FROM python:3.11-alpine AS builder
ENV PATH="/root/.local/bin/:$PATH"

# build-base/*-dev: source builds for deps without musllinux wheels (confluent-kafka, etc.).
# librdkafka-dev from edge: confluent-kafka 2.14.0 needs librdkafka >= 2.14.0; Alpine 3.23 ships 2.12.1.
RUN apk add --no-cache \
    curl \
    git \
    unzip \
    build-base \
    libffi-dev \
    openssl-dev \
    cyrus-sasl-dev \
    && apk add --no-cache \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/main \
    librdkafka-dev

WORKDIR /

# Create venv; upgrade wheel + pip (CVE floors pinned via the ARGs at the top).
# The venv is selected via VIRTUAL_ENV/PATH below (sourcing activate in a RUN has
# no effect — the shell exits when the layer finishes).
ARG PIP_MIN_VERSION
ARG WHEEL_MIN_VERSION
RUN python -m venv /venv --upgrade-deps && \
    /venv/bin/pip install --upgrade "wheel>=${WHEEL_MIN_VERSION}" "pip>=${PIP_MIN_VERSION}"

ENV VIRTUAL_ENV=/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# kubectl: official release binary from dl.k8s.io, pulled with upstream SHA-256
# verification. v1.37.0 is built with Go 1.26.6 (stdlib CVE fixes incl. the
# CVE-2026-33818/56853/56858-56862 batch) and vendors x/net v0.57.0 / x/sys
# v0.47.0 / x/text v0.40.0, clearing the x/net finding set (CVE-2026-33814/
# 25681/27136/39821 High and friends) that the 1.36.x line carried and that
# once forced a from-source rebuild (see git history). When bumping, check the
# candidate's toolchain and x/* versions with:
#   go version -m <(curl -sL https://dl.k8s.io/release/<ver>/bin/linux/amd64/kubectl)
ARG TARGETARCH
ARG KUBECTL_VERSION=v1.37.0
RUN cd /tmp \
    && curl -fsSLO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
    && curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl.sha256" -o kubectl.sha256 \
    && echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c - \
    && mv kubectl /usr/local/bin/kubectl && chmod +x /usr/local/bin/kubectl \
    && rm -f kubectl.sha256 \
    && kubectl version --client

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


# Final stage
FROM python:3.11-alpine

ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"
ENV PYTHONPATH=$PYTHONPATH:.:/app/holmes

WORKDIR /app

COPY --from=builder /venv /venv

# Runtime packages. librdkafka: confluent-kafka binding; libstdc++/libgcc:
# compiled wheels. apk upgrade pulls Alpine security fixes for base-image
# packages.
#
# bash + GNU coreutils/findutils/grep/gzip: the bash toolset allowlist
# (default_lists.py) lets the LLM run grep/find/sort/date/head/stat/zgrep/etc.
# with prefix-only validation (any flags pass). Alpine's busybox applets reject
# the GNU flags LLMs reflexively emit (grep -P, date -d "1 hour ago",
# find -printf, head -n -5). These packages replace the busybox applets,
# restoring the GNU behavior the previous Debian image provided. gawk/sed are
# not in the default allowlist but are installed in GNU form so they behave
# correctly when a user adds them via the bash toolset's `allow` config.
# bind-tools (dig/nslookup) + tcpdump: network/DNS troubleshooting, including the
# dig-based API-server reachability check in the kubernetes toolset.
RUN apk upgrade --no-cache && apk add --no-cache \
    curl \
    jq \
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
    && apk add --no-cache \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community \
    --repository=https://dl-cdn.alpinelinux.org/alpine/edge/main \
    librdkafka

# Set up kubectl
COPY --from=builder /usr/local/bin/kubectl /usr/local/bin/kubectl
RUN kubectl version --client

# Set up flux
COPY --from=builder /usr/local/bin/flux /usr/local/bin/flux
RUN flux --version

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

# Upgrade base-image system Python's wheel/setuptools (CVE floors pinned via
# the ARGs at the top of this file), then remove pip from BOTH the system
# Python and the venv. The runtime never installs packages (the venv is fully
# populated at build time, holmes never shells out to pip, and pip is not in
# the bash toolset allowlist), while pip's own vendored dependencies
# (pip/_vendor/vendor.txt) ship msgpack 1.1.2 (GHSA-6v7p-g79w-8964, High) and
# setuptools 70.3.0 (CVE-2025-47273, High) with NO pip release that fixes them
# -- pip 26.2.1 is the latest and still vendors both. Removing pip removes the
# vendored copies. Restore with `python -m ensurepip` if ever needed at runtime.
ARG PIP_MIN_VERSION
ARG WHEEL_MIN_VERSION
ARG SETUPTOOLS_MIN_VERSION
RUN /usr/local/bin/pip install --upgrade --no-cache-dir \
    "wheel>=${WHEEL_MIN_VERSION}" "setuptools>=${SETUPTOOLS_MIN_VERSION}" "pip>=${PIP_MIN_VERSION}" \
    && /venv/bin/python -m pip uninstall -y pip \
    && /usr/local/bin/python -m pip uninstall -y pip

COPY ./experimental/ag-ui/server-agui.py /app/experimental/ag-ui/server-agui.py
COPY ./holmes /app/holmes
COPY ./server.py /app/server.py
COPY ./holmes_cli.py /app/holmes_cli.py

ENTRYPOINT ["python", "holmes_cli.py"]

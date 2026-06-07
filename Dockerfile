# Build stage
FROM python:3.11-slim-bookworm as builder
ENV PATH="/root/.local/bin/:$PATH"

RUN apt-get update \
    && apt-get install -y \
    curl \
    git \
    apt-transport-https \
    gnupg2 \
    build-essential \
    unzip \
    && apt-get purge -y --auto-remove \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /

# Create and activate virtual environment.
# Upgrade wheel to >= 0.46.2 to fix CVE-2026-24049 (path traversal); the version
# pulled in by --upgrade-deps (0.45.1) is vulnerable.
RUN python -m venv /venv --upgrade-deps && \
    /venv/bin/pip install --upgrade 'wheel>=0.46.2' && \
    . /venv/bin/activate

ENV VIRTUAL_ENV=/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Needed for kubectl
ENV VERIFY_CHECKSUM=true \
    VERIFY_SIGNATURES=true
RUN curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.34/deb/Release.key -o Release.key

# Set up kube-lineage (pre-built with Go 1.26.4 to fix stdlib
# CVE-2026-42499/33814/39836/33811/39820, grpc pinned to v1.79.3 to fix CVE-2026-33186,
# spdystream pinned to v0.5.1 to fix CVE-2026-35469, and containerd pinned to
# v1.7.32 to fix CVE-2026-46680).
# kube-lineage v2.2.5 ships with Go 1.24.13 + grpc 1.64.1 + spdystream 0.5.0 which are vulnerable.
# Rebuild with: ./scripts/build_go_binaries.sh
# Revert to upstream binary when kube-lineage releases a version with all three at fixed versions.
ARG TARGETARCH
COPY bin/go-cve-rebuild/${TARGETARCH}/kube-lineage.gz /tmp/kube-lineage.gz
COPY bin/go-cve-rebuild/${TARGETARCH}/kube-lineage.gz.sha256 /tmp/kube-lineage.gz.sha256
RUN cd /tmp && sha256sum -c kube-lineage.gz.sha256 \
    && gunzip /tmp/kube-lineage.gz && mv /tmp/kube-lineage /kube-lineage && chmod +x /kube-lineage \
    && rm -f /tmp/kube-lineage.gz.sha256
RUN /kube-lineage --version

# Set up ArgoCD (rebuilt from v3.3.11 source with Go 1.26.4 to fix stdlib
# CVE-2026-42499/33814/39836/33811/39820, go-git pinned to v5.19.1 to fix
# CVE-2026-41506/45022/45570/45571, and go-billy pinned to v5.9.0 to fix CVE-2026-44973.
# ArgoCD pins go-git v5.14.0 upstream (go-git/go-git#1551, an SSH-push regression
# that holmes never hits — argocd is used as a read-only API client only).
# v3.3.11 ships otel/sdk 1.43.0, so the previous otel replace was dropped.
# Rebuild with: ./scripts/build_go_binaries.sh
# Revert to plain upstream binary when ArgoCD ships go-git >= 5.19.1 and go-billy >= 5.9.0.
COPY bin/go-cve-rebuild/${TARGETARCH}/argocd.gz /tmp/argocd.gz
COPY bin/go-cve-rebuild/${TARGETARCH}/argocd.gz.sha256 /tmp/argocd.gz.sha256
RUN cd /tmp && sha256sum -c argocd.gz.sha256 \
    && gunzip /tmp/argocd.gz && mv /tmp/argocd /argocd && chmod +x /argocd \
    && rm -f /tmp/argocd.gz.sha256

# Set up Helm (v3.21.0 pre-built with Go 1.26.4 to fix stdlib
# CVE-2026-42499/33814/39836/33811/39820, and containerd pinned to v1.7.32 to fix
# CVE-2026-46680). Helm v3.21.0 already ships grpc v1.80.0, so the previous grpc
# replace (CVE-2026-33186) was dropped.
# Rebuild with: ./scripts/build_go_binaries.sh
# Revert to upstream binary when Helm releases a version built with Go >= 1.26.3
# and containerd >= 1.7.32.
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
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"
ENV PYTHONPATH=$PYTHONPATH:.:/app/holmes

WORKDIR /app

COPY --from=builder /venv /venv

# Known unfixable CVEs (acknowledge in scanner): perl CVE-2026-42496 (Critical),
# CVE-2026-42497/48959/48961/48962/9538 (High). No fixed perl exists in any Debian
# release (bookworm/trixie/sid all vulnerable as of 2026-06). perl-base is
# Debian-Essential and git Depends on perl, so the packages cannot be removed.
# Re-check https://security-tracker.debian.org/tracker/source-package/perl and
# remove this note once a fixed 5.36.0-7+deb12uX lands in bookworm-security.

# We're installing here libexpat1, to upgrade the package to include a fix to 3 high CVEs. CVE-2024-45491,CVE-2024-45490,CVE-2024-45492
# libgnutls30 is upgraded to >= 3.7.9-2+deb12u7 to fix CVE-2026-33845/42010 (Critical)
# and CVE-2026-33846/3833/42009 (High); the base image ships an older build.
RUN apt-get update \
    && apt-get install -y \
    curl \
    jq \
    git \
    apt-transport-https \
    gnupg2 \
    tcpdump \
    && apt-get purge -y --auto-remove \
    && apt-get install -y --no-install-recommends libexpat1 libgnutls30 \
    && rm -rf /var/lib/apt/lists/*

# Set up kubectl
COPY --from=builder /Release.key Release.key
RUN cat Release.key |  gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg \
    && echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.34/deb/ /' | tee /etc/apt/sources.list.d/kubernetes.list \
    && apt-get update
RUN apt-get install -y kubectl


# Microsoft ODBC for Azure SQL. Required for azure/sql toolset
RUN VERSION_ID=$(grep VERSION_ID /etc/os-release | cut -d '"' -f 2 | cut -d '.' -f 1) && \
    if ! echo "11 12" | grep -q "$VERSION_ID"; then \
        echo "Debian $VERSION_ID is not currently supported."; \
        exit 1; \
    fi && \
    curl -sSL -O https://packages.microsoft.com/config/debian/$VERSION_ID/packages-microsoft-prod.deb && \
    dpkg -i packages-microsoft-prod.deb && \
    rm packages-microsoft-prod.deb && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18 && \
    apt-get install -y libgssapi-krb5-2 && \
    rm -rf /var/lib/apt/lists/*


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

# Remove setuptools-65.5.1 installed from python:3.11-slim base image as fix for CVE-2024-6345 until image will be updated
RUN rm -rf /usr/local/lib/python3.11/site-packages/setuptools-65.5.1.dist-info
RUN rm -rf /usr/local/lib/python3.11/ensurepip/_bundled/setuptools-65.5.0-py3-none-any.whl

# Upgrade wheel + setuptools in the base image's system Python to fix CVE-2026-24049
# (wheel 0.45.1 path traversal). The venv at /venv was already upgraded in the builder stage,
# but the base image's system Python still has the vulnerable copy.
RUN /usr/local/bin/pip install --upgrade --no-cache-dir 'wheel>=0.46.2' 'setuptools>=80.0.0' \
    && rm -rf /usr/local/lib/python3.11/site-packages/wheel-0.45.1.dist-info

COPY ./experimental/ag-ui/server-agui.py /app/experimental/ag-ui/server-agui.py
COPY ./holmes /app/holmes
COPY ./server.py /app/server.py
COPY ./holmes_cli.py /app/holmes_cli.py

ENTRYPOINT ["python", "holmes_cli.py"]
#CMD ["http://docker.for.mac.localhost:9093"]

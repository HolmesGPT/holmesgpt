# Go builder stage - rebuild Go binaries with Go 1.25.7 to fix CVE-2025-68121
FROM golang:1.25.7-bookworm AS go-builder
ARG ARGOCD_VERSION=v3.3.2
ARG HELM_VERSION=v3.20.0
# Build ArgoCD CLI with patched Go
RUN git clone --depth 1 --branch ${ARGOCD_VERSION} https://github.com/argoproj/argo-cd.git /build/argo-cd
WORKDIR /build/argo-cd
RUN CGO_ENABLED=0 go build -ldflags "-X github.com/argoproj/argo-cd/v3/common.version=${ARGOCD_VERSION}" -o /go/bin/argocd ./cmd
# Build Helm with patched Go
RUN git clone --depth 1 --branch ${HELM_VERSION} https://github.com/helm/helm.git /build/helm
WORKDIR /build/helm
RUN CGO_ENABLED=0 go build -ldflags "-X helm.sh/helm/v3/internal/version.version=${HELM_VERSION}" -o /go/bin/helm ./cmd/helm

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

# Create and activate virtual environment
RUN python -m venv /venv --upgrade-deps && \
    . /venv/bin/activate

ENV VIRTUAL_ENV=/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Needed for kubectl
ENV VERIFY_CHECKSUM=true \
    VERIFY_SIGNATURES=true
RUN curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.34/deb/Release.key -o Release.key

# Set the architecture-specific kube lineage URLs
ARG KUBE_LINEAGE_ARM_URL=https://github.com/robusta-dev/kube-lineage/releases/download/v2.2.5/kube-lineage-macos-latest-v2.2.5
ARG KUBE_LINEAGE_AMD_URL=https://github.com/robusta-dev/kube-lineage/releases/download/v2.2.5/kube-lineage-ubuntu-latest-v2.2.5
# Define a build argument to identify the platform
ARG TARGETPLATFORM
# Conditional download based on the platform
RUN if [ "$TARGETPLATFORM" = "linux/arm64" ]; then \
    curl -L -o kube-lineage $KUBE_LINEAGE_ARM_URL; \
    elif [ "$TARGETPLATFORM" = "linux/amd64" ]; then \
    curl -L -o kube-lineage $KUBE_LINEAGE_AMD_URL; \
    else \
    echo "Unsupported platform: $TARGETPLATFORM"; exit 1; \
    fi
RUN chmod 777 kube-lineage
RUN ./kube-lineage --version

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
    && poetry install --no-interaction --no-ansi --no-root


# Final stage
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"
ENV PYTHONPATH=$PYTHONPATH:.:/app/holmes

WORKDIR /app

COPY --from=builder /venv /venv

# We're installing here libexpat1, to upgrade the package to include a fix to 3 high CVEs. CVE-2024-45491,CVE-2024-45490,CVE-2024-45492
RUN apt-get update \
    && apt-get install -y \
    curl \
    jq \
    git \
    apt-transport-https \
    gnupg2 \
    tcpdump \
    && apt-get purge -y --auto-remove \
    && apt-get install -y --no-install-recommends libexpat1 \
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

# Set up ArgoCD (built from source with patched Go for CVE-2025-68121)
COPY --from=go-builder /go/bin/argocd /usr/local/bin/argocd
RUN argocd --help

# Set up Helm (built from source with patched Go for CVE-2025-68121)
COPY --from=go-builder /go/bin/helm /usr/local/bin/helm
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

COPY ./experimental/ag-ui/server-agui.py /app/experimental/ag-ui/server-agui.py
COPY ./holmes /app/holmes
COPY ./server.py /app/server.py
COPY ./holmes_cli.py /app/holmes_cli.py

ENTRYPOINT ["python", "holmes_cli.py"]
#CMD ["http://docker.for.mac.localhost:9093"]

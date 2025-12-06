# gcloud MCP Server with Supergateway
# Uses Supergateway base image to convert STDIO MCP to HTTP/SSE API

FROM supercorp/supergateway:latest

# Switch to root to install dependencies
USER root

# Update npm to latest version for security
RUN npm install -g npm@latest

# Install gcloud SDK dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    bash \
    curl \
    gcc \
    python3-dev \
    musl-dev \
    linux-headers

# Install Google Cloud SDK
ENV PATH="/google-cloud-sdk/bin:${PATH}"
RUN curl -O https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-sdk-476.0.0-linux-x86_64.tar.gz && \
    tar xzf google-cloud-sdk-476.0.0-linux-x86_64.tar.gz && \
    rm google-cloud-sdk-476.0.0-linux-x86_64.tar.gz && \
    /google-cloud-sdk/install.sh --quiet --usage-reporting=false --path-update=true && \
    gcloud config set disable_usage_reporting true

# Pre-install the gcloud MCP package to cache it
RUN npx -y @google-cloud/gcloud-mcp --version || true

# Switch back to node user for security
USER node

# Expose the SSE API port
EXPOSE 8000

# Run Supergateway with gcloud-mcp as the stdio process
CMD ["--port", "8000", "--stdio", "npx -y @google-cloud/gcloud-mcp"]

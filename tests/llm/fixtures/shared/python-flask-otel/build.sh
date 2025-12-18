#!/bin/bash

# Build script for Python Flask OpenTelemetry test image

set -e

REGISTRY="me-west1-docker.pkg.dev/robusta-development/development"
IMAGE_NAME="python-flask-otel"
IMAGE_TAG="2.2"
FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
LOCAL_IMAGE="holmes-test/${IMAGE_NAME}:${IMAGE_TAG}"

echo "Building Docker image for x86_64: ${LOCAL_IMAGE}"
docker build --platform linux/amd64 -t "${LOCAL_IMAGE}" .
docker tag "${LOCAL_IMAGE}" "${FULL_IMAGE}"

echo ""
echo "✅ Image built successfully"
echo ""
echo "To push to registry (one-time):"
echo "  docker push ${FULL_IMAGE}"
echo ""
echo "To use in your Kubernetes manifests:"
echo "  image: ${FULL_IMAGE}"
echo "  imagePullPolicy: IfNotPresent"
echo ""
echo "And update the command to just run the app:"
echo "  command: [\"python\", \"/app/app.py\"]"
echo ""
echo "Note: The image already contains:"
echo "  - Flask 3.1.2"
echo "  - OpenTelemetry API & SDK 1.37.0"
echo "  - OpenTelemetry Flask instrumentation 0.58b0"
echo "  - OpenTelemetry OTLP gRPC exporter 1.37.0"
echo "  - Requests 2.32.3"
echo "  - Prometheus Client 0.21.1"

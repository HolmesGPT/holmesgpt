#!/bin/bash

set -e

IMAGE_NAME="us-central1-docker.pkg.dev/genuine-flight-317411/devel/kafka-lag-orders-app:v1"

echo "Building Docker image: $IMAGE_NAME"
docker build -t "$IMAGE_NAME" .

echo "Pushing Docker image: $IMAGE_NAME"
docker push "$IMAGE_NAME"

echo "Build and push completed successfully!"

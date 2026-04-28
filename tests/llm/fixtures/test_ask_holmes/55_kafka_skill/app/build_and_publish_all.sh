#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Building and publishing all Kafka apps from: $SCRIPT_DIR"

# Find all directories containing build_and_publish.sh scripts
APP_DIRS=$(find "$SCRIPT_DIR" -name "build_and_publish.sh" -type f -exec dirname {} \;)

if [ -z "$APP_DIRS" ]; then
    echo "No app directories with build_and_publish.sh found"
    exit 1
fi

echo "Found app directories:"
echo "$APP_DIRS"
echo ""

# Build and publish each app
for app_dir in $APP_DIRS; do
    app_name=$(basename "$app_dir")
    echo "=========================================="
    echo "Building $app_name"
    echo "=========================================="

    cd "$app_dir"

    if [ -x "./build_and_publish.sh" ]; then
        ./build_and_publish.sh
        if [ $? -eq 0 ]; then
            echo "✓ Successfully built and published $app_name"
        else
            echo "✗ Failed to build $app_name"
            exit 1
        fi
    else
        echo "✗ build_and_publish.sh not executable in $app_dir"
        exit 1
    fi

    echo ""
done

echo "=========================================="
echo "All apps built and published successfully!"
echo "=========================================="
echo ""
echo "To redeploy all apps:"
echo "kubectl rollout restart deployment/finance-app deployment/orders-app deployment/accounting-app deployment/invoices-app -n ask-holmes-namespace-55"

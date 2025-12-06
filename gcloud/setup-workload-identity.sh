#!/bin/bash

# Setup Workload Identity for gcloud MCP Server
# This script automates the complete Workload Identity setup process for GKE

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
NAMESPACE="default"
KSA_NAME="gcloud-mcp-sa"
GSA_NAME="holmes-gcloud-mcp"
ROLES_FILE="gcp-readonly-roles.txt"

# Function to print colored output
print_color() {
    echo -e "${2}${1}${NC}"
}

# Function to print usage
usage() {
    cat << EOF
Usage: $0 --project PROJECT --cluster CLUSTER_NAME --region REGION [OPTIONS]

Required arguments:
  --project PROJECT         GCP project ID
  --cluster CLUSTER_NAME    GKE cluster name
  --region REGION          GCP region (e.g., us-central1)

Optional arguments:
  --namespace NAMESPACE     Kubernetes namespace (default: default)
  --gsa-name NAME          GCP service account name (default: holmes-gcloud-mcp)
  --ksa-name NAME          K8s service account name (default: gcloud-mcp-sa)
  --roles-file FILE        Path to roles file (default: gcp-readonly-roles.txt)
  --skip-roles             Skip assigning IAM roles
  --help                   Show this help message

Example:
  $0 --project my-project --cluster my-cluster --region us-central1
EOF
    exit 1
}

# Parse command line arguments
SKIP_ROLES=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --project)
            PROJECT="$2"
            shift 2
            ;;
        --cluster)
            CLUSTER_NAME="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --gsa-name)
            GSA_NAME="$2"
            shift 2
            ;;
        --ksa-name)
            KSA_NAME="$2"
            shift 2
            ;;
        --roles-file)
            ROLES_FILE="$2"
            shift 2
            ;;
        --skip-roles)
            SKIP_ROLES=true
            shift
            ;;
        --help)
            usage
            ;;
        *)
            print_color "Unknown option: $1" "$RED"
            usage
            ;;
    esac
done

# Validate required arguments
if [ -z "$PROJECT" ] || [ -z "$CLUSTER_NAME" ] || [ -z "$REGION" ]; then
    print_color "Error: Missing required arguments" "$RED"
    usage
fi

print_color "=== gcloud MCP Workload Identity Setup ===" "$GREEN"
echo ""
print_color "Project: $PROJECT" "$YELLOW"
print_color "Cluster: $CLUSTER_NAME" "$YELLOW"
print_color "Region: $REGION" "$YELLOW"
print_color "Namespace: $NAMESPACE" "$YELLOW"
print_color "GCP Service Account: $GSA_NAME" "$YELLOW"
print_color "K8s Service Account: $KSA_NAME" "$YELLOW"
echo ""

# Check prerequisites
print_color "Checking prerequisites..." "$GREEN"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    print_color "Error: gcloud CLI is not installed" "$RED"
    exit 1
fi

# Check if kubectl is installed
if ! command -v kubectl &> /dev/null; then
    print_color "Error: kubectl is not installed" "$RED"
    exit 1
fi

# Set the project
print_color "Setting GCP project to $PROJECT..." "$GREEN"
gcloud config set project $PROJECT

# Get cluster credentials
print_color "Getting cluster credentials..." "$GREEN"
gcloud container clusters get-credentials $CLUSTER_NAME --region=$REGION --project=$PROJECT

# Check if namespace exists
if ! kubectl get namespace $NAMESPACE &> /dev/null; then
    print_color "Creating namespace $NAMESPACE..." "$GREEN"
    kubectl create namespace $NAMESPACE
fi

# Check if K8s service account already exists
if kubectl get serviceaccount $KSA_NAME -n $NAMESPACE &> /dev/null 2>&1; then
    print_color "Kubernetes service account $KSA_NAME already exists in namespace $NAMESPACE" "$YELLOW"
    read -p "Do you want to continue and update it? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_color "Aborted by user" "$RED"
        exit 1
    fi
fi

# Step 1: Enable Workload Identity on the cluster (if not already enabled)
print_color "Checking if Workload Identity is enabled on the cluster..." "$GREEN"
WORKLOAD_POOL=$(gcloud container clusters describe $CLUSTER_NAME \
    --region=$REGION \
    --format="value(workloadIdentityConfig.workloadPool)" 2>/dev/null)

if [ -z "$WORKLOAD_POOL" ]; then
    print_color "Workload Identity is not enabled on this cluster." "$YELLOW"
    print_color "Please enable it with:" "$YELLOW"
    echo "gcloud container clusters update $CLUSTER_NAME --region=$REGION --workload-pool=$PROJECT.svc.id.goog"
    exit 1
else
    print_color "Workload Identity is enabled: $WORKLOAD_POOL" "$GREEN"
fi

# Step 2: Create or get GCP Service Account
if gcloud iam service-accounts describe $GSA_NAME@$PROJECT.iam.gserviceaccount.com &> /dev/null; then
    print_color "GCP service account $GSA_NAME already exists" "$YELLOW"
else
    print_color "Creating GCP service account $GSA_NAME..." "$GREEN"
    gcloud iam service-accounts create $GSA_NAME \
        --display-name="Holmes gcloud MCP Service Account" \
        --description="Service account for Holmes gcloud MCP server with read-only access to GCP resources"
fi

# Step 3: Grant IAM roles to the service account
if [ "$SKIP_ROLES" = false ]; then
    if [ -f "$ROLES_FILE" ]; then
        print_color "Granting IAM roles from $ROLES_FILE..." "$GREEN"

        # Read roles from file and grant them
        while IFS= read -r role || [ -n "$role" ]; do
            # Skip comments and empty lines
            if [[ $role =~ ^#.*$ ]] || [ -z "$role" ]; then
                continue
            fi

            # Trim whitespace
            role=$(echo "$role" | xargs)

            print_color "  Granting $role..." "$NC"
            if gcloud projects add-iam-policy-binding $PROJECT \
                --member="serviceAccount:$GSA_NAME@$PROJECT.iam.gserviceaccount.com" \
                --role="$role" \
                --condition=None \
                --quiet &> /dev/null; then
                print_color "    ✓ Granted $role" "$GREEN"
            else
                print_color "    ⚠ Failed to grant $role (may already exist or be invalid)" "$YELLOW"
            fi
        done < "$ROLES_FILE"
    else
        print_color "Warning: Roles file $ROLES_FILE not found. Granting basic viewer role only." "$YELLOW"
        gcloud projects add-iam-policy-binding $PROJECT \
            --member="serviceAccount:$GSA_NAME@$PROJECT.iam.gserviceaccount.com" \
            --role="roles/viewer" \
            --condition=None \
            --quiet
    fi
else
    print_color "Skipping IAM role assignment (--skip-roles flag)" "$YELLOW"
fi

# Step 4: Create Kubernetes Service Account
print_color "Creating/updating Kubernetes service account $KSA_NAME..." "$GREEN"
kubectl create serviceaccount $KSA_NAME -n $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -

# Step 5: Annotate K8s Service Account with GCP Service Account
print_color "Annotating Kubernetes service account..." "$GREEN"
kubectl annotate serviceaccount $KSA_NAME \
    -n $NAMESPACE \
    iam.gke.io/gcp-service-account=$GSA_NAME@$PROJECT.iam.gserviceaccount.com \
    --overwrite

# Step 6: Create IAM policy binding
print_color "Creating IAM policy binding for Workload Identity..." "$GREEN"
gcloud iam service-accounts add-iam-policy-binding \
    $GSA_NAME@$PROJECT.iam.gserviceaccount.com \
    --role roles/iam.workloadIdentityUser \
    --member "serviceAccount:$PROJECT.svc.id.goog[$NAMESPACE/$KSA_NAME]" \
    --quiet

# Step 7: Verify the setup
print_color "" "$NC"
print_color "=== Setup Complete! ===" "$GREEN"
print_color "" "$NC"
print_color "Verifying Workload Identity setup..." "$GREEN"

# Check service account annotation
SA_ANNOTATION=$(kubectl get serviceaccount $KSA_NAME -n $NAMESPACE -o jsonpath='{.metadata.annotations.iam\.gke\.io/gcp-service-account}')
if [ "$SA_ANNOTATION" = "$GSA_NAME@$PROJECT.iam.gserviceaccount.com" ]; then
    print_color "✓ Kubernetes service account is correctly annotated" "$GREEN"
else
    print_color "✗ Kubernetes service account annotation is incorrect" "$RED"
fi

# Test with a pod
print_color "" "$NC"
print_color "Testing Workload Identity with a test pod..." "$GREEN"
cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: workload-identity-test
  namespace: $NAMESPACE
spec:
  serviceAccountName: $KSA_NAME
  containers:
  - name: test
    image: google/cloud-sdk:slim
    command: ['sleep', '10']
EOF

print_color "Waiting for test pod to be ready..." "$NC"
kubectl wait --for=condition=ready pod/workload-identity-test -n $NAMESPACE --timeout=30s &> /dev/null || true

# Check if pod can authenticate
print_color "Checking authentication..." "$NC"
if kubectl exec -n $NAMESPACE workload-identity-test -- gcloud auth list 2>/dev/null | grep -q "$GSA_NAME@$PROJECT.iam.gserviceaccount.com"; then
    print_color "✓ Workload Identity is working correctly!" "$GREEN"
    print_color "  The pod is authenticated as: $GSA_NAME@$PROJECT.iam.gserviceaccount.com" "$GREEN"
else
    print_color "⚠ Could not verify Workload Identity authentication" "$YELLOW"
fi

# Cleanup test pod
kubectl delete pod workload-identity-test -n $NAMESPACE --wait=false &> /dev/null

print_color "" "$NC"
print_color "=== Next Steps ===" "$GREEN"
print_color "1. Build and push the Docker image:" "$NC"
echo "   docker build -t gcr.io/$PROJECT/gcloud-mcp-server:latest ."
echo "   docker push gcr.io/$PROJECT/gcloud-mcp-server:latest"
print_color "" "$NC"
print_color "2. Update the deployment manifest:" "$NC"
echo "   - Set the image to: gcr.io/$PROJECT/gcloud-mcp-server:latest"
echo "   - Update the GCP_PROJECT in ConfigMap to: $PROJECT"
print_color "" "$NC"
print_color "3. Deploy the gcloud MCP server:" "$NC"
echo "   kubectl apply -f gcloud-mcp-deployment.yaml"
echo "   kubectl apply -f gcloud-mcp-service.yaml"
print_color "" "$NC"
print_color "4. Configure Holmes to use the MCP server" "$NC"
echo "   Add to Holmes config:"
echo "   mcp_servers:"
echo "     gcloud:"
echo "       url: http://gcloud-mcp-server.$NAMESPACE.svc.cluster.local:8000"
print_color "" "$NC"
print_color "Setup completed successfully!" "$GREEN"

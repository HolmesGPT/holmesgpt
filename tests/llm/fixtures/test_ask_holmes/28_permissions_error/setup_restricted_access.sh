#!/bin/bash
set -e

# Fixed path shared with test_case.yaml's KUBECONFIG test_env_var. Must NOT
# depend on $TMPDIR: the eval harness expands test_env_vars with
# os.path.expandvars, so an unset TMPDIR leaves a literal "$TMPDIR/..." path
# and the whole eval fails at setup (this is why this eval was red in CI).
TEMP_DIR="/tmp/holmes-test-28-permissions"

# Create the test namespace
kubectl create namespace 28-test --dry-run=client -o yaml | kubectl apply -f -

# Delete existing ClusterRoleBinding if it exists
kubectl delete clusterrolebinding restricted-holmes-binding-28 --ignore-not-found=true

# Create a restricted service account that cannot access secrets
kubectl apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: restricted-holmes-sa
  namespace: 28-test
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: restricted-holmes-role-28
rules:
# Allow access to most resources but explicitly deny clusterroles
- apiGroups: [""]
  resources: ["pods", "services", "configmaps", "events", "namespaces", "nodes", "persistentvolumes", "persistentvolumeclaims", "secrets"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["apps"]
  resources: ["deployments", "replicasets", "daemonsets", "statefulsets"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["batch"]
  resources: ["jobs", "cronjobs"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["networking.k8s.io"]
  resources: ["ingresses", "networkpolicies"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["rbac.authorization.k8s.io"]
  resources: ["roles", "rolebindings", "clusterrolebindings"]
  verbs: ["get", "list", "watch"]
# Note: No clusterroles access granted at all
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: restricted-holmes-binding-28
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: restricted-holmes-role-28
subjects:
- kind: ServiceAccount
  name: restricted-holmes-sa
  namespace: 28-test
EOF

# Wait for the service account to be created and have a token
sleep 2

# Get the service account token using TokenRequest API (K8s >= 1.24)
SA_TOKEN=$(kubectl create token restricted-holmes-sa -n 28-test --duration=1h)

# Verify token exists
if [ -z "$SA_TOKEN" ]; then
    echo "Error: Failed to obtain service account token"
    exit 1
fi

# Get cluster info
CLUSTER_NAME=$(kubectl config view --minify -o jsonpath='{.clusters[0].name}')
CLUSTER_SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
CLUSTER_CA=$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')

# Create predictable temporary directory for kubeconfig
mkdir -p "$TEMP_DIR"
KUBECONFIG_PATH="$TEMP_DIR/restricted-kubeconfig"

# Kubeconfig is created at predictable path for test to use

# Create a restricted kubeconfig file in temp directory
cat > "$KUBECONFIG_PATH" <<EOF
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: $CLUSTER_CA
    server: $CLUSTER_SERVER
  name: $CLUSTER_NAME
contexts:
- context:
    cluster: $CLUSTER_NAME
    user: restricted-holmes-sa
  name: restricted-context
current-context: restricted-context
users:
- name: restricted-holmes-sa
  user:
    token: $SA_TOKEN
EOF

# No output to prevent sensitive information leakage
# The kubeconfig path is stored in .restricted-kubeconfig-temp-dir for the test framework

# Create a test clusterrole to verify access is denied
kubectl apply -f - <<TESTEOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: test-clusterrole-28
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list"]
TESTEOF

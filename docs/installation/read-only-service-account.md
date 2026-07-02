# Read-Only Service Account

By default, Holmes's Kubernetes service account has full permissions. This guide explains how to restrict Holmes to read-only access.

## Why Read-Only Mode?

- **Prevent accidental modifications**: Holmes can investigate without modifying cluster resources
- **Comply with security policies**: Meet organizational requirements for read-only access
- **Prevent dangerous operations**: Prevent draining or restarting nodes

## What Works in Read-Only Mode

✅ Querying Kubernetes resources
✅ Analyzing logs and events
✅ Retrieving metrics from Prometheus
✅ Accessing Grafana dashboards
✅ Inspecting resource configurations
✅ Event and warning analysis

## Implementation: Creating a Read-Only Role

Create a YAML file (`holmes-readonly-role.yaml`):

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: holmes-readonly
rules:
  # Core API - read-only
  - apiGroups: [""]
    resources:
      - configmaps
      - events
      - namespaces
      - nodes
      - persistentvolumes
      - persistentvolumeclaims
      - pods
      - pods/log
      - pods/status
      - replicasets
      - replicationcontrollers
      - secrets
      - services
      - serviceaccounts
      - endpoints
    verbs: ["get", "list", "watch"]

  # Apps API - read-only
  - apiGroups: ["apps"]
    resources:
      - daemonsets
      - deployments
      - replicasets
      - statefulsets
    verbs: ["get", "list", "watch"]

  # Batch API - read-only
  - apiGroups: ["batch"]
    resources:
      - cronjobs
      - jobs
    verbs: ["get", "list", "watch"]

  # Autoscaling - read-only
  - apiGroups: ["autoscaling"]
    resources:
      - horizontalpodautoscalers
    verbs: ["get", "list", "watch"]

  # RBAC - read-only
  - apiGroups: ["rbac.authorization.k8s.io"]
    resources:
      - clusterroles
      - clusterrolebindings
      - roles
      - rolebindings
    verbs: ["get", "list", "watch"]

  # Networking - read-only
  - apiGroups: ["networking.k8s.io"]
    resources:
      - ingresses
      - networkpolicies
    verbs: ["get", "list", "watch"]

  # Events API - read-only
  - apiGroups: ["events.k8s.io"]
    resources:
      - events
    verbs: ["get", "list"]

  # API extensions - read-only
  - apiGroups: ["apiextensions.k8s.io"]
    resources:
      - customresourcedefinitions
    verbs: ["list", "get"]

  # Monitoring - read-only
  - apiGroups: ["monitoring.coreos.com"]
    resources:
      - prometheusrules
      - servicemonitors
      - podmonitors
      - alertmanagers
    verbs: ["get", "list", "watch"]

---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: holmes-readonly-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: holmes-readonly
subjects:
  - kind: ServiceAccount
    name: holmes
    namespace: holmes
```

Apply the configuration:

```bash
kubectl apply -f holmes-readonly-role.yaml
```

## Namespace-Scoped Alternative

For single-namespace access, use a **Role** instead:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: holmes-readonly
  namespace: monitoring
rules:
  - apiGroups: [""]
    resources: [pods, events, configmaps, services, endpoints]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: [deployments, replicasets, statefulsets]
    verbs: ["get", "list", "watch"]

---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: holmes-readonly-binding
  namespace: monitoring
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: holmes-readonly
subjects:
  - kind: ServiceAccount
    name: holmes
    namespace: holmes
```

## Verify the Configuration

```bash
kubectl get clusterrole holmes-readonly
kubectl describe clusterrole holmes-readonly
kubectl get clusterrolebinding holmes-readonly-binding
```

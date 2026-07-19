# Limiting Holmes to a Namespace

By default, the Holmes Helm chart creates a cluster-wide, read-only `ClusterRole` so Holmes can investigate resources across the whole cluster. This guide explains how to instead restrict Holmes to a single namespace.

## What to Expect

When Holmes is scoped to one namespace, it can only see resources in that namespace. Tools that query cluster-scoped resources (nodes, persistent volumes, storage classes, CRDs) or that list across all namespaces (`kubectl get ... --all-namespaces`, `kubectl top pods -A`) will return `forbidden` errors. Holmes keeps running and simply reports those errors, but investigations are limited to the target namespace.

## Configuration

Point Holmes at your own service account instead of the chart-managed cluster-wide one.

Set the following in your Helm values:

```yaml
# Don't let the chart create its cluster-wide ClusterRole/ClusterRoleBinding
createServiceAccount: false
# Use the namespace-scoped service account you create below
customServiceAccountName: holmes
```

Create the service account, `Role`, and `RoleBinding` (`holmes-namespace-scoped.yaml`). Replace `monitoring` with the namespace you want Holmes to investigate, and `holmes` with the namespace Holmes is installed in:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: holmes
  namespace: holmes

---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: holmes-namespace-scoped
  namespace: monitoring
rules:
  - apiGroups: [""]
    resources:
      - configmaps
      - endpoints
      - events
      - persistentvolumeclaims
      - pods
      - pods/log
      - pods/status
      - replicationcontrollers
      - services
      - serviceaccounts
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources:
      - daemonsets
      - deployments
      - replicasets
      - statefulsets
    verbs: ["get", "list", "watch"]
  - apiGroups: ["batch"]
    resources:
      - cronjobs
      - jobs
    verbs: ["get", "list", "watch"]
  - apiGroups: ["autoscaling"]
    resources:
      - horizontalpodautoscalers
    verbs: ["get", "list", "watch"]
  - apiGroups: ["networking.k8s.io"]
    resources:
      - ingresses
      - networkpolicies
    verbs: ["get", "list", "watch"]

---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: holmes-namespace-scoped
  namespace: monitoring
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: holmes-namespace-scoped
subjects:
  - kind: ServiceAccount
    name: holmes
    namespace: holmes
```

Apply it, then upgrade Holmes with the values above:

```bash
kubectl apply -f holmes-namespace-scoped.yaml
```

To grant access to more than one namespace, create an additional `Role` + `RoleBinding` in each namespace, all bound to the same `holmes` service account.

## Verify the Configuration

```bash
# Confirm the Role and binding exist in the target namespace
kubectl get role holmes-namespace-scoped -n monitoring
kubectl get rolebinding holmes-namespace-scoped -n monitoring

# Check what the service account can and cannot do
kubectl auth can-i list pods -n monitoring --as=system:serviceaccount:holmes:holmes
kubectl auth can-i list nodes --as=system:serviceaccount:holmes:holmes  # expected: no
```

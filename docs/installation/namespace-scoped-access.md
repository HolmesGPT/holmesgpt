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

Create the service account, `Role`, and `RoleBinding` (`holmes-namespace-scoped.yaml`).

!!! warning "The service account must live in the namespace where Holmes is deployed"
    Create the `ServiceAccount` in the **same namespace where the Holmes agent runs** (the release namespace, e.g. the namespace you pass to `helm install ... -n <namespace>`). If it is created in a different namespace, Holmes' pod won't be able to use it and the setup will not work. The `RoleBinding` in the target namespace then references this service account by its name **and** its namespace.

In the manifest below, replace:

- `monitoring` — the namespace you want Holmes to investigate
- `<HOLMES_NAMESPACE>` — the namespace where the Holmes agent is deployed (both places it appears)

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: holmes
  # Must match the namespace where the Holmes agent is deployed
  namespace: <HOLMES_NAMESPACE>

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
    # Must match the namespace where the Holmes agent is deployed
    namespace: <HOLMES_NAMESPACE>
```

Apply it, then upgrade Holmes with the values above:

```bash
kubectl apply -f holmes-namespace-scoped.yaml
```

To grant access to more than one namespace, create an additional `Role` + `RoleBinding` in each namespace, all bound to the same `holmes` service account (still referencing it in its own `<HOLMES_NAMESPACE>`).

## Verify the Configuration

```bash
# Confirm the Role and binding exist in the target namespace
kubectl get role holmes-namespace-scoped -n monitoring
kubectl get rolebinding holmes-namespace-scoped -n monitoring

# Check what the service account can and cannot do
# Replace <HOLMES_NAMESPACE> with the namespace where Holmes is deployed before running these commands.
# Format: system:serviceaccount:<HOLMES_NAMESPACE>:<serviceaccount-name>
kubectl auth can-i list pods -n monitoring --as=system:serviceaccount:<HOLMES_NAMESPACE>:holmes
kubectl auth can-i list nodes --as=system:serviceaccount:<HOLMES_NAMESPACE>:holmes  # expected: no
```

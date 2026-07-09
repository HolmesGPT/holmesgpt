# Limiting Holmes to a Namespace

By default, the Holmes Helm chart creates a cluster-wide, read-only `ClusterRole` so Holmes can investigate resources across the whole cluster. This guide explains how to instead restrict Holmes to specific namespaces.

## What to Expect

When Holmes is scoped to specific namespaces, it can only see resources in those namespaces. Tools that query cluster-scoped resources (nodes, persistent volumes, storage classes, CRDs) or that list across all namespaces (`kubectl get ... --all-namespaces`, `kubectl top pods -A`) will return `forbidden` errors. Holmes keeps running and simply reports those errors, but investigations are limited to the target namespaces.

## Configuration

There are two approaches to scope Holmes to specific namespaces:

1. **Helm Configuration** (recommended) — Let the Helm chart automatically create the necessary RBAC resources
2. **Manual YAML** — Create custom `Role` and `RoleBinding` resources yourself

### Approach 1: Helm Configuration (Recommended)

Use the `roleBindingNamespaces` value in your Helm chart to automatically create namespaced `RoleBindings` instead of a cluster-wide `ClusterRoleBinding`.

Set the following in your Helm `values.yaml`:

```yaml
# List the namespaces where Holmes should have access
roleBindingNamespaces:
  - monitoring
  - production
  - staging
```

That's it! The Helm chart will:
- Keep the cluster-wide `ClusterRole` for consistency
- Create `RoleBinding` resources in each specified namespace, binding Holmes' `ClusterRole` to those namespace-scoped service accounts
- Automatically set `createServiceAccount: true` so Holmes has the necessary permissions in those namespaces

When you upgrade Holmes, it will automatically create the necessary `RoleBindings` in each namespace listed.

**To grant access to additional namespaces** later, simply add them to the list and re-run `helm upgrade`:

```bash
helm upgrade holmes robusta/holmes -f values.yaml
```

**To verify the configuration:**

```bash
# Check RoleBindings in your target namespaces
kubectl get rolebinding -n monitoring
kubectl get rolebinding -n production
kubectl get rolebinding -n staging

# Confirm Holmes can access resources in these namespaces
kubectl auth can-i list pods -n monitoring --as=system:serviceaccount:holmes:holmes
kubectl auth can-i list pods -n production --as=system:serviceaccount:holmes:holmes

# Confirm Holmes cannot access other namespaces
kubectl auth can-i list pods -n default --as=system:serviceaccount:holmes:holmes  # expected: no
```

### Approach 2: Manual YAML Configuration

Use this approach if you need to customize the permissions or use a different service account name.

First, set the following in your Helm `values.yaml` to skip the chart-managed RBAC resources:

```yaml
# Don't let the chart create its cluster-wide ClusterRole/ClusterRoleBinding
createServiceAccount: false
# Use the namespace-scoped service account you create below
customServiceAccountName: holmes
```

Then, create the service account, `Role`, and `RoleBinding` resources manually. Create a file called `holmes-namespace-scoped.yaml`. Replace `monitoring` with the namespace you want Holmes to investigate, and `holmes` with the namespace Holmes is installed in:

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

Apply the resources:

```bash
kubectl apply -f holmes-namespace-scoped.yaml
```

Then upgrade Holmes with the values above:

```bash
helm upgrade holmes robusta/holmes -f values.yaml
```

To grant access to more than one namespace, create an additional `Role` + `RoleBinding` in each namespace, all bound to the same `holmes` service account. Or, switch to **Approach 1** for easier multi-namespace management.

## Comparison

| Aspect | Helm Configuration | Manual YAML |
|--------|-------------------|------------|
| **Setup** | Add `roleBindingNamespaces` to values | Create custom YAML files |
| **Namespaces** | Specify in list, auto-creates RoleBindings | Create Role+RoleBinding per namespace |
| **Updates** | `helm upgrade` manages all namespaces | Manual updates to YAML |
| **Customization** | Uses standard ClusterRole | Can customize permissions |
| **Recommended for** | Most deployments | Advanced custom permission needs |

## Verify the Configuration

For **Helm Configuration** approach:

```bash
# Confirm RoleBindings are in each namespace
kubectl get rolebinding -n monitoring
kubectl get rolebinding -n production

# Check what the service account can and cannot do
kubectl auth can-i list pods -n monitoring --as=system:serviceaccount:holmes:holmes
kubectl auth can-i list nodes --as=system:serviceaccount:holmes:holmes  # expected: no
```

For **Manual YAML** approach:

```bash
# Confirm the Role and binding exist in the target namespace
kubectl get role holmes-namespace-scoped -n monitoring
kubectl get rolebinding holmes-namespace-scoped -n monitoring

# Check what the service account can and cannot do
kubectl auth can-i list pods -n monitoring --as=system:serviceaccount:holmes:holmes
kubectl auth can-i list nodes --as=system:serviceaccount:holmes:holmes  # expected: no
```

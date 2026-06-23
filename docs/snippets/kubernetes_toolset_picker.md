!!! tip "Which Kubernetes toolset should I use?"
    Holmes has three Kubernetes integrations. Most users only need the first:

    - **[Kubernetes (built-in)](kubernetes.md)** — *default for most users.* Read-only access to cluster resources via `kubectl`, authenticated with the pod's ServiceAccount in-cluster or your local kubeconfig for CLI. No extra deployment.
    - **[Kubernetes (MCP)](kubernetes-mcp.md)** — *use when you need OAuth/OIDC authentication* (e.g. AKS with Microsoft Entra ID, or per-user RBAC enforced by your identity provider). Replaces the built-in toolset.
    - **[Kubernetes Remediation (MCP)](kubernetes-remediation-mcp.md)** — *add on top of either of the above when you want Holmes to perform write actions* (restart, scale, drain, patch, etc.). Complements the read-only toolsets rather than replacing them.

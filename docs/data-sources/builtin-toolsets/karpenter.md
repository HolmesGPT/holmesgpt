# Karpenter

This toolset lets HolmesGPT investigate [Karpenter](https://karpenter.sh/) node autoscaling — why pods stay `Pending`, why a `NodeClaim` never becomes a real `Node`, and why Karpenter is disrupting (consolidating, expiring, drifting) nodes you didn't expect.

The integration is split into two toolsets:

- **`karpenter/core`** — cloud-agnostic tools: NodePools, NodeClaims, disruption events, controller logs, pending pods. Works on any cluster running upstream Karpenter (AWS, Azure, GCP, on-prem).
- **`karpenter/aws`** — AWS-specific tools for inspecting `EC2NodeClass` resources (AMI selectors, subnets, security groups, IAM instance profile, userdata). Requires the Karpenter AWS provider CRDs.

## Prerequisites

- `kubectl` configured against a cluster where Karpenter is installed
- Karpenter CRDs present in the cluster (`nodepools.karpenter.sh`) for `karpenter/core`
- Karpenter AWS provider CRDs (`ec2nodeclasses.karpenter.k8s.aws`) for `karpenter/aws`

Each toolset runs its own CRD health check on startup. On AKS/GKE clusters, `karpenter/aws` stays disabled automatically when the AWS provider is not installed.

By default `karpenter_controller_logs` reads from the `karpenter` namespace (the upstream Helm chart default). On EKS managed add-on installs — where Karpenter runs in `kube-system` — Holmes will pass `ns: kube-system` to the tool.

## Configuration

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**:

    <!-- markdownlint-disable-next-line MD046 -->
    ```yaml
    toolsets:
        karpenter/core:
            enabled: true
        karpenter/aws:
            enabled: true  # omit on non-AWS clusters
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "Why are my pending pods not triggering a new node?"
    ```

## Common Use Cases

```bash
holmes ask "Why are my pending pods not being scheduled onto a new Karpenter node?"
```

```bash
holmes ask "I have a NodeClaim stuck in Unknown — what's blocking it?"
```

```bash
holmes ask "Which NodePool would satisfy the scheduling constraints of the pod web-api-abc in the default namespace?"
```

```bash
holmes ask "Why did Karpenter terminate node ip-10-0-12-34.ec2.internal earlier today?"
```

```bash
holmes ask "Is my EC2NodeClass default picking up the correct subnets and security groups?"
```

# AWS (MCP)

Holmes connects to the hosted [AWS MCP Server](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/mcp-server.html), which gives it **read-only access to any AWS API** you permit via IAM: EC2, RDS, ELB, CloudWatch, CloudTrail, S3, Lambda, Cost Explorer and hundreds more. Holmes signs every request with its own AWS credentials (SigV4), so nothing else is deployed and every call shows up in CloudTrail.

## Overview

- **Helm users**: Holmes authenticates with IRSA (IAM Roles for Service Accounts) on its own service account
- **CLI users**: Holmes uses your local AWS credentials (`~/.aws`, environment variables or a named profile)

!!! warning "Migrating from the AWS API MCP server pod"
    Earlier Holmes versions deployed `aws-api-mcp-server` as a separate pod with the `aws-api-mcp-sa` service account. That server is [deprecated by AWS](https://github.com/awslabs/mcp/blob/main/src/aws-api-mcp-server/MIGRATION.md) and the pod is no longer rendered. Point the IAM role's trust policy at the **Holmes service account** instead (`system:serviceaccount:NAMESPACE:RELEASE-holmes-service-account`, `robusta-holmes-service-account` for the Robusta chart) and annotate it as shown below. `mcpAddons.aws.serviceAccount`, `image`, `networkPolicy` and `resources` are ignored. Export your current values with `helm get values RELEASE -n NAMESPACE > values.yaml` before upgrading.

## Single Account Setup

### Step 1: Set Up IAM Permissions

!!! tip "CLI users can skip this step"
    Holmes CLI uses your local AWS credentials directly. Skip to [Step 2](#step-2-deploy-aws-mcp).

Holmes needs an IAM role with read-only permissions that its Kubernetes service account can assume. We provide a default IAM policy that works for most users; restrict it if needed. Read-only access is enforced by this policy - the MCP server itself exposes write operations too, so attach only read permissions.

=== "Helper Scripts (recommended)"

    ```bash
    # Download the scripts
    curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/enable-oidc-provider.sh
    curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/setup-irsa.sh
    curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/aws-mcp-iam-policy.json
    chmod +x enable-oidc-provider.sh setup-irsa.sh

    # 1. Enable OIDC provider for your EKS cluster (if not already enabled)
    ./enable-oidc-provider.sh --cluster-name YOUR_CLUSTER_NAME --region YOUR_REGION

    # 2. Create the IAM policy and role, trusting the Holmes service account
    # --namespace must match the namespace where Holmes is deployed
    # --service-account is RELEASE-holmes-service-account (Holmes chart) or robusta-holmes-service-account (Robusta chart)
    ./setup-irsa.sh --cluster-name YOUR_CLUSTER_NAME --region YOUR_REGION --namespace YOUR_NAMESPACE --service-account holmes-holmes-service-account
    ```

    The script outputs the role ARN at the end. Save it for Step 2:
    ```
    Role ARN: arn:aws:iam::123456789012:role/holmes-aws-mcp-role-YOUR_CLUSTER_NAME
    ```

=== "Manual Setup"

    **Create the IAM policy:**

    ```bash
    # Download the policy
    curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/aws-mcp-iam-policy.json

    # Create the IAM policy
    aws iam create-policy \
      --policy-name HolmesMCPReadOnly \
      --policy-document file://aws-mcp-iam-policy.json
    ```

    The complete policy is available on GitHub: [aws-mcp-iam-policy.json](https://github.com/robusta-dev/holmes-mcp-integrations/blob/master/servers/aws/aws-mcp-iam-policy.json)

    **Create the IAM role:**

    Service account names by installation method:

    - Holmes Helm Chart: `RELEASE-holmes-service-account` (for example `holmes-holmes-service-account`)
    - Robusta Helm Chart: `robusta-holmes-service-account`

    ```bash
    # Get your OIDC provider URL
    OIDC_PROVIDER=$(aws eks describe-cluster --name YOUR_CLUSTER_NAME --query "cluster.identity.oidc.issuer" --output text | sed -e "s/^https:\/\///")

    # Create the trust policy
    cat > trust-policy.json << EOF
    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Principal": {
            "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/${OIDC_PROVIDER}"
          },
          "Action": "sts:AssumeRoleWithWebIdentity",
          "Condition": {
            "StringEquals": {
              "${OIDC_PROVIDER}:aud": "sts.amazonaws.com",
              "${OIDC_PROVIDER}:sub": "system:serviceaccount:YOUR_NAMESPACE:SERVICE_ACCOUNT_NAME"
            }
          }
        }
      ]
    }
    EOF

    # Create the role
    aws iam create-role \
      --role-name HolmesMCPRole \
      --assume-role-policy-document file://trust-policy.json

    # Attach the policy to the role
    aws iam attach-role-policy \
      --role-name HolmesMCPRole \
      --policy-arn arn:aws:iam::ACCOUNT_ID:policy/HolmesMCPReadOnly
    ```

    **Note the role ARN** - you'll need it in the next step: `arn:aws:iam::ACCOUNT_ID:role/HolmesMCPRole`

### Step 2: Deploy AWS MCP

Choose your installation method.

=== "Holmes CLI"

    **Prerequisites:** working AWS credentials (`aws sts get-caller-identity` should succeed). Set `AWS_PROFILE` or `profile` below to use a named profile, including `aws sso login` / `aws login` profiles.

    **Configure Holmes CLI**

    Add to `~/.holmes/config.yaml`:

    ```yaml
    mcp_servers:
      aws_api:
        description: "AWS MCP Server - query any AWS API for investigating infrastructure issues"
        config:
          mode: aws
          region: "us-east-1"   # Change to your region
          # profile: "your-profile"  # Optional: AWS profile from ~/.aws/config
        llm_instructions: |
          IMPORTANT: When investigating issues related to AWS resources or Kubernetes workloads running on AWS, you MUST actively use this MCP server to gather data rather than providing manual instructions to the user.

          Use `aws___run_script` to query AWS: `data = await call_boto3(service_name='ec2', operation_name='DescribeInstances', params={...})` (async, keyword-only, PascalCase operation names), then end the script with a `result` dict as the final expression. Batch related lookups into one script and return only the fields you need.

          ## Investigation Principles

          1. First, gather current state and configuration using AWS APIs
          2. Check CloudTrail for recent changes that might have caused the issue
          3. Collect metrics and logs from CloudWatch if available
          4. Analyze all gathered data before providing conclusions

          **Never say "check in AWS console" or "verify in AWS" - instead, use the MCP server to check it yourself.**
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    ??? info "Alternative: AWS's stdio proxy"
        If you prefer AWS's own client, run [mcp-proxy-for-aws](https://github.com/aws/mcp-proxy-for-aws) as a stdio subprocess instead. It needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and signs with the same local credentials:

        ```yaml
        mcp_servers:
          aws_api:
            description: "AWS MCP Server - query any AWS API for investigating infrastructure issues"
            config:
              mode: stdio
              command: "uvx"
              args: ["mcp-proxy-for-aws-cli@1.7.0", "https://aws-mcp.us-east-1.api.aws/mcp"]
              env:
                AWS_REGION: "us-east-1"
                # AWS_PROFILE: "your-profile"
        ```

        Do not add the proxy's `--read-only` flag: it hides `aws___run_script`, the only tool that calls AWS APIs.

    **Test it**

    ```bash
    holmes ask "List my EC2 instances and their current status"
    ```

=== "Holmes Helm Chart"

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`, annotating the Holmes service account with the IAM role from Step 1:

    ```yaml
    serviceAccount:
      annotations:
        eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT_ID:role/HolmesMCPRole"

    mcpAddons:
      aws:
        enabled: true
        config:
          region: "us-east-1"  # Change to your AWS region
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`, annotating the Holmes service account with the IAM role from Step 1:

    ```yaml
    holmes:
      serviceAccount:
        annotations:
          eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT_ID:role/HolmesMCPRole"

      mcpAddons:
        aws:
          enabled: true
          config:
            region: "us-east-1"  # Change to your AWS region
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

**Step 3: Verify the deployment**

In Kubernetes, Holmes logs the IAM identity it authenticated with:

```bash
kubectl logs -l app=holmes | grep "aws_api"
```

## Multi-Account Setup

If you have a single Holmes agent that needs to query AWS resources across multiple accounts (e.g., a staging account and a production account), use this setup instead of the single account setup above.

!!! note "Alternative: One agent per account"
    You can also deploy a separate Holmes agent in each AWS account. If you use [Robusta](https://home.robusta.dev/), you can manage a fleet of agents across environments from a single pane of glass. The multi-account setup below is for when you want **one agent** to reach into **multiple accounts**.

??? info "How It Works"
    When multi-account mode is enabled:

    1. Holmes gets one MCP server per account (`aws_dev`, `aws_prod`, ...), each signing with its own AWS profile
    2. The chart renders an AWS config file where every profile assumes the account's IAM role with the pod's projected service account token (`AssumeRoleWithWebIdentity`); the AWS SDK refreshes the credentials itself
    3. The IAM roles in the target accounts trust the Holmes service account of your cluster

### Step 1: Download the Setup Script

```bash
# Download the setup script
curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/scripts/setup-multi-account-iam.sh
chmod +x setup-multi-account-iam.sh

# Download example configuration file and the read-only policy
curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/scripts/multi-cluster-config-example.yaml
curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/aws-mcp-iam-policy.json
```

??? info "What the Script Does"
    For each target account, the script:

    1. **Creates OIDC Providers**: Sets up OIDC providers for each cluster in the target account
    2. **Creates IAM Role**: Creates a role with trust policy allowing `assume_role_with_web_identity` from all configured clusters
    3. **Attaches Permissions**: Applies the read-only permissions policy to the role
    4. **Generates `holmes_config.yaml`**: The Helm values for Step 4

### Step 2: Create Configuration File

Edit `multi-cluster-config-example.yaml` with your cluster and account details. `kubernetes.service_account` must be the Holmes service account (`RELEASE-holmes-service-account`, or `robusta-holmes-service-account` for the Robusta chart).

??? example "Example Configuration"
    ```yaml
    clusters:
      - name: prod-cluster
        region: us-east-1
        account_id: "111111111111"
        oidc_issuer_id: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA

      - name: staging-cluster
        region: us-west-2
        account_id: "111111111111"
        oidc_issuer_id: BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB

    kubernetes:
      namespace: YOUR_NAMESPACE  # Must match the namespace where Holmes is deployed
      service_account: holmes-holmes-service-account

    iam:
      role_name: EKSMultiAccountMCPRole
      policy_name: MCPReadOnlyPolicy
      session_duration: 3600

    target_accounts:
      - profile: dev
        account_id: "111111111111"
        description: "Development account"

      - profile: prod
        account_id: "222222222222"
        description: "Production account"
    ```

To get the `oidc_issuer_id` for each cluster:

```bash
# Get the OIDC issuer URL for your cluster
aws eks describe-cluster --name <cluster-name> --query "cluster.identity.oidc.issuer" --output text
# Output: https://oidc.eks.us-east-1.amazonaws.com/id/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA

# The issuer ID is the last part of the URL (after /id/)
```

### Step 3: Run the Setup

```bash
# Basic usage (uses default config: multi-cluster-config.yaml)
./setup-multi-account-iam.sh setup

# With custom config file
./setup-multi-account-iam.sh setup my-config.yaml

# With custom permissions file
./setup-multi-account-iam.sh setup my-config.yaml ./aws-mcp-iam-policy.json

# Verify the setup
./setup-multi-account-iam.sh verify my-config.yaml

# Teardown (removes all created resources)
./setup-multi-account-iam.sh teardown my-config.yaml
```

### Step 4: Configure Helm Chart

Once the IAM roles are set up, configure the Helm chart to enable multi-account mode. The setup script writes this block to `holmes_config.yaml` for you. CLI users instead add one `mode: aws` server per account to `~/.holmes/config.yaml`, each with its own `profile` from `~/.aws/config`.

=== "Holmes Helm Chart"

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    mcpAddons:
      aws:
        enabled: true
        config:
          region: "us-east-1"  # Default region for all accounts

        multiAccount:
          enabled: true
          profiles:
            dev:
              account_id: "111111111111"
              role_arn: "arn:aws:iam::111111111111:role/EKSMultiAccountMCPRole"
              description: "Development account"   # optional, shown to the LLM as the server description
              region: "us-east-1"                  # optional, defaults to config.region
            prod:
              account_id: "222222222222"
              role_arn: "arn:aws:iam::222222222222:role/EKSMultiAccountMCPRole"
              description: "Production account"
          llm_account_descriptions: |
            aws_dev is the development account and contains the development resources.
            aws_prod is the production account and contains the production resources.
    ```

    No IRSA annotation is needed on the Holmes service account in this mode: each role is assumed with the pod's projected token.

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        aws:
          enabled: true
          config:
            region: "us-east-1"  # Default region for all accounts

          multiAccount:
            enabled: true
            profiles:
              dev:
                account_id: "111111111111"
                role_arn: "arn:aws:iam::111111111111:role/EKSMultiAccountMCPRole"
                description: "Development account"   # optional, shown to the LLM as the server description
                region: "us-east-1"                  # optional, defaults to config.region
              prod:
                account_id: "222222222222"
                role_arn: "arn:aws:iam::222222222222:role/EKSMultiAccountMCPRole"
                description: "Production account"
            llm_account_descriptions: |
              aws_dev is the development account and contains the development resources.
              aws_prod is the production account and contains the production resources.
    ```

    No IRSA annotation is needed on the Holmes service account in this mode: each role is assumed with the pod's projected token.

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## OAuth (optional)

The AWS MCP Server also accepts [OAuth 2.1 sign-in](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/oauth-authentication.html) instead of IAM credentials. Configure it like any other [OAuth MCP server](../oauth-mcp-servers.md); Holmes opens a browser for the AWS login, so this suits the CLI rather than headless deployments. The `?oauth=initialize` suffix is required: without it the server answers unauthenticated requests with 200 and OAuth discovery fails. We verified discovery up to the AWS sign-in authorization server but not the browser login itself.

```yaml
mcp_servers:
  aws_api:
    description: "AWS MCP Server (OAuth)"
    config:
      mode: streamable-http
      url: https://aws-mcp.us-east-1.api.aws/mcp?oauth=initialize
      oauth:
        enabled: true
```

OAuth grants no permissions beyond those of the signed-in identity; your IAM policies still apply.

## Example Usage

```
"Why can't my application connect to RDS? It stopped working after 3 PM yesterday."
```

```
"What changed in our AWS infrastructure in the last 24 hours?"
```

```
"Why did our AWS costs increase 40% last week?"
```

```
"Is there something wrong with our load balancer? Users are reporting timeouts."
```

```
"What security groups are attached to our production EC2 instances?"
```

```
"Can you check the EKS node group status and see if there are any capacity issues?"
```

## Troubleshooting

```bash
# "AWS credentials check failed" in holmes toolset list / Holmes logs:
# verify the pod's identity is the IAM role, not the node role
kubectl exec deploy/holmes-holmes -- python -c "import boto3; print(boto3.client('sts').get_caller_identity()['Arn'])"

# Trust policy mismatch (AccessDenied on AssumeRoleWithWebIdentity):
# the `sub` condition must name the Holmes service account
kubectl get deploy -l app=holmes -o jsonpath='{.items[0].spec.template.spec.serviceAccountName}'

# 401 from https://aws-mcp.<region>.api.aws/mcp: credentials expired or the
# region in config.region does not exist; re-run `aws sso login` for CLI profiles

# Slow or timed-out tool calls: aws___run_script takes 10-20 s per call;
# raise MCP_TOOL_CALL_TIMEOUT_SEC (default 120) if your scripts need longer
```

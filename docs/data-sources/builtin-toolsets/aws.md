# AWS (MCP)

The AWS MCP server gives Holmes **read-only access to any AWS API** you permit via IAM. This means Holmes can query EC2, RDS, ELB, CloudWatch, CloudTrail, S3, Lambda, Cost Explorer, and hundreds of other AWS services - limited only by the IAM policy you attach.

## Overview

- **Helm users**: The MCP server pod is deployed automatically when you enable the addon
- **CLI users**: The MCP server runs locally on your machine as a subprocess -- no Kubernetes cluster required

## Single Account Setup

### Step 1: Set Up IAM Permissions

!!! tip "CLI users can skip this step"
    If you're using Holmes CLI (local stdio mode), the MCP server uses your local AWS credentials directly. Skip to [Step 2](#step-2-deploy-aws-mcp) and select the "Holmes CLI" tab.

The AWS MCP server requires read-only permissions across AWS services. We provide a default IAM policy that works for most users. You can customize it to restrict access if needed.

=== "Helper Scripts (recommended)"

    We provide scripts that automate the IAM setup:

    ```bash
    # Download the scripts
    curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/enable-oidc-provider.sh
    curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/setup-irsa.sh
    chmod +x enable-oidc-provider.sh setup-irsa.sh

    # 1. Enable OIDC provider for your EKS cluster (if not already enabled)
    ./enable-oidc-provider.sh --cluster-name YOUR_CLUSTER_NAME --region YOUR_REGION

    # 2. Create IAM policy and role
    ./setup-irsa.sh --cluster-name YOUR_CLUSTER_NAME --region YOUR_REGION
    ```

    The script outputs the role ARN at the end. Save it for Step 2:
    ```
    Role ARN: arn:aws:iam::123456789012:role/HolmesMCPRole
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

    - Holmes Helm Chart: `aws-api-mcp-sa`
    - Robusta Helm Chart: `aws-api-mcp-sa`
    - CLI deployment: `aws-mcp-sa` (as defined in the manifest)

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

Choose your installation method:

=== "Holmes Helm Chart"

    **Step 2a: Update your values.yaml**

    Add the AWS MCP addon configuration:

    ```yaml
    mcpAddons:
      aws:
        enabled: true

        serviceAccount:
          create: true
          annotations:
            # Use the IAM role ARN from Step 1
            eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT_ID:role/HolmesMCPRole"

        config:
          region: "us-east-1"  # Change to your AWS region
    ```

    For additional options (resources, network policy, node selectors), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml#L75).

    **Step 2b: Deploy Holmes**

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

    **Step 2c: Verify the deployment**

    ```bash
    # Check that the MCP server pod is running
    kubectl get pods -l app.kubernetes.io/name=aws-mcp-server

    # Check the logs for any errors
    kubectl logs -l app.kubernetes.io/name=aws-mcp-server
    ```

=== "Robusta Helm Chart"

    **Step 2a: Update your Helm values**

    Add the Holmes MCP addon configuration under the `holmes` section:

    ```yaml
    holmes:
      mcpAddons:
        aws:
          enabled: true

          serviceAccount:
            create: true
            annotations:
              # Use the IAM role ARN from Step 1
              eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT_ID:role/HolmesMCPRole"

          config:
            region: "us-east-1"  # Change to your AWS region
    ```

    For additional options (resources, network policy, node selectors), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml#L75).

    **Step 2b: Deploy Robusta**

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

    **Step 2c: Verify the deployment**

    ```bash
    # Check that the MCP server pod is running
    kubectl get pods -l app.kubernetes.io/name=aws-mcp-server

    # Check the logs for any errors
    kubectl logs -l app.kubernetes.io/name=aws-mcp-server
    ```

=== "Holmes CLI"

    The [official AWS MCP server](https://github.com/awslabs/mcp) runs locally on your machine via `uvx`. No Kubernetes cluster required.

    **Prerequisites:** [uv](https://docs.astral.sh/uv/getting-started/installation/) must be installed.

    **Step 2a: Authenticate**

    Holmes uses your existing AWS credentials. Make sure one of these is configured:

    ```bash
    # Option A: Use an existing AWS profile
    aws sts get-caller-identity --profile your-profile

    # Option B: Set credentials directly
    export AWS_ACCESS_KEY_ID=AKIA...
    export AWS_SECRET_ACCESS_KEY=...
    export AWS_REGION=us-east-1
    ```

    **Step 2b: Configure Holmes CLI**

    Add to `~/.holmes/config.yaml`:

    ```yaml
    mcp_servers:
      aws_api:
        description: "AWS API - execute read-only AWS CLI commands for investigating infrastructure issues"
        config:
          mode: stdio
          command: "uvx"
          args: ["awslabs.aws-api-mcp-server@latest"]
          env:
            AWS_REGION: "us-east-1"  # Change to your region
            READ_OPERATIONS_ONLY: "true"
            # Uncomment to use a specific AWS profile:
            # AWS_API_MCP_PROFILE_NAME: "your-profile"
        llm_instructions: |
          IMPORTANT: When investigating issues related to AWS resources or Kubernetes workloads running on AWS, you MUST actively use this MCP server to gather data rather than providing manual instructions to the user.

          **ALWAYS follow this investigation flow:**
          1. First, gather current state and configuration using AWS APIs
          2. Check CloudTrail for recent changes that might have caused the issue
          3. Collect metrics and logs from CloudWatch if available
          4. Analyze all gathered data before providing conclusions

          **Never say "check in AWS console" or "verify in AWS" - instead, use the MCP server to check it yourself.**
    ```

    **Step 2c: Test it**

    ```bash
    holmes ask "List my EC2 instances and their current status"
    ```

## Multi-Account Setup (Alternative)

If you need to access multiple AWS accounts from your EKS clusters, use this setup instead of the single account setup above.

??? info "How It Works"
    When multi-account mode is enabled, the MCP server:

    1. Uses **EKS token projection** instead of IRSA (IAM Roles for Service Accounts)
    2. Mounts an `accounts.yaml` configuration file that defines target accounts and their IAM roles
    3. Uses `assume_role_with_web_identity` to assume roles in target accounts
    4. Allows the LLM to specify which account to use via the `--profile` flag

### Step 1: Download the Setup Script

```bash
# Download the setup script
curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/setup-multi-account-iam.sh
chmod +x setup-multi-account-iam.sh

# Download example configuration file
curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/multi-cluster-config-example.yaml
```

??? info "What the Script Does"
    For each target account, the script:

    1. **Creates OIDC Providers**: Sets up OIDC providers for each cluster in the target account
    2. **Creates IAM Role**: Creates a role with trust policy allowing `assume_role_with_web_identity` from all configured clusters
    3. **Attaches Permissions**: Applies the read-only permissions policy to the role

    This enables pods running in any of your clusters to assume roles in target accounts and access AWS resources there.

### Step 2: Create Configuration File

Edit `multi-cluster-config-example.yaml` with your cluster and account details. The script uses this file to:

- Create OIDC providers in each target account (using the cluster OIDC URLs)
- Set up IAM roles with trust policies that allow your clusters to assume them
- Configure which AWS accounts Holmes can access via `--profile`

??? example "Example Configuration"
    ```yaml
    clusters:
      - name: prod-cluster
        region: us-east-1
        account_id: "111111111111"
        oidc_issuer_id: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
        oidc_issuer_url: https://oidc.eks.us-east-1.amazonaws.com/id/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA

      - name: staging-cluster
        region: us-west-2
        account_id: "111111111111"
        oidc_issuer_id: BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
        oidc_issuer_url: https://oidc.eks.us-west-2.amazonaws.com/id/BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB

    kubernetes:
      namespace: default
      service_account: multi-account-mcp-sa

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

To get the `oidc_issuer_url` and `oidc_issuer_id` values for each cluster in the config file:

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

Once the IAM roles are set up, configure the Helm chart to enable multi-account mode:

=== "Holmes Helm Chart"

    Add the following configuration to your `values.yaml` file:

    ```yaml
    mcpAddons:
      aws:
        enabled: true

        # AWS configuration
        config:
          region: "us-east-1"  # Your AWS region
          readOnlyMode: true

        # Multi-account configuration
        multiAccount:
          enabled: true
          profiles:
            dev:
              account_id: "111111111111"
              role_arn: "arn:aws:iam::111111111111:role/EKSMultiAccountMCPRole"
              region: "us-east-1"  # optional, defaults to the region specified in config
            prod:
              account_id: "222222222222"
              role_arn: "arn:aws:iam::222222222222:role/EKSMultiAccountMCPRole"
              region: "us-east-1"  # optional, defaults to the region specified in config
          llm_account_descriptions: |
            You must use the --profile flag to specify the account to use.
            Example: --profile dev - this is the development account and contains the development resources
            Example: --profile prod - this is the production account and contains the production resources

        # Note: When multiAccount.enabled is true, IRSA annotations are not used
        # The service account will use EKS token projection instead
        serviceAccount:
          create: true
          # annotations are ignored when multiAccount is enabled
    ```

=== "Robusta Helm Chart"

    Add the following configuration to your Helm values:

    ```yaml
    holmes:
      mcpAddons:
        aws:
          enabled: true

          # AWS configuration
          config:
            region: "us-east-1"  # Your AWS region
            readOnlyMode: true

          # Multi-account configuration
          multiAccount:
            enabled: true
            profiles:
              dev:
                account_id: "111111111111"
                role_arn: "arn:aws:iam::111111111111:role/EKSMultiAccountMCPRole"
                region: "us-east-1"  # optional, defaults to the region specified in config
              prod:
                account_id: "222222222222"
                role_arn: "arn:aws:iam::222222222222:role/EKSMultiAccountMCPRole"
                region: "us-east-1"  # optional, defaults to the region specified in config
            llm_account_descriptions: |
              You must use the --profile flag to specify the account to use.
              Example: --profile dev - this is the development account and contains the development resources
              Example: --profile prod - this is the production account and contains the production resources

          # Note: When multiAccount.enabled is true, IRSA annotations are not used
          # The service account will use EKS token projection instead
          serviceAccount:
            create: true
            # annotations are ignored when multiAccount is enabled
    ```

=== "Holmes CLI"

    Multi-account mode is not currently supported for CLI deployments. Use the [Single Account Setup](#single-account-setup) instead, or deploy Holmes via Helm.

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

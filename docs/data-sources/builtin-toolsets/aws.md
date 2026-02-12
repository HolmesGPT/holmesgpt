# AWS (MCP)

The AWS MCP server gives Holmes **read-only access to any AWS API** you permit via IAM. This means Holmes can query EC2, RDS, ELB, CloudWatch, CloudTrail, S3, Lambda, Cost Explorer, and hundreds of other AWS services - limited only by the IAM policy you attach.

## Overview

The AWS MCP server runs as a pod in your Kubernetes cluster.

- **Helm users**: The pod is deployed automatically when you enable the addon
- **CLI users**: You deploy the pod manually to your cluster, then point Holmes at it

!!! note
    Even when using Holmes CLI locally, the AWS MCP server must run in a Kubernetes cluster. Local-only deployment is not currently supported.

## Single Account Setup

### Step 1: Set Up IAM Permissions

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

    For CLI usage, you deploy the AWS MCP server to your cluster, then configure Holmes to connect to it.

    **Step 2a: Create the deployment manifest**

    Create a file named `aws-mcp-deployment.yaml`. The manifest below uses IRSA (recommended for EKS). Replace `ACCOUNT_ID` with your AWS account ID in the role ARN annotation.

    ??? info "Using Access Keys Instead of IRSA"
        If you're not on EKS or prefer access keys, make these changes to the manifest:

        **1. Remove the annotation** from the ServiceAccount:
        ```yaml
        apiVersion: v1
        kind: ServiceAccount
        metadata:
          name: aws-mcp-sa
          namespace: holmes-mcp
          # No annotations needed for access keys
        ```

        **2. Create a secret** with your credentials:
        ```bash
        kubectl create secret generic aws-credentials \
          --from-literal=aws-access-key-id=YOUR_KEY \
          --from-literal=aws-secret-access-key=YOUR_SECRET \
          -n holmes-mcp
        ```

        **3. Add environment variables** to the container spec (after `AWS_DEFAULT_REGION`):
        ```yaml
        - name: AWS_ACCESS_KEY_ID
          valueFrom:
            secretKeyRef:
              name: aws-credentials
              key: aws-access-key-id
        - name: AWS_SECRET_ACCESS_KEY
          valueFrom:
            secretKeyRef:
              name: aws-credentials
              key: aws-secret-access-key
        ```

    ```yaml
    apiVersion: v1
    kind: Namespace
    metadata:
      name: holmes-mcp
    ---
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: aws-mcp-sa
      namespace: holmes-mcp
      annotations:
        # For IRSA: use the IAM role ARN from Step 1
        eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT_ID:role/HolmesMCPRole"
    ---
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: aws-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: aws-mcp-server
      template:
        metadata:
          labels:
            app: aws-mcp-server
        spec:
          serviceAccountName: aws-mcp-sa
          containers:
          - name: aws-mcp
            image: us-central1-docker.pkg.dev/genuine-flight-317411/devel/aws-api-mcp-server:1.0.1
            imagePullPolicy: Always
            ports:
            - containerPort: 8000
              name: http
            env:
            - name: AWS_REGION
              value: "us-east-1"  # Change to your region
            - name: AWS_DEFAULT_REGION
              value: "us-east-1"  # Change to your region
            - name: READ_OPERATIONS_ONLY
              value: "true"
            resources:
              requests:
                memory: "512Mi"
                cpu: "250m"
              limits:
                memory: "1Gi"
                cpu: "500m"
            readinessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 20
              periodSeconds: 10
            livenessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 30
              periodSeconds: 30
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: aws-mcp-server
      namespace: holmes-mcp
    spec:
      selector:
        app: aws-mcp-server
      ports:
      - port: 8000
        targetPort: 8000
        protocol: TCP
        name: http
    ```

    **Step 2b: Deploy to your cluster**

    ```bash
    kubectl apply -f aws-mcp-deployment.yaml
    ```

    **Step 2c: Verify the deployment**

    ```bash
    # Check that the pod is running
    kubectl get pods -n holmes-mcp

    # Check the logs for any errors
    kubectl logs -n holmes-mcp -l app=aws-mcp-server
    ```

    **Step 2d: Configure Holmes CLI**

    Add the MCP server to `~/.holmes/config.yaml`:

    ```yaml
    mcp_servers:
      aws_api:
        description: "AWS API MCP Server - comprehensive AWS service access. Allow executing any AWS CLI commands."
        url: "http://aws-mcp-server.holmes-mcp.svc.cluster.local:8000"
        llm_instructions: |
          IMPORTANT: When investigating issues related to AWS resources or Kubernetes workloads running on AWS, you MUST actively use this MCP server to gather data rather than providing manual instructions to the user.

          ## Investigation Principles

          **ALWAYS follow this investigation flow:**
          1. First, gather current state and configuration using AWS APIs
          2. Check CloudTrail for recent changes that might have caused the issue
          3. Collect metrics and logs from CloudWatch if available
          4. Analyze all gathered data before providing conclusions

          **Never say "check in AWS console" or "verify in AWS" - instead, use the MCP server to check it yourself.**

          ## Core Investigation Patterns

          ### For ANY connectivity or access issues:
          1. ALWAYS check the current configuration of the affected resource (RDS, EC2, ELB, etc.)
          2. ALWAYS examine security groups and network ACLs
          3. ALWAYS query CloudTrail for recent configuration changes
          4. Look for patterns in timing between when issues started and when changes were made

          ### When investigating database issues (RDS):
          - Get RDS instance status and configuration: `aws rds describe-db-instances --db-instance-identifier INSTANCE_ID`
          - Check security groups attached to RDS: Extract VpcSecurityGroups from the above
          - Examine security group rules: `aws ec2 describe-security-groups --group-ids SG_ID`
          - Look for recent RDS events: `aws rds describe-events --source-identifier INSTANCE_ID --source-type db-instance`
          - Check CloudTrail for security group modifications: `aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=SG_ID`

          Remember: Your goal is to gather evidence from AWS, not to instruct the user to gather it. Use the MCP server proactively to build a complete picture of what happened.
    ```

    **Step 2e: Port forwarding (for local testing only)**

    If running Holmes CLI locally (outside the cluster):

    ```bash
    kubectl port-forward -n holmes-mcp svc/aws-mcp-server 8000:8000
    ```

    Then update the URL in `~/.holmes/config.yaml`:

    ```yaml
    url: "http://localhost:8000"
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

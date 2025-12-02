# AWS (MCP)

The AWS MCP (Model Context Protocol) server provides comprehensive access to AWS services through a secure, read-only interface. It enables Holmes to investigate AWS infrastructure issues, analyze CloudTrail events, examine security configurations, and troubleshoot service-specific problems.

## Overview

The AWS MCP server is deployed as an add-on to the Holmes Helm chart, providing a dedicated service that Holmes can use to interact with AWS APIs. It supports all AWS CLI commands and services, making it a powerful tool for comprehensive AWS investigations.

## Configuration

=== "Robusta Helm Chart"

    Add the following to your `values.yaml` file:

    ```yaml
    mcpAddons:
      aws:
        enabled: true

        # Service account for IRSA (IAM Roles for Service Accounts)
        serviceAccount:
          create: true
          name: "aws-api-mcp-sa"
          annotations:
            # Add your EKS IRSA role ARN here
            eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT_ID:role/YOUR_ROLE_NAME"

        # Image configuration (optional - defaults shown)
        image: "aws-api-mcp-server:1.0.1"
        registry: "us-central1-docker.pkg.dev/genuine-flight-317411/devel"

        # Resource limits (optional - defaults shown)
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"

        # AWS configuration
        config:
          region: "us-east-1"        # Your AWS region
          readOnlyMode: true          # Keep true for safety (prevents write operations)
          namespace: ""               # Leave empty to use release namespace

        # Network isolation (recommended)
        networkPolicy:
          enabled: true

        # Pod placement (optional)
        nodeSelector: {}
        tolerations: []
        affinity: {}
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Holmes CLI"

    For CLI usage, you'll need to configure AWS credentials directly:

    ```bash
    export AWS_ACCESS_KEY_ID="<your AWS access key ID>"
    export AWS_SECRET_ACCESS_KEY="<your AWS secret access key>"
    export AWS_DEFAULT_REGION="us-west-2"
    ```

    Then configure the MCP server in **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      aws_api:
        description: "AWS API MCP Server"
        url: "http://your-aws-mcp-server:8000"
        llm_instructions: |
          Use this server to investigate AWS resources and issues.
    ```

## IAM Configuration

### EKS with IRSA (Recommended)

For EKS clusters, use IAM Roles for Service Accounts (IRSA) for secure, fine-grained permissions:

1. Create an IAM policy with the required permissions (see example below)
2. Create an IAM role and attach the policy
3. Associate the role with the service account using the annotation in values.yaml

### IAM Policy Example

Here's a comprehensive read-only IAM policy for the AWS MCP server:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:Describe*",
        "rds:Describe*",
        "rds:List*",
        "elasticloadbalancing:Describe*",
        "autoscaling:Describe*",
        "cloudwatch:Describe*",
        "cloudwatch:Get*",
        "cloudwatch:List*",
        "logs:Describe*",
        "logs:Get*",
        "logs:List*",
        "logs:FilterLogEvents",
        "cloudtrail:LookupEvents",
        "cloudtrail:Get*",
        "cloudtrail:Describe*",
        "cloudtrail:List*",
        "iam:Get*",
        "iam:List*",
        "iam:SimulatePrincipalPolicy",
        "s3:List*",
        "s3:Get*",
        "lambda:Get*",
        "lambda:List*",
        "eks:Describe*",
        "eks:List*",
        "ecs:Describe*",
        "ecs:List*",
        "kms:Describe*",
        "kms:List*",
        "sns:Get*",
        "sns:List*",
        "sqs:Get*",
        "sqs:List*",
        "organizations:Describe*",
        "organizations:List*",
        "ce:Get*",
        "ce:Describe*",
        "ce:List*",
        "tag:Get*"
      ],
      "Resource": "*"
    }
  ]
}
```

Save this policy to a file (e.g., `aws-mcp-policy.json`) and attach it to your IAM role.

## Capabilities

The AWS MCP server provides access to all AWS services through the AWS CLI. Common investigation patterns include:

### CloudTrail Investigation
- Query recent API calls and configuration changes
- Find who made specific changes
- Correlate changes with issue timelines
- Audit security events

### EC2 and Networking
- Describe instances, security groups, VPCs
- Check network ACLs and route tables
- Investigate connectivity issues
- Review instance metadata and status

### RDS Database Issues
- Check database instance status and configuration
- Review security groups and network access
- Analyze performance metrics
- Look up recent events and modifications

### EKS/Container Issues
- Describe cluster configuration
- Check node group status
- Query CloudWatch Container Insights
- Review pod logs and metrics

### Load Balancers
- Check target health
- Review listener configurations
- Investigate traffic patterns
- Analyze access logs

### Cost and Usage
- Query cost and usage reports
- Analyze spending trends
- Identify expensive resources

## Best Practices

### Memory Management

The AWS MCP server can experience memory pressure with large queries. Follow these guidelines:

- **CloudWatch Logs**: Limit to 500 items, use 1-hour time windows initially
- **CloudTrail**: Limit to 200 items, use 2-hour time windows
- **EC2 Describe**: Can handle up to 500 items
- **Always use time constraints** for log queries
- Use pagination for large result sets

### Security

- Always use `readOnlyMode: true` in production
- Implement network policies to restrict access
- Use IRSA for credential management in EKS
- Regularly audit IAM permissions

### Investigation Tips

1. Let Holmes actively query AWS rather than asking for manual checks
2. Start with current state queries before historical analysis
3. Use CloudTrail to correlate changes with issue timing
4. Leverage CloudWatch for metrics and logs
5. Check security groups and network ACLs for connectivity issues

## Example Investigations

### Database Connection Issues
```
"My application can't connect to RDS after 3 PM yesterday"
```
Holmes will:
- Check RDS instance status and configuration
- Review security group changes in CloudTrail
- Identify who made the changes
- Analyze the specific rules that were modified

### EKS Pod Failures
```
"Pods in my EKS cluster are failing with ImagePullBackOff"
```
Holmes will:
- Check ECR repository permissions
- Review IAM roles and policies
- Look for recent permission changes
- Examine node group configurations

### Cost Spike Investigation
```
"Our AWS costs increased 40% last week"
```
Holmes will:
- Query cost and usage reports
- Identify services with increased spending
- Find new or modified resources
- Analyze usage patterns

## Troubleshooting

### Common Issues

1. **Service Account Not Assuming Role**
   - Verify IRSA annotation is correct
   - Check trust relationship on IAM role
   - Ensure OIDC provider is configured

2. **Memory Issues with Large Queries**
   - Reduce time windows for log queries
   - Use pagination with `--max-items`
   - Add specific filters to reduce data volume

3. **Permission Denied Errors**
   - Review IAM policy attached to role
   - Check for explicit deny rules
   - Verify resource-level permissions

### Verification

To verify the MCP server is working:

```bash
# Check if the pod is running
kubectl get pods -n <namespace> | grep aws-mcp

# Check logs
kubectl logs -n <namespace> <aws-mcp-pod-name>

# Test from Holmes
holmes ask "What EC2 instances are running in my account?"
```

## Migration from Legacy AWS Toolset

If you're using the older AWS toolset, migrate to the MCP version for:
- Better memory management
- Comprehensive service coverage
- Active CloudTrail investigation
- Improved error handling

The legacy toolset will be deprecated in future releases.

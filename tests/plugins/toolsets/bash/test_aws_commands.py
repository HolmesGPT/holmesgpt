"""
Tests for AWS CLI command parsing, validation, and safety.

These tests verify:
1. Safe AWS commands are properly parsed and stringified
2. Unsafe AWS commands are rejected
3. Command validation works correctly
"""

import pytest
import argparse
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestAWSCliSafeCommands:
    """Test AWS CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Basic EC2 commands
            ("aws ec2 describe-instances", "aws ec2 describe-instances"),
            (
                "aws ec2 describe-instances --output json",
                "aws ec2 describe-instances --output json",
            ),
            (
                "aws ec2 describe-instances --region us-east-1",
                "aws ec2 describe-instances --region us-east-1",
            ),
            (
                "aws ec2 describe-images --owners amazon",
                "aws ec2 describe-images --owners amazon",
            ),
            (
                "aws ec2 describe-volumes --max-items 50",
                "aws ec2 describe-volumes --max-items 50",
            ),
            (
                "aws ec2 describe-security-groups --group-ids sg-12345",
                "aws ec2 describe-security-groups --group-ids sg-12345",
            ),
            (
                "aws ec2 describe-subnets --subnet-ids subnet-12345",
                "aws ec2 describe-subnets --subnet-ids subnet-12345",
            ),
            (
                "aws ec2 describe-vpcs --vpc-ids vpc-12345",
                "aws ec2 describe-vpcs --vpc-ids vpc-12345",
            ),
            (
                "aws ec2 describe-availability-zones",
                "aws ec2 describe-availability-zones",
            ),
            ("aws ec2 describe-regions", "aws ec2 describe-regions"),
            # S3 commands
            ("aws s3 list-buckets", "aws s3 list-buckets"),
            (
                "aws s3 list-objects --bucket my-bucket",
                "aws s3 list-objects --bucket my-bucket",
            ),
            ("aws s3api list-buckets", "aws s3api list-buckets"),
            (
                "aws s3api head-bucket --bucket my-bucket",
                "aws s3api head-bucket --bucket my-bucket",
            ),
            (
                "aws s3api get-bucket-location --bucket my-bucket",
                "aws s3api get-bucket-location --bucket my-bucket",
            ),
            (
                "aws s3api get-bucket-versioning --bucket my-bucket",
                "aws s3api get-bucket-versioning --bucket my-bucket",
            ),
            # Lambda commands
            ("aws lambda list-functions", "aws lambda list-functions"),
            (
                "aws lambda get-function --function-name my-function",
                "aws lambda get-function --function-name my-function",
            ),
            ("aws lambda list-layers", "aws lambda list-layers"),
            (
                "aws lambda get-function-configuration --function-name my-function",
                "aws lambda get-function-configuration --function-name my-function",
            ),
            (
                "aws lambda list-aliases --function-name my-function",
                "aws lambda list-aliases --function-name my-function",
            ),
            # RDS commands
            ("aws rds describe-db-instances", "aws rds describe-db-instances"),
            ("aws rds describe-db-clusters", "aws rds describe-db-clusters"),
            ("aws rds describe-db-snapshots", "aws rds describe-db-snapshots"),
            (
                "aws rds describe-db-parameter-groups",
                "aws rds describe-db-parameter-groups",
            ),
            (
                "aws rds describe-db-engine-versions",
                "aws rds describe-db-engine-versions",
            ),
            # ECS commands
            ("aws ecs list-clusters", "aws ecs list-clusters"),
            (
                "aws ecs list-services --cluster my-cluster",
                "aws ecs list-services --cluster my-cluster",
            ),
            (
                "aws ecs describe-clusters --clusters my-cluster",
                "aws ecs describe-clusters --clusters my-cluster",
            ),
            (
                "aws ecs describe-services --cluster my-cluster --services my-service",
                "aws ecs describe-services --cluster my-cluster --services my-service",
            ),
            (
                "aws ecs list-tasks --cluster my-cluster",
                "aws ecs list-tasks --cluster my-cluster",
            ),
            # CloudWatch commands
            ("aws cloudwatch list-metrics", "aws cloudwatch list-metrics"),
            ("aws cloudwatch describe-alarms", "aws cloudwatch describe-alarms"),
            (
                "aws cloudwatch get-metric-statistics --namespace AWS/EC2 --metric-name CPUUtilization --start-time 2023-01-01T00:00:00Z --end-time 2023-01-02T00:00:00Z --period 3600 --statistics Average",
                "aws cloudwatch get-metric-statistics --namespace AWS/EC2 --metric-name CPUUtilization --start-time 2023-01-01T00:00:00Z --end-time 2023-01-02T00:00:00Z --period 3600 --statistics Average",
            ),
            ("aws logs describe-log-groups", "aws logs describe-log-groups"),
            (
                "aws logs describe-log-streams --log-group-name my-log-group",
                "aws logs describe-log-streams --log-group-name my-log-group",
            ),
            # Load Balancer commands
            ("aws elbv2 describe-load-balancers", "aws elbv2 describe-load-balancers"),
            ("aws elbv2 describe-target-groups", "aws elbv2 describe-target-groups"),
            (
                "aws elbv2 describe-listeners --load-balancer-arn arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/my-lb/1234567890123456",
                "aws elbv2 describe-listeners --load-balancer-arn arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/my-lb/1234567890123456",
            ),
            ("aws elb describe-load-balancers", "aws elb describe-load-balancers"),
            # IAM commands (read-only)
            ("aws iam list-users", "aws iam list-users"),
            ("aws iam list-roles", "aws iam list-roles"),
            ("aws iam list-groups", "aws iam list-groups"),
            ("aws iam list-policies", "aws iam list-policies"),
            (
                "aws iam get-user --user-name my-user",
                "aws iam get-user --user-name my-user",
            ),
            (
                "aws iam get-role --role-name my-role",
                "aws iam get-role --role-name my-role",
            ),
            ("aws iam get-account-summary", "aws iam get-account-summary"),
            # Route53 commands
            ("aws route53 list-hosted-zones", "aws route53 list-hosted-zones"),
            (
                "aws route53 list-resource-record-sets --hosted-zone-id Z123456789",
                "aws route53 list-resource-record-sets --hosted-zone-id Z123456789",
            ),
            (
                "aws route53 get-hosted-zone --id Z123456789",
                "aws route53 get-hosted-zone --id Z123456789",
            ),
            ("aws route53 list-health-checks", "aws route53 list-health-checks"),
            # DynamoDB commands
            ("aws dynamodb list-tables", "aws dynamodb list-tables"),
            (
                "aws dynamodb describe-table --table-name my-table",
                "aws dynamodb describe-table --table-name my-table",
            ),
            (
                "aws dynamodb describe-time-to-live --table-name my-table",
                "aws dynamodb describe-time-to-live --table-name my-table",
            ),
            # SSM commands (safe ones)
            (
                "aws ssm describe-instance-information",
                "aws ssm describe-instance-information",
            ),
            ("aws ssm describe-parameters", "aws ssm describe-parameters"),
            ("aws ssm list-documents", "aws ssm list-documents"),
            (
                "aws ssm describe-document --name AWS-RunShellScript",
                "aws ssm describe-document --name AWS-RunShellScript",
            ),
            # Commands with various output formats
            (
                "aws ec2 describe-instances --output table",
                "aws ec2 describe-instances --output table",
            ),
            (
                "aws ec2 describe-instances --output yaml",
                "aws ec2 describe-instances --output yaml",
            ),
            ("aws s3 list-buckets --output text", "aws s3 list-buckets --output text"),
            # Commands with queries
            (
                "aws ec2 describe-instances --query 'Reservations[*].Instances[*].InstanceId'",
                "aws ec2 describe-instances --query 'Reservations[*].Instances[*].InstanceId'",
            ),
            (
                "aws s3 list-buckets --query 'Buckets[*].Name'",
                "aws s3 list-buckets --query 'Buckets[*].Name'",
            ),
            # Commands with regions
            (
                "aws ec2 describe-instances --region us-west-2",
                "aws ec2 describe-instances --region us-west-2",
            ),
            (
                "aws ec2 describe-instances --region eu-west-1",
                "aws ec2 describe-instances --region eu-west-1",
            ),
            (
                "aws s3 list-buckets --region ap-southeast-1",
                "aws s3 list-buckets --region ap-southeast-1",
            ),
            # Commands with profiles
            (
                "aws ec2 describe-instances --profile prod",
                "aws ec2 describe-instances --profile prod",
            ),
            ("aws s3 list-buckets --profile dev", "aws s3 list-buckets --profile dev"),
            # Commands with pagination
            (
                "aws ec2 describe-instances --max-items 10",
                "aws ec2 describe-instances --max-items 10",
            ),
            (
                "aws s3 list-objects --bucket my-bucket --page-size 100",
                "aws s3 list-objects --bucket my-bucket --page-size 100",
            ),
            (
                "aws ec2 describe-images --max-items 20 --starting-token abc123",
                "aws ec2 describe-images --max-items 20 --starting-token abc123",
            ),
            # Commands with debug flags
            (
                "aws ec2 describe-instances --debug",
                "aws ec2 describe-instances --debug",
            ),
            (
                "aws s3 list-buckets --no-cli-pager",
                "aws s3 list-buckets --no-cli-pager",
            ),
            (
                "aws ec2 describe-instances --color on",
                "aws ec2 describe-instances --color on",
            ),
            (
                "aws ec2 describe-instances --no-color",
                "aws ec2 describe-instances --no-color",
            ),
            # CloudFront commands
            ("aws cloudfront list-distributions", "aws cloudfront list-distributions"),
            (
                "aws cloudfront get-distribution --id E123456789",
                "aws cloudfront get-distribution --id E123456789",
            ),
            # CloudTrail commands
            ("aws cloudtrail describe-trails", "aws cloudtrail describe-trails"),
            (
                "aws cloudtrail get-trail-status --name my-trail",
                "aws cloudtrail get-trail-status --name my-trail",
            ),
            ("aws cloudtrail lookup-events", "aws cloudtrail lookup-events"),
        ],
    )
    def test_aws_safe_commands(self, input_command: str, expected_output: str):
        """Test that safe AWS commands are parsed and stringified correctly."""
        config = BashExecutorConfig()
        output_command = make_command_safe(input_command, config=config)
        assert output_command == expected_output


class TestAWSCliUnsafeCommands:
    """Test AWS CLI unsafe commands that should be rejected."""

    @pytest.mark.parametrize(
        "command,expected_exception,partial_error_message_content",
        [
            # Blocked services
            ("aws configure list", ValueError, "blocked"),
            ("aws sts get-caller-identity", ValueError, "blocked"),
            ("aws secretsmanager list-secrets", ValueError, "blocked"),
            ("aws kms list-keys", ValueError, "blocked"),
            # State-modifying EC2 operations
            ("aws ec2 run-instances --image-id ami-12345", ValueError, "blocked"),
            (
                "aws ec2 terminate-instances --instance-ids i-12345",
                ValueError,
                "blocked",
            ),
            ("aws ec2 start-instances --instance-ids i-12345", ValueError, "blocked"),
            ("aws ec2 stop-instances --instance-ids i-12345", ValueError, "blocked"),
            ("aws ec2 reboot-instances --instance-ids i-12345", ValueError, "blocked"),
            ("aws ec2 create-volume --size 10", ValueError, "blocked"),
            ("aws ec2 delete-volume --volume-id vol-12345", ValueError, "blocked"),
            ("aws ec2 create-security-group --group-name test", ValueError, "blocked"),
            (
                "aws ec2 delete-security-group --group-id sg-12345",
                ValueError,
                "blocked",
            ),
            (
                "aws ec2 authorize-security-group-ingress --group-id sg-12345",
                ValueError,
                "blocked",
            ),
            ("aws ec2 create-vpc --cidr-block 10.0.0.0/16", ValueError, "blocked"),
            ("aws ec2 delete-vpc --vpc-id vpc-12345", ValueError, "blocked"),
            # State-modifying S3 operations
            ("aws s3 cp file.txt s3://bucket/", ValueError, "blocked"),
            ("aws s3 mv file.txt s3://bucket/", ValueError, "blocked"),
            ("aws s3 rm s3://bucket/file.txt", ValueError, "blocked"),
            ("aws s3 sync . s3://bucket/", ValueError, "blocked"),
            ("aws s3 mb s3://new-bucket", ValueError, "blocked"),
            ("aws s3 rb s3://bucket", ValueError, "blocked"),
            ("aws s3api create-bucket --bucket new-bucket", ValueError, "blocked"),
            ("aws s3api delete-bucket --bucket old-bucket", ValueError, "blocked"),
            ("aws s3api put-object --bucket bucket --key file", ValueError, "blocked"),
            (
                "aws s3api delete-object --bucket bucket --key file",
                ValueError,
                "blocked",
            ),
            # State-modifying Lambda operations
            ("aws lambda create-function --function-name test", ValueError, "blocked"),
            ("aws lambda delete-function --function-name test", ValueError, "blocked"),
            (
                "aws lambda update-function-code --function-name test",
                ValueError,
                "blocked",
            ),
            ("aws lambda invoke --function-name test", ValueError, "blocked"),
            ("aws lambda publish-version --function-name test", ValueError, "blocked"),
            # State-modifying RDS operations
            (
                "aws rds create-db-instance --db-instance-identifier test",
                ValueError,
                "blocked",
            ),
            (
                "aws rds delete-db-instance --db-instance-identifier test",
                ValueError,
                "blocked",
            ),
            (
                "aws rds modify-db-instance --db-instance-identifier test",
                ValueError,
                "blocked",
            ),
            (
                "aws rds reboot-db-instance --db-instance-identifier test",
                ValueError,
                "blocked",
            ),
            (
                "aws rds start-db-instance --db-instance-identifier test",
                ValueError,
                "blocked",
            ),
            (
                "aws rds stop-db-instance --db-instance-identifier test",
                ValueError,
                "blocked",
            ),
            # State-modifying ECS operations
            ("aws ecs create-cluster --cluster-name test", ValueError, "blocked"),
            ("aws ecs delete-cluster --cluster test", ValueError, "blocked"),
            (
                "aws ecs create-service --cluster test --service-name web",
                ValueError,
                "blocked",
            ),
            (
                "aws ecs delete-service --cluster test --service web",
                ValueError,
                "blocked",
            ),
            (
                "aws ecs update-service --cluster test --service web",
                ValueError,
                "blocked",
            ),
            (
                "aws ecs run-task --cluster test --task-definition web",
                ValueError,
                "blocked",
            ),
            ("aws ecs stop-task --cluster test --task arn", ValueError, "blocked"),
            # State-modifying IAM operations
            ("aws iam create-user --user-name test", ValueError, "blocked"),
            ("aws iam delete-user --user-name test", ValueError, "blocked"),
            ("aws iam create-role --role-name test", ValueError, "blocked"),
            ("aws iam delete-role --role-name test", ValueError, "blocked"),
            (
                "aws iam attach-user-policy --user-name test --policy-arn arn",
                ValueError,
                "blocked",
            ),
            ("aws iam create-access-key --user-name test", ValueError, "blocked"),
            ("aws iam delete-access-key --access-key-id key", ValueError, "blocked"),
            (
                "aws iam put-user-policy --user-name test --policy-name policy",
                ValueError,
                "blocked",
            ),
            # Invalid service
            (
                "aws nonexistent describe-something",
                ValueError,
                "Command is not in the allowlist",
            ),
            # Invalid operation for valid service
            (
                "aws ec2 invalid-operation",
                ValueError,
                "Command is not in the allowlist",
            ),
            ("aws s3 invalid-command", ValueError, "Command is not in the allowlist"),
            (
                "aws lambda invalid-function",
                ValueError,
                "Command is not in the allowlist",
            ),
        ],
    )
    def test_aws_unsafe_commands(
        self, command: str, expected_exception: type, partial_error_message_content: str
    ):
        """Test that unsafe AWS commands are properly rejected."""
        config = BashExecutorConfig()
        with pytest.raises(expected_exception) as exc_info:
            make_command_safe(command, config=config)

        if partial_error_message_content:
            assert partial_error_message_content in str(exc_info.value)


class TestAWSCliEdgeCases:
    """Test edge cases and error conditions for AWS CLI parsing."""

    def test_aws_with_grep_combination(self):
        """Test AWS commands combined with grep."""
        config = BashExecutorConfig()

        # Valid combination
        result = make_command_safe(
            "aws ec2 describe-instances | grep running", config=config
        )
        assert result == "aws ec2 describe-instances | grep running"

        # Invalid - unsafe AWS command with grep
        with pytest.raises(ValueError):
            make_command_safe(
                "aws ec2 run-instances --image-id ami-123 | grep test", config=config
            )

    def test_aws_empty_service_or_operation(self):
        """Test AWS commands with missing service or operation."""
        config = BashExecutorConfig()

        # Missing service should fail at argument parsing level
        with pytest.raises((argparse.ArgumentError, ValueError)):
            make_command_safe("aws", config=config)

    def test_aws_with_complex_valid_parameters(self):
        """Test AWS commands with complex but valid parameters."""
        config = BashExecutorConfig()

        # Complex EC2 command
        complex_cmd = "aws ec2 describe-instances --filters Name=instance-state-name,Values=running --query 'Reservations[*].Instances[*].[InstanceId,State.Name]' --output table"
        result = make_command_safe(complex_cmd, config=config)
        assert "describe-instances" in result
        assert "--filters" in result
        assert "--query" in result
        assert "--output table" in result

    def test_aws_case_sensitivity(self):
        """Test that AWS commands are case-sensitive where appropriate."""
        config = BashExecutorConfig()

        # AWS service names should be lowercase
        with pytest.raises(ValueError):
            make_command_safe("aws EC2 describe-instances", config=config)

        # Operations should match exactly
        with pytest.raises(ValueError):
            make_command_safe("aws ec2 DESCRIBE-INSTANCES", config=config)

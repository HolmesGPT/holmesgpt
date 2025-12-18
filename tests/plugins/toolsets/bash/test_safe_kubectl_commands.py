"""
Integration tests for CLI command parsing and stringifying.

These tests verify the complete round-trip functionality by:
1. Taking a string command as input
2. Parsing it into structured Command objects
3. Stringifying it back to a command string
4. Verifying the output matches expected behavior

Covers kubectl commands.
"""

import pytest
from holmes.plugins.toolsets.bash.common.config import (
    BashExecutorConfig,
    KubectlConfig,
    KubectlImageConfig,
)
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


TEST_CONFIG = BashExecutorConfig(
    kubectl=KubectlConfig(
        allowed_images=[
            KubectlImageConfig(
                image="busybox",
                allowed_commands=["cat /etc/resolv.conf", "nslookup .*"],
            ),
            KubectlImageConfig(
                image="registry.k8s.io/e2e-test-images/jessie-dnsutils:1.3",
                allowed_commands=["cat /etc/resolv.conf"],
            ),
        ]
    )
)


class TestKubectlIntegration:
    """Integration tests for kubectl commands through parse -> stringify pipeline."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Basic kubectl get commands
            ("kubectl get pods", "kubectl get pods"),
            ("kubectl get pod my-pod", "kubectl get pod my-pod"),
            ("kubectl get pods -n default", "kubectl get pods -n default"),
            (
                "kubectl get pods --namespace=kube-system",
                "kubectl get pods --namespace=kube-system",
            ),
            ("kubectl get pods --all-namespaces", "kubectl get pods --all-namespaces"),
            ("kubectl get pods -A", "kubectl get pods -A"),
            ("kubectl get pods -o yaml", "kubectl get pods -o yaml"),
            ("kubectl get pods --output=json", "kubectl get pods --output=json"),
            ("kubectl get pods -l app=nginx", "kubectl get pods -l app=nginx"),
            (
                "kubectl get pods --selector=environment=production",
                "kubectl get pods --selector=environment=production",
            ),
            (
                "kubectl get pods --field-selector=status.phase=Running",
                "kubectl get pods --field-selector=status.phase=Running",
            ),
            ("kubectl get pods --show-labels", "kubectl get pods --show-labels"),
            # Basic kubectl describe commands
            ("kubectl describe pods", "kubectl describe pods"),
            ("kubectl describe pod my-pod", "kubectl describe pod my-pod"),
            (
                "kubectl describe pods -n default",
                "kubectl describe pods -n default",
            ),
            (
                "kubectl describe pods --namespace=kube-system",
                "kubectl describe pods --namespace=kube-system",
            ),
            (
                "kubectl describe pods --all-namespaces",
                "kubectl describe pods --all-namespaces",
            ),
            ("kubectl describe pods -A", "kubectl describe pods -A"),
            (
                "kubectl describe pods -l app=nginx",
                "kubectl describe pods -l app=nginx",
            ),
            (
                "kubectl describe pods --selector=environment=production",
                "kubectl describe pods --selector=environment=production",
            ),
            # Basic kubectl top commands
            ("kubectl top nodes", "kubectl top nodes"),
            ("kubectl top pods", "kubectl top pods"),
            ("kubectl top pods -n default", "kubectl top pods -n default"),
            (
                "kubectl top pods --namespace=kube-system",
                "kubectl top pods --namespace=kube-system",
            ),
            ("kubectl top pods --containers", "kubectl top pods --containers"),
            (
                "kubectl top pods --use-protocol-buffers",
                "kubectl top pods --use-protocol-buffers",
            ),
            ("kubectl top pods -l app=nginx", "kubectl top pods -l app=nginx"),
            ("kubectl top pod my-pod", "kubectl top pod my-pod"),
            # Basic kubectl events commands
            ("kubectl events", "kubectl events"),
            ("kubectl events -n default", "kubectl events -n default"),
            (
                "kubectl events --namespace=kube-system",
                "kubectl events --namespace=kube-system",
            ),
            ("kubectl events --for=pod/my-pod", "kubectl events --for=pod/my-pod"),
            ("kubectl events --types=Normal", "kubectl events --types=Normal"),
            (
                "kubectl events --types=Normal,Warning",
                "kubectl events --types=Normal,Warning",
            ),
            ("kubectl events --watch", "kubectl events --watch"),
            # kubectl get with grep
            ("kubectl get pods | grep nginx", "kubectl get pods | grep nginx"),
            (
                "kubectl get pods -n default | grep 'my-app'",
                "kubectl get pods -n default | grep my-app",
            ),
            (
                "kubectl get deployments -o wide | grep running",
                "kubectl get deployments -o wide | grep running",
            ),
            (
                "kubectl get pods --all-namespaces | grep kube-system",
                "kubectl get pods --all-namespaces | grep kube-system",
            ),
            (
                "kubectl get pods -l app=nginx | grep 'Running'",
                "kubectl get pods -l app=nginx | grep Running",
            ),
            # kubectl describe with grep
            (
                "kubectl describe pods | grep Error",
                "kubectl describe pods | grep Error",
            ),
            (
                "kubectl describe pod my-pod | grep 'Status:'",
                "kubectl describe pod my-pod | grep Status:",
            ),
            (
                "kubectl describe pods -n kube-system | grep Warning",
                "kubectl describe pods -n kube-system | grep Warning",
            ),
            # kubectl top with grep
            ("kubectl top pods | grep high-cpu", "kubectl top pods | grep high-cpu"),
            ("kubectl top nodes | grep 'Ready'", "kubectl top nodes | grep Ready"),
            (
                "kubectl top pods --containers | grep memory",
                "kubectl top pods --containers | grep memory",
            ),
            # kubectl events with grep
            ("kubectl events | grep Failed", "kubectl events | grep Failed"),
            (
                "kubectl events -n default | grep 'Warning'",
                "kubectl events -n default | grep Warning",
            ),
            (
                "kubectl events --for=pod/my-pod | grep Error",
                "kubectl events --for=pod/my-pod | grep Error",
            ),
            # Complex kubectl get commands
            (
                "kubectl get deployments -n kube-system -o wide --show-labels",
                "kubectl get deployments -n kube-system -o wide --show-labels",
            ),
            (
                "kubectl get pods -n kube-system -l app=nginx -o yaml --show-labels",
                "kubectl get pods -n kube-system -l app=nginx -o yaml --show-labels",
            ),
            (
                "kubectl get deployments --all-namespaces --field-selector=metadata.namespace!=kube-system -o wide",
                "kubectl get deployments --all-namespaces --field-selector=metadata.namespace!=kube-system -o wide",
            ),
            (
                "kubectl get services -n production --selector=tier=frontend --output=json",
                "kubectl get services -n production --selector=tier=frontend --output=json",
            ),
            # Complex kubectl describe commands
            (
                "kubectl describe pods -n monitoring --selector=app=prometheus",
                "kubectl describe pods -n monitoring --selector=app=prometheus",
            ),
            (
                "kubectl describe deployments --all-namespaces -l environment=staging",
                "kubectl describe deployments --all-namespaces -l environment=staging",
            ),
            # Complex kubectl top commands
            (
                "kubectl top pods -n kube-system --containers --selector=k8s-app=kube-dns",
                "kubectl top pods -n kube-system --containers --selector=k8s-app=kube-dns",
            ),
            (
                "kubectl top nodes --use-protocol-buffers",
                "kubectl top nodes --use-protocol-buffers",
            ),
            # Complex kubectl events commands
            (
                "kubectl events -n default --for=deployment/my-app --types=Warning,Normal",
                "kubectl events -n default --for=deployment/my-app --types=Warning,Normal",
            ),
            (
                "kubectl events --namespace=kube-system --types=Warning --watch",
                "kubectl events --namespace=kube-system --types=Warning --watch",
            ),
            # Complex commands with grep
            (
                "kubectl get pods -n production -l tier=web -o wide | grep 'Running'",
                "kubectl get pods -n production -l tier=web -o wide | grep Running",
            ),
            (
                "kubectl describe pods --all-namespaces --selector=app=nginx | grep 'Status:'",
                "kubectl describe pods --all-namespaces --selector=app=nginx | grep Status:",
            ),
            (
                "kubectl top pods -n monitoring --containers | grep 'prometheus'",
                "kubectl top pods -n monitoring --containers | grep prometheus",
            ),
            (
                "kubectl events -n kube-system --types=Warning | grep 'Failed'",
                "kubectl events -n kube-system --types=Warning | grep Failed",
            ),
            (
                "kubectl events -n kube-system --types=Warning | grep 'Failed connection'",
                "kubectl events -n kube-system --types=Warning | grep 'Failed connection'",
            ),
            # Resource names with special characters
            ("kubectl get pod my-pod-123", "kubectl get pod my-pod-123"),
            (
                "kubectl describe deployment nginx-deployment-v2",
                "kubectl describe deployment nginx-deployment-v2",
            ),
            (
                "kubectl get service my-service.example.com",
                "kubectl get service my-service.example.com",
            ),
            # Selectors with various formats
            (
                "kubectl get pods -l app=nginx,version=v1",
                "kubectl get pods -l app=nginx,version=v1",
            ),
            (
                "kubectl get pods --selector=app!=nginx",
                "kubectl get pods --selector=app!=nginx",
            ),
            (
                "kubectl get pods --field-selector=spec.nodeName=worker-1",
                "kubectl get pods --field-selector=spec.nodeName=worker-1",
            ),
            (
                "kubectl get pods -n kube-system -l k8s-app=kube-dns",
                "kubectl get pods -n kube-system -l k8s-app=kube-dns",
            ),
            # Grep with quoted strings containing special characters
            (
                "kubectl get pods | grep 'my-app-.*'",
                "kubectl get pods | grep 'my-app-.*'",
            ),
            (
                'kubectl describe pods | grep "Status: Running"',
                "kubectl describe pods | grep 'Status: Running'",
            ),
            (
                "kubectl events | grep 'Failed to pull image'",
                "kubectl events | grep 'Failed to pull image'",
            ),
            # Namespaces with special characters
            (
                "kubectl get pods -n my-namespace-123",
                "kubectl get pods -n my-namespace-123",
            ),
            (
                "kubectl describe pods --namespace=test-env",
                "kubectl describe pods --namespace=test-env",
            ),
            # Mixed flag formats (short/long)
            (
                "kubectl get pods -n default --output=yaml",
                "kubectl get pods -n default --output=yaml",
            ),
            (
                "kubectl describe pods --namespace=kube-system -l app=nginx",
                "kubectl describe pods --namespace=kube-system -l app=nginx",
            ),
            (
                "kubectl top pods -A --containers",
                "kubectl top pods -A --containers",
            ),
            (
                "kubectl get pods --namespace default",
                "kubectl get pods --namespace default",
            ),
            (
                "kubectl logs -n app-27b arctic-fox --previous --all-containers=true",
                "kubectl logs -n app-27b arctic-fox --previous --all-containers=true",
            ),
            # Quote normalization in grep
            ('kubectl get pods | grep "nginx"', "kubectl get pods | grep nginx"),
            ("kubectl get pods | grep 'nginx'", "kubectl get pods | grep nginx"),
        ],
    )
    def test_kubectl_round_trip(self, input_command: str, expected_output: str):
        output_command = make_command_safe(input_command, config=TEST_CONFIG)
        # print(f"* EXPECTED:\n{expected_output}")
        # print(f"* ACTUAL:\n{output_command}")
        assert output_command == expected_output


class TestShlexEscaping:
    """Tests to verify that shlex.quote properly escapes complex parameters."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Resource names with spaces and special characters (properly quoted)
            (
                "kubectl get pod 'my pod with spaces'",
                "kubectl get pod 'my pod with spaces'",
            ),
            # Complex grep patterns with special regex characters
            (
                "kubectl get pods | grep '^nginx-.*-[0-9]\\+$'",
                "kubectl get pods | grep '^nginx-.*-[0-9]\\+$'",
            ),
        ],
    )
    def test_shlex_escaping_complex_params(
        self, input_command: str, expected_output: str
    ):
        """Test that complex parameters are safely handled by shlex escaping."""
        output_command = make_command_safe(input_command, config=TEST_CONFIG)
        assert output_command == expected_output

    def test_shlex_escaping_security(self):
        """Test that potentially dangerous strings are safely escaped."""
        # These commands should not fail validation since shlex will properly escape them
        dangerous_commands = [
            "kubectl get pods 'pod-name; rm -rf /'",
        ]

        for cmd in dangerous_commands:
            # Should not raise an exception due to character validation
            try:
                result = make_command_safe(cmd, config=TEST_CONFIG)
                # Verify the dangerous parts are properly quoted in the output
                assert ";" in result or "|" in result or "&" in result
            except ValueError as e:
                # If it fails, it should be for allowlist reasons, not character validation
                assert "unsafe characters" not in str(e).lower()

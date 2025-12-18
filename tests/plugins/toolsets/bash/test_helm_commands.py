"""
Tests for Helm CLI command parsing, validation, and stringification.

This module tests the Helm CLI integration in the bash toolset, ensuring:
1. Safe Helm commands are allowed and properly parsed
2. Unsafe Helm commands are blocked with appropriate error messages
3. Helm command options are validated correctly
4. Commands are properly stringified back to safe command strings
"""

import pytest
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestHelmCliSafeCommands:
    """Test Helm CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Release management (read-only)
            ("helm list", "helm list"),
            ("helm ls", "helm ls"),
            ("helm list --all-namespaces", "helm list --all-namespaces"),
            ("helm list -A", "helm list -A"),
            ("helm list --namespace production", "helm list --namespace production"),
            ("helm list -n production", "helm list -n production"),
            ("helm list --output json", "helm list --output json"),
            ("helm list -o json", "helm list -o json"),
            ("helm list --output table", "helm list --output table"),
            ("helm list --output yaml", "helm list --output yaml"),
            ("helm list --short", "helm list --short"),
            ("helm list -q", "helm list -q"),
            ("helm list --date", "helm list --date"),
            ("helm list --deployed", "helm list --deployed"),
            ("helm list --failed", "helm list --failed"),
            ("helm list --pending", "helm list --pending"),
            ("helm list --superseded", "helm list --superseded"),
            ("helm list --uninstalled", "helm list --uninstalled"),
            ("helm list --filter nginx", "helm list --filter nginx"),
            ("helm list --selector app=web", "helm list --selector app=web"),
            ("helm list -l app=web", "helm list -l app=web"),
            ("helm list --max 10", "helm list --max 10"),
            ("helm list --offset 5", "helm list --offset 5"),
            ("helm list --reverse", "helm list --reverse"),
            ("helm list -r", "helm list -r"),
            # Get commands
            ("helm get all myrelease", "helm get all myrelease"),
            ("helm get hooks myrelease", "helm get hooks myrelease"),
            ("helm get manifest myrelease", "helm get manifest myrelease"),
            ("helm get notes myrelease", "helm get notes myrelease"),
            ("helm get values myrelease", "helm get values myrelease"),
            ("helm get metadata myrelease", "helm get metadata myrelease"),
            (
                "helm get values myrelease --output json",
                "helm get values myrelease --output json",
            ),
            ("helm get values myrelease -o yaml", "helm get values myrelease -o yaml"),
            (
                "helm get values myrelease --revision 2",
                "helm get values myrelease --revision 2",
            ),
            (
                "helm get manifest myrelease --revision 1",
                "helm get manifest myrelease --revision 1",
            ),
            (
                "helm get all myrelease --namespace production",
                "helm get all myrelease --namespace production",
            ),
            (
                "helm get all myrelease -n production",
                "helm get all myrelease -n production",
            ),
            # Status commands
            ("helm status myrelease", "helm status myrelease"),
            (
                "helm status myrelease --output json",
                "helm status myrelease --output json",
            ),
            ("helm status myrelease -o yaml", "helm status myrelease -o yaml"),
            (
                "helm status myrelease --namespace production",
                "helm status myrelease --namespace production",
            ),
            (
                "helm status myrelease -n production",
                "helm status myrelease -n production",
            ),
            (
                "helm status myrelease --revision 2",
                "helm status myrelease --revision 2",
            ),
            # History commands
            ("helm history myrelease", "helm history myrelease"),
            (
                "helm history myrelease --output json",
                "helm history myrelease --output json",
            ),
            ("helm history myrelease -o table", "helm history myrelease -o table"),
            (
                "helm history myrelease --namespace production",
                "helm history myrelease --namespace production",
            ),
            (
                "helm history myrelease -n production",
                "helm history myrelease -n production",
            ),
            ("helm history myrelease --max 5", "helm history myrelease --max 5"),
            # Show commands
            ("helm show chart nginx", "helm show chart nginx"),
            ("helm show values nginx", "helm show values nginx"),
            ("helm show readme nginx", "helm show readme nginx"),
            ("helm show all nginx", "helm show all nginx"),
            ("helm show crds nginx", "helm show crds nginx"),
            ("helm show chart stable/nginx", "helm show chart stable/nginx"),
            (
                "helm show values bitnami/nginx --version 1.0.0",
                "helm show values bitnami/nginx --version 1.0.0",
            ),
            ("helm show chart ./mychart", "helm show chart ./mychart"),
            (
                "helm show values https://example.com/charts/nginx-1.0.0.tgz",
                "helm show values https://example.com/charts/nginx-1.0.0.tgz",
            ),
            # Search commands
            ("helm search repo nginx", "helm search repo nginx"),
            (
                "helm search repo nginx --max-col-width 50",
                "helm search repo nginx --max-col-width 50",
            ),
            ("helm search repo nginx --devel", "helm search repo nginx --devel"),
            (
                "helm search repo nginx --version 1.0.0",
                "helm search repo nginx --version 1.0.0",
            ),
            ("helm search repo nginx --versions", "helm search repo nginx --versions"),
            ("helm search repo nginx --regexp", "helm search repo nginx --regexp"),
            ("helm search repo nginx -r", "helm search repo nginx -r"),
            (
                "helm search repo nginx --output json",
                "helm search repo nginx --output json",
            ),
            ("helm search repo nginx -o table", "helm search repo nginx -o table"),
            # Lint commands
            ("helm lint ./mychart", "helm lint ./mychart"),
            ("helm lint ./mychart --strict", "helm lint ./mychart --strict"),
            (
                "helm lint ./mychart --values values.yaml",
                "helm lint ./mychart --values values.yaml",
            ),
            (
                "helm lint ./mychart -f values.yaml",
                "helm lint ./mychart -f values.yaml",
            ),
            (
                "helm lint ./mychart --set image.tag=v2.0",
                "helm lint ./mychart --set image.tag=v2.0",
            ),
            (
                "helm lint ./mychart --with-subcharts",
                "helm lint ./mychart --with-subcharts",
            ),
            ("helm lint ./mychart --quiet", "helm lint ./mychart --quiet"),
            # Verify commands
            ("helm verify mychart-1.0.0.tgz", "helm verify mychart-1.0.0.tgz"),
            (
                "helm verify mychart-1.0.0.tgz --keyring ~/.gnupg/pubring.gpg",
                "helm verify mychart-1.0.0.tgz --keyring '~/.gnupg/pubring.gpg'",
            ),
            # Dependency commands (read-only)
            ("helm dependency list ./mychart", "helm dependency list ./mychart"),
            # Repository commands (read-only)
            ("helm repo list", "helm repo list"),
            # Plugin commands (read-only)
            ("helm plugin list", "helm plugin list"),
            # Help and information
            ("helm help", "helm help"),
            ("helm version", "helm version"),
            ("helm version --short", "helm version --short"),
            ("helm version --client", "helm version --client"),
            (
                "helm version --template '{{.Version}}'",
                "helm version --template '{{.Version}}'",
            ),
            # Completion
            ("helm completion bash", "helm completion bash"),
            ("helm completion zsh", "helm completion zsh"),
            ("helm completion fish", "helm completion fish"),
            ("helm completion powershell", "helm completion powershell"),
            # Commands with Kubernetes context options
            (
                "helm list --kube-context production",
                "helm list --kube-context production",
            ),
            (
                "helm list --kubeconfig ~/.kube/config",
                "helm list --kubeconfig '~/.kube/config'",
            ),
            (
                "helm list --kube-apiserver https://k8s.example.com",
                "helm list --kube-apiserver https://k8s.example.com",
            ),
            ("helm list --kube-token mytoken", "helm list --kube-token mytoken"),
            ("helm list --kube-as-user admin", "helm list --kube-as-user admin"),
            (
                "helm list --kube-as-group system:masters",
                "helm list --kube-as-group system:masters",
            ),
            # Commands with debug and logging options
            ("helm list --debug", "helm list --debug"),
            ("helm lint ./mychart --debug", "helm lint ./mychart --debug"),
            # Commands with repository configuration
            (
                "helm list --repository-config ~/.config/helm/repositories.yaml",
                "helm list --repository-config '~/.config/helm/repositories.yaml'",
            ),
            (
                "helm list --repository-cache ~/.cache/helm/repository",
                "helm list --repository-cache '~/.cache/helm/repository'",
            ),
            (
                "helm search repo nginx --repository-config ~/.config/helm/repositories.yaml",
                "helm search repo nginx --repository-config '~/.config/helm/repositories.yaml'",
            ),
            # Commands with time formatting
            (
                "helm list --time-format '2006-01-02 15:04:05'",
                "helm list --time-format '2006-01-02 15:04:05'",
            ),
            (
                "helm history myrelease --time-format RFC3339",
                "helm history myrelease --time-format RFC3339",
            ),
            # Diff commands (if diff plugin available)
            ("helm diff revision myrelease 1 2", "helm diff revision myrelease 1 2"),
            ("helm diff upgrade myrelease nginx", "helm diff upgrade myrelease nginx"),
            # Help for subcommands
            ("helm list --help", "helm list --help"),
        ],
    )
    def test_helm_safe_commands(self, input_command: str, expected_output: str):
        """Test that safe Helm commands are parsed and stringified correctly."""
        config = BashExecutorConfig()
        output_command = make_command_safe(input_command, config=config)
        assert output_command == expected_output


class TestHelmCliUnsafeCommands:
    """Test Helm CLI unsafe commands that should be rejected."""

    @pytest.mark.parametrize(
        "command,expected_exception,partial_error_message_content",
        [
            # Release lifecycle operations
            ("helm install myrelease nginx", ValueError, "Command is blocked"),
            ("helm upgrade myrelease nginx", ValueError, "Command is blocked"),
            ("helm uninstall myrelease", ValueError, "Command is blocked"),
            ("helm delete myrelease", ValueError, "Command is blocked"),
            ("helm rollback myrelease 1", ValueError, "Command is blocked"),
            ("helm test myrelease", ValueError, "Command is blocked"),
            # Repository management
            (
                "helm repo add stable https://charts.helm.sh/stable",
                ValueError,
                "Command is blocked",
            ),
            ("helm repo remove stable", ValueError, "Command is blocked"),
            ("helm repo rm stable", ValueError, "Command is blocked"),
            ("helm repo update", ValueError, "Command is blocked"),
            ("helm repo index charts", ValueError, "Command is blocked"),
            # Chart packaging and publishing
            ("helm create mychart", ValueError, "Command is blocked"),
            ("helm package mychart", ValueError, "Command is blocked"),
            (
                "helm push mychart.tgz oci://registry.example.com",
                ValueError,
                "Command is blocked",
            ),
            ("helm pull bitnami/nginx", ValueError, "Command is blocked"),
            ("helm fetch bitnami/nginx", ValueError, "Command is blocked"),
            (
                "helm dependency update ./mychart",
                ValueError,
                "Command is blocked",
            ),
            (
                "helm dependency build ./mychart",
                ValueError,
                "Command is blocked",
            ),
            # Plugin management
            (
                "helm plugin install https://github.com/example/helm-plugin",
                ValueError,
                "Command is blocked",
            ),
            (
                "helm plugin uninstall myplugin",
                ValueError,
                "Command is blocked",
            ),
            (
                "helm plugin update myplugin",
                ValueError,
                "Command is blocked",
            ),
            # Registry operations
            (
                "helm registry login registry.example.com",
                ValueError,
                "Command is blocked",
            ),
            (
                "helm registry logout registry.example.com",
                ValueError,
                "Command is blocked",
            ),
            # Environment modification
            ("helm env set HELM_DRIVER sql", ValueError, "Command is blocked"),
            ("helm env unset HELM_DRIVER", ValueError, "Command is blocked"),
            # Configuration modification
            ("helm config list", ValueError, "Command is blocked"),
            ("helm config set driver sql", ValueError, "Command is blocked"),
            # Mapkubeapis operations
            (
                "helm mapkubeapis myrelease",
                ValueError,
                "Command is blocked",
            ),
            # Invalid command
            ("helm nonexistent", ValueError, "not in the allowlist"),
            # Invalid subcommand for valid command
            ("helm get invalid myrelease", ValueError, "not in the allowlist"),
            ("helm show invalid nginx", ValueError, "not in the allowlist"),
            ("helm repo invalid", ValueError, "not in the allowlist"),
            # Invalid release name format (blocked because template is blocked)
            (
                "helm template 'Invalid-Release-Name!' nginx",
                ValueError,
                "Command is blocked",
            ),
            (
                "helm template MyRelease nginx",
                ValueError,
                "Command is blocked",
            ),
            # Invalid repository URL format (blocked because repo index is blocked)
            (
                "helm repo index charts --url ftp://invalid.com",
                ValueError,
                "Command is blocked",
            ),
            (
                "helm repo index charts --url javascript:alert(1)",
                ValueError,
                "Command is blocked",
            ),
            # Unknown flags
            (
                "helm template myrelease nginx --evil-option",
                ValueError,
                "Command is blocked",
            ),
            # Operations that could modify cluster state
            (
                "helm template myrelease nginx --atomic",
                ValueError,
                "Command is blocked",
            ),
            ("helm template myrelease nginx --wait", ValueError, "Command is blocked"),
            ("helm template myrelease nginx --force", ValueError, "Command is blocked"),
        ],
    )
    def test_helm_unsafe_commands(
        self, command: str, expected_exception: type, partial_error_message_content: str
    ):
        """Test that unsafe Helm commands are properly rejected."""
        config = BashExecutorConfig()
        with pytest.raises(expected_exception) as exc_info:
            make_command_safe(command, config=config)

        if partial_error_message_content:
            assert partial_error_message_content in str(exc_info.value)

"""
Tests for Flux CD CLI command parsing, validation, and safety.

These tests verify:
1. Safe Flux commands are properly parsed and stringified
2. Unsafe Flux commands are rejected
3. Command validation works correctly
"""

import pytest
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestFluxCliSafeCommands:
    """Test Flux CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Version command
            ("flux version", "flux version"),
            ("flux version --client", "flux version --client"),
            # Check command
            ("flux check", "flux check"),
            ("flux check --pre", "flux check --pre"),
            # Stats command
            ("flux stats", "flux stats"),
            # Get all resources
            ("flux get all", "flux get all"),
            ("flux get all -A", "flux get all -A"),
            ("flux get all --all-namespaces", "flux get all --all-namespaces"),
            # Get sources
            ("flux get sources git", "flux get sources git"),
            ("flux get sources git -A", "flux get sources git -A"),
            ("flux get sources git -n flux-system", "flux get sources git -n flux-system"),
            ("flux get sources helm", "flux get sources helm"),
            ("flux get sources oci", "flux get sources oci"),
            ("flux get sources bucket", "flux get sources bucket"),
            ("flux get sources all", "flux get sources all"),
            # Get kustomizations
            ("flux get kustomizations", "flux get kustomizations"),
            ("flux get kustomizations -A", "flux get kustomizations -A"),
            (
                "flux get kustomizations -n production",
                "flux get kustomizations -n production",
            ),
            ("flux get kustomizations myapp", "flux get kustomizations myapp"),
            # Get helmreleases
            ("flux get helmreleases", "flux get helmreleases"),
            ("flux get helmreleases -A", "flux get helmreleases -A"),
            ("flux get helmreleases myrelease", "flux get helmreleases myrelease"),
            # Get alerts
            ("flux get alerts", "flux get alerts"),
            ("flux get alert-providers", "flux get alert-providers"),
            ("flux get receivers", "flux get receivers"),
            # Get images
            ("flux get images all", "flux get images all"),
            ("flux get images policy", "flux get images policy"),
            ("flux get images repository", "flux get images repository"),
            ("flux get images update", "flux get images update"),
            # Get artifacts
            ("flux get artifacts", "flux get artifacts"),
            # Logs command
            ("flux logs", "flux logs"),
            ("flux logs --level error", "flux logs --level error"),
            ("flux logs --since 10m", "flux logs --since 10m"),
            ("flux logs --kind Kustomization", "flux logs --kind Kustomization"),
            (
                "flux logs --kind Kustomization --name myapp",
                "flux logs --kind Kustomization --name myapp",
            ),
            # Events command
            ("flux events", "flux events"),
            ("flux events -A", "flux events -A"),
            ("flux events -n flux-system", "flux events -n flux-system"),
            (
                "flux events --for Kustomization/myapp",
                "flux events --for Kustomization/myapp",
            ),
            # Trace command
            ("flux trace kustomization myapp", "flux trace kustomization myapp"),
            (
                "flux trace kustomization myapp -n flux-system",
                "flux trace kustomization myapp -n flux-system",
            ),
            ("flux trace helmrelease myapp", "flux trace helmrelease myapp"),
            # Tree command
            ("flux tree kustomization myapp", "flux tree kustomization myapp"),
            (
                "flux tree kustomization myapp -n flux-system",
                "flux tree kustomization myapp -n flux-system",
            ),
            # Debug commands
            (
                "flux debug kustomization myapp",
                "flux debug kustomization myapp",
            ),
            (
                "flux debug kustomization myapp -n flux-system",
                "flux debug kustomization myapp -n flux-system",
            ),
            ("flux debug helmrelease myapp", "flux debug helmrelease myapp"),
            # Diff commands
            (
                "flux diff kustomization myapp",
                "flux diff kustomization myapp",
            ),
            (
                "flux diff kustomization myapp -n flux-system",
                "flux diff kustomization myapp -n flux-system",
            ),
            # Export commands (read-only YAML export)
            ("flux export source git myrepo", "flux export source git myrepo"),
            (
                "flux export source git myrepo -n flux-system",
                "flux export source git myrepo -n flux-system",
            ),
            (
                "flux export kustomization myapp",
                "flux export kustomization myapp",
            ),
            ("flux export helmrelease myapp", "flux export helmrelease myapp"),
            # Commands with output format
            ("flux get all -o wide", "flux get all -o wide"),
            ("flux get sources git -o json", "flux get sources git -o json"),
            ("flux get kustomizations -o yaml", "flux get kustomizations -o yaml"),
            # Commands with watch flag
            ("flux get kustomizations -w", "flux get kustomizations -w"),
            ("flux get sources git --watch", "flux get sources git --watch"),
            # Commands with status selector
            (
                "flux get kustomizations --status-selector ready=false",
                "flux get kustomizations --status-selector ready=false",
            ),
        ],
    )
    def test_flux_safe_commands(self, input_command: str, expected_output: str):
        """Test that safe Flux commands are parsed and stringified correctly."""
        config = BashExecutorConfig()
        output_command = make_command_safe(input_command, config=config)
        assert output_command == expected_output


class TestFluxCliUnsafeCommands:
    """Test Flux CLI unsafe commands that should be rejected."""

    @pytest.mark.parametrize(
        "command,expected_exception,partial_error_message_content",
        [
            # Reconcile operations (state-modifying)
            (
                "flux reconcile source git flux-system",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux reconcile kustomization myapp",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux reconcile helmrelease myapp",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux reconcile kustomization myapp --with-source",
                ValueError,
                "Command is blocked",
            ),
            # Suspend operations (state-modifying)
            (
                "flux suspend kustomization myapp",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux suspend helmrelease myapp",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux suspend source git myrepo",
                ValueError,
                "Command is blocked",
            ),
            # Resume operations (state-modifying)
            (
                "flux resume kustomization myapp",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux resume helmrelease myapp",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux resume source git myrepo",
                ValueError,
                "Command is blocked",
            ),
            # Create operations (state-modifying)
            (
                "flux create source git myrepo",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux create kustomization myapp",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux create helmrelease myapp",
                ValueError,
                "Command is blocked",
            ),
            # Delete operations (state-modifying)
            (
                "flux delete source git myrepo",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux delete kustomization myapp",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux delete helmrelease myapp",
                ValueError,
                "Command is blocked",
            ),
            # Bootstrap operations (cluster modification)
            (
                "flux bootstrap github",
                ValueError,
                "Command is blocked",
            ),
            (
                "flux bootstrap gitlab",
                ValueError,
                "Command is blocked",
            ),
            # Install/Uninstall operations
            ("flux install", ValueError, "Command is blocked"),
            ("flux uninstall", ValueError, "Command is blocked"),
            # Push operations (artifact modification)
            (
                "flux push artifact",
                ValueError,
                "Command is blocked",
            ),
            # Build operations
            (
                "flux build kustomization myapp",
                ValueError,
                "Command is blocked",
            ),
            # Invalid command
            ("flux nonexistent", ValueError, "not in the allowlist"),
            # Invalid operation for valid command
            ("flux get invalid-resource", ValueError, "not in the allowlist"),
        ],
    )
    def test_flux_unsafe_commands(
        self, command: str, expected_exception: type, partial_error_message_content: str
    ):
        """Test that unsafe Flux commands are properly rejected."""
        config = BashExecutorConfig()
        with pytest.raises(expected_exception) as exc_info:
            make_command_safe(command, config=config)

        if partial_error_message_content:
            assert partial_error_message_content in str(exc_info.value)


class TestFluxCliEdgeCases:
    """Test edge cases and error conditions for Flux CLI parsing."""

    def test_flux_with_grep_combination(self):
        """Test Flux commands combined with grep."""
        config = BashExecutorConfig()

        # Valid combination
        result = make_command_safe("flux get all | grep myapp", config=config)
        assert result == "flux get all | grep myapp"

        # Invalid - unsafe Flux command with grep
        with pytest.raises(ValueError):
            make_command_safe(
                "flux reconcile kustomization myapp | grep success", config=config
            )

    def test_flux_commands_with_namespaces(self):
        """Test Flux commands with various namespace options."""
        config = BashExecutorConfig()

        # Short namespace flag
        result = make_command_safe("flux get kustomizations -n production", config=config)
        assert result == "flux get kustomizations -n production"

        # Long namespace flag
        result = make_command_safe(
            "flux get kustomizations --namespace production", config=config
        )
        assert result == "flux get kustomizations --namespace production"

        # All namespaces short flag
        result = make_command_safe("flux get all -A", config=config)
        assert result == "flux get all -A"

        # All namespaces long flag
        result = make_command_safe("flux get all --all-namespaces", config=config)
        assert result == "flux get all --all-namespaces"

    def test_flux_logs_with_various_options(self):
        """Test Flux logs command with various filtering options."""
        config = BashExecutorConfig()

        # With level filter
        result = make_command_safe("flux logs --level error", config=config)
        assert result == "flux logs --level error"

        # With since filter
        result = make_command_safe("flux logs --since 1h", config=config)
        assert result == "flux logs --since 1h"

        # With kind and name
        complex_cmd = "flux logs --kind Kustomization --name myapp --level info"
        result = make_command_safe(complex_cmd, config=config)
        assert "--kind Kustomization" in result
        assert "--name myapp" in result
        assert "--level info" in result

    def test_flux_case_sensitivity(self):
        """Test that Flux commands are case-sensitive where appropriate."""
        config = BashExecutorConfig()

        # Command should be lowercase
        with pytest.raises(ValueError):
            make_command_safe("flux GET all", config=config)

        # Subcommands should match exactly
        with pytest.raises(ValueError):
            make_command_safe("flux get ALL", config=config)

    def test_flux_get_with_status_selector(self):
        """Test Flux get commands with status selector."""
        config = BashExecutorConfig()

        # Status selector for filtering
        result = make_command_safe(
            "flux get kustomizations --status-selector ready=false", config=config
        )
        assert "--status-selector ready=false" in result

    def test_flux_export_commands(self):
        """Test Flux export commands (read-only YAML export)."""
        config = BashExecutorConfig()

        # Export source
        result = make_command_safe("flux export source git myrepo", config=config)
        assert result == "flux export source git myrepo"

        # Export kustomization
        result = make_command_safe("flux export kustomization myapp", config=config)
        assert result == "flux export kustomization myapp"

        # Export helmrelease
        result = make_command_safe("flux export helmrelease myapp", config=config)
        assert result == "flux export helmrelease myapp"

        # Export all in namespace
        result = make_command_safe(
            "flux export source git --all -n flux-system", config=config
        )
        assert "flux export source git" in result
        assert "--all" in result

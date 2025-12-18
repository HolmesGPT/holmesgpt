"""
Tests for Azure CLI command parsing, validation, and safety.

These tests verify:
1. Safe Azure commands are properly parsed and stringified
2. Unsafe Azure commands are rejected
3. Command validation works correctly
"""

import pytest
import argparse
from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig
from holmes.plugins.toolsets.bash.parse_command import make_command_safe


class TestAzureCliSafeCommands:
    """Test Azure CLI safe commands that should be allowed."""

    @pytest.mark.parametrize(
        "input_command,expected_output",
        [
            # Account and resource management
            ("az account list", "az account list"),
            ("az account show", "az account show"),
            ("az account list-locations", "az account list-locations"),
            ("az account tenant list", "az account tenant list"),
            ("az group list", "az group list"),
            ("az group show --name mygroup", "az group show --name mygroup"),
            ("az group exists --name mygroup", "az group exists --name mygroup"),
            ("az resource list", "az resource list"),
            (
                "az resource show --ids /subscriptions/12345/resourceGroups/mygroup/providers/Microsoft.Compute/virtualMachines/myvm",
                "az resource show --ids /subscriptions/12345/resourceGroups/mygroup/providers/Microsoft.Compute/virtualMachines/myvm",
            ),
            # Virtual Machine commands
            ("az vm list", "az vm list"),
            (
                "az vm show --name myvm --resource-group mygroup",
                "az vm show --name myvm --resource-group mygroup",
            ),
            ("az vm list-ip-addresses", "az vm list-ip-addresses"),
            (
                "az vm list-sizes --location eastus",
                "az vm list-sizes --location eastus",
            ),
            ("az vm list-skus --location westus", "az vm list-skus --location westus"),
            (
                "az vm list-usage --location centralus",
                "az vm list-usage --location centralus",
            ),
            (
                "az vm list-vm-resize-options --name myvm --resource-group mygroup",
                "az vm list-vm-resize-options --name myvm --resource-group mygroup",
            ),
            (
                "az vm get-instance-view --name myvm --resource-group mygroup",
                "az vm get-instance-view --name myvm --resource-group mygroup",
            ),
            # Network commands - Virtual Networks
            ("az network vnet list", "az network vnet list"),
            (
                "az network vnet show --name myvnet --resource-group mygroup",
                "az network vnet show --name myvnet --resource-group mygroup",
            ),
            (
                "az network vnet subnet list --vnet-name myvnet --resource-group mygroup",
                "az network vnet subnet list --vnet-name myvnet --resource-group mygroup",
            ),
            (
                "az network vnet subnet show --name mysubnet --vnet-name myvnet --resource-group mygroup",
                "az network vnet subnet show --name mysubnet --vnet-name myvnet --resource-group mygroup",
            ),
            # Network Security Groups
            ("az network nsg list", "az network nsg list"),
            (
                "az network nsg show --name mynsg --resource-group mygroup",
                "az network nsg show --name mynsg --resource-group mygroup",
            ),
            (
                "az network nsg rule list --nsg-name mynsg --resource-group mygroup",
                "az network nsg rule list --nsg-name mynsg --resource-group mygroup",
            ),
            (
                "az network nsg rule show --name myrule --nsg-name mynsg --resource-group mygroup",
                "az network nsg rule show --name myrule --nsg-name mynsg --resource-group mygroup",
            ),
            # Public IP addresses
            ("az network public-ip list", "az network public-ip list"),
            (
                "az network public-ip show --name myip --resource-group mygroup",
                "az network public-ip show --name myip --resource-group mygroup",
            ),
            # Load Balancers
            ("az network lb list", "az network lb list"),
            (
                "az network lb show --name mylb --resource-group mygroup",
                "az network lb show --name mylb --resource-group mygroup",
            ),
            (
                "az network lb frontend-ip list --lb-name mylb --resource-group mygroup",
                "az network lb frontend-ip list --lb-name mylb --resource-group mygroup",
            ),
            (
                "az network lb rule list --lb-name mylb --resource-group mygroup",
                "az network lb rule list --lb-name mylb --resource-group mygroup",
            ),
            # Application Gateway
            (
                "az network application-gateway list",
                "az network application-gateway list",
            ),
            (
                "az network application-gateway show --name mygateway --resource-group mygroup",
                "az network application-gateway show --name mygateway --resource-group mygroup",
            ),
            (
                "az network application-gateway show-backend-health --name mygateway --resource-group mygroup",
                "az network application-gateway show-backend-health --name mygateway --resource-group mygroup",
            ),
            # Storage commands
            ("az storage account list", "az storage account list"),
            (
                "az storage account show --name mystorageaccount",
                "az storage account show --name mystorageaccount",
            ),
            (
                "az storage account show-usage --location eastus",
                "az storage account show-usage --location eastus",
            ),
            (
                "az storage account check-name --name teststorage",
                "az storage account check-name --name teststorage",
            ),
            (
                "az storage container list --account-name mystorageaccount",
                "az storage container list --account-name mystorageaccount",
            ),
            (
                "az storage container show --name mycontainer --account-name mystorageaccount",
                "az storage container show --name mycontainer --account-name mystorageaccount",
            ),
            (
                "az storage blob list --container-name mycontainer --account-name mystorageaccount",
                "az storage blob list --container-name mycontainer --account-name mystorageaccount",
            ),
            (
                "az storage blob show --name myblob --container-name mycontainer --account-name mystorageaccount",
                "az storage blob show --name myblob --container-name mycontainer --account-name mystorageaccount",
            ),
            # AKS commands
            ("az aks list", "az aks list"),
            (
                "az aks show --name mycluster --resource-group mygroup",
                "az aks show --name mycluster --resource-group mygroup",
            ),
            (
                "az aks get-versions --location eastus",
                "az aks get-versions --location eastus",
            ),
            (
                "az aks get-upgrades --name mycluster --resource-group mygroup",
                "az aks get-upgrades --name mycluster --resource-group mygroup",
            ),
            (
                "az aks nodepool list --cluster-name mycluster --resource-group mygroup",
                "az aks nodepool list --cluster-name mycluster --resource-group mygroup",
            ),
            (
                "az aks nodepool show --name mynodepool --cluster-name mycluster --resource-group mygroup",
                "az aks nodepool show --name mynodepool --cluster-name mycluster --resource-group mygroup",
            ),
            # Monitoring commands
            (
                "az monitor metrics list --resource /subscriptions/12345/resourceGroups/mygroup/providers/Microsoft.Compute/virtualMachines/myvm",
                "az monitor metrics list --resource /subscriptions/12345/resourceGroups/mygroup/providers/Microsoft.Compute/virtualMachines/myvm",
            ),
            (
                "az monitor metrics list-definitions --resource /subscriptions/12345/resourceGroups/mygroup/providers/Microsoft.Compute/virtualMachines/myvm",
                "az monitor metrics list-definitions --resource /subscriptions/12345/resourceGroups/mygroup/providers/Microsoft.Compute/virtualMachines/myvm",
            ),
            ("az monitor activity-log list", "az monitor activity-log list"),
            (
                "az monitor activity-log list-categories",
                "az monitor activity-log list-categories",
            ),
            (
                "az monitor log-analytics workspace list",
                "az monitor log-analytics workspace list",
            ),
            (
                "az monitor log-analytics workspace show --workspace-name myworkspace --resource-group mygroup",
                "az monitor log-analytics workspace show --workspace-name myworkspace --resource-group mygroup",
            ),
            # App Service commands
            ("az appservice plan list", "az appservice plan list"),
            (
                "az appservice plan show --name myplan --resource-group mygroup",
                "az appservice plan show --name myplan --resource-group mygroup",
            ),
            (
                "az appservice list-locations --sku S1",
                "az appservice list-locations --sku S1",
            ),
            ("az webapp list", "az webapp list"),
            (
                "az webapp show --name mywebapp --resource-group mygroup",
                "az webapp show --name mywebapp --resource-group mygroup",
            ),
            ("az webapp list-runtimes", "az webapp list-runtimes"),
            # Key Vault (safe operations only)
            ("az keyvault list", "az keyvault list"),
            (
                "az keyvault show --name mykeyvault",
                "az keyvault show --name mykeyvault",
            ),
            ("az keyvault list-deleted", "az keyvault list-deleted"),
            (
                "az keyvault check-name --name testkeyvault",
                "az keyvault check-name --name testkeyvault",
            ),
            # SQL Database commands
            ("az sql server list", "az sql server list"),
            (
                "az sql server show --name myserver --resource-group mygroup",
                "az sql server show --name myserver --resource-group mygroup",
            ),
            (
                "az sql db list --server myserver --resource-group mygroup",
                "az sql db list --server myserver --resource-group mygroup",
            ),
            (
                "az sql db show --name mydatabase --server myserver --resource-group mygroup",
                "az sql db show --name mydatabase --server myserver --resource-group mygroup",
            ),
            # Commands with output formats
            ("az vm list --output json", "az vm list --output json"),
            ("az vm list --output table", "az vm list --output table"),
            ("az group list --output yaml", "az group list --output yaml"),
            (
                "az storage account list --output tsv",
                "az storage account list --output tsv",
            ),
            # Commands with queries
            (
                "az vm list --query '[].{Name:name, Status:powerState}'",
                "az vm list --query '[].{Name:name, Status:powerState}'",
            ),
            ("az group list --query '[].name'", "az group list --query '[].name'"),
            # Commands with resource groups
            (
                "az vm list --resource-group mygroup",
                "az vm list --resource-group mygroup",
            ),
            (
                "az storage account list --resource-group mygroup",
                "az storage account list --resource-group mygroup",
            ),
            # Commands with locations
            (
                "az vm list-sizes --location eastus",
                "az vm list-sizes --location eastus",
            ),
            (
                "az storage account show-usage --location westus",
                "az storage account show-usage --location westus",
            ),
            # Commands with subscriptions
            (
                "az vm list --subscription 12345678-1234-1234-1234-123456789012",
                "az vm list --subscription 12345678-1234-1234-1234-123456789012",
            ),
            (
                "az group list --subscription mysubscription",
                "az group list --subscription mysubscription",
            ),
            # Commands with tags
            (
                "az resource list --tag environment=prod",
                "az resource list --tag environment=prod",
            ),
            ("az vm list --tag owner=devteam", "az vm list --tag owner=devteam"),
            # Commands with max items and pagination
            ("az vm list --max-items 50", "az vm list --max-items 50"),
            (
                "az resource list --skip-token abc123",
                "az resource list --skip-token abc123",
            ),
            # Commands with time ranges for monitoring
            (
                "az monitor activity-log list --start-time 2023-01-01T00:00:00Z --end-time 2023-01-02T00:00:00Z",
                "az monitor activity-log list --start-time 2023-01-01T00:00:00Z --end-time 2023-01-02T00:00:00Z",
            ),
            (
                "az monitor metrics list --resource /subscriptions/12345/resourceGroups/mygroup/providers/Microsoft.Compute/virtualMachines/myvm --start-time 2023-01-01T00:00:00Z --end-time 2023-01-02T00:00:00Z",
                "az monitor metrics list --resource /subscriptions/12345/resourceGroups/mygroup/providers/Microsoft.Compute/virtualMachines/myvm --start-time 2023-01-01T00:00:00Z --end-time 2023-01-02T00:00:00Z",
            ),
            # Commands with debug flags
            ("az vm list --debug", "az vm list --debug"),
            ("az group list --verbose", "az group list --verbose"),
            ("az vm list --only-show-errors", "az vm list --only-show-errors"),
            ("az vm list --no-color", "az vm list --no-color"),
        ],
    )
    def test_azure_safe_commands(self, input_command: str, expected_output: str):
        """Test that safe Azure commands are parsed and stringified correctly."""
        config = BashExecutorConfig()
        output_command = make_command_safe(input_command, config=config)
        assert output_command == expected_output


class TestAzureCliUnsafeCommands:
    """Test Azure CLI unsafe commands that should be rejected."""

    @pytest.mark.parametrize(
        "command,expected_exception,partial_error_message_content",
        [
            # Blocked account operations
            (
                "az account set --subscription mysubscription",
                ValueError,
                "Command is blocked: account set",
            ),
            ("az account clear", ValueError, "Command is blocked: account clear"),
            ("az login", ValueError, "Command is blocked: login"),
            ("az logout", ValueError, "Command is blocked: logout"),
            # State-modifying VM operations
            (
                "az vm create --name myvm --resource-group mygroup",
                ValueError,
                "Command is blocked: vm create",
            ),
            (
                "az vm delete --name myvm --resource-group mygroup",
                ValueError,
                "Command is blocked: vm delete",
            ),
            (
                "az vm start --name myvm --resource-group mygroup",
                ValueError,
                "Command is blocked: vm start",
            ),
            (
                "az vm stop --name myvm --resource-group mygroup",
                ValueError,
                "Command is blocked: vm stop",
            ),
            (
                "az vm restart --name myvm --resource-group mygroup",
                ValueError,
                "Command is blocked: vm restart",
            ),
            (
                "az vm deallocate --name myvm --resource-group mygroup",
                ValueError,
                "Command is blocked: vm deallocate",
            ),
            (
                "az vm resize --name myvm --resource-group mygroup --size Standard_D2s_v3",
                ValueError,
                "Command is blocked: vm resize",
            ),
            (
                "az vm run-command invoke --name myvm --resource-group mygroup --command-id RunShellScript",
                ValueError,
                "Command is blocked: vm run-command",
            ),
            # State-modifying resource operations
            (
                "az group create --name mygroup --location eastus",
                ValueError,
                "Command is blocked: group create",
            ),
            (
                "az group delete --name mygroup",
                ValueError,
                "Command is blocked: group delete",
            ),
            (
                "az resource create --resource-group mygroup --name myresource",
                ValueError,
                "Command is blocked: resource create",
            ),
            (
                "az resource delete --ids /subscriptions/12345/resourceGroups/mygroup",
                ValueError,
                "Command is blocked: resource delete",
            ),
            (
                "az resource tag --ids /subscriptions/12345/resourceGroups/mygroup --tags env=prod",
                ValueError,
                "Command is blocked: resource tag",
            ),
            # State-modifying network operations
            (
                "az network vnet create --name myvnet --resource-group mygroup",
                ValueError,
                "Command is blocked: network vnet create",
            ),
            (
                "az network vnet delete --name myvnet --resource-group mygroup",
                ValueError,
                "Command is blocked: network vnet delete",
            ),
            (
                "az network nsg create --name mynsg --resource-group mygroup",
                ValueError,
                "Command is blocked: network nsg create",
            ),
            (
                "az network nsg rule create --name myrule --nsg-name mynsg --resource-group mygroup",
                ValueError,
                "Command is blocked: network nsg rule create",
            ),
            # State-modifying storage operations
            (
                "az storage account create --name mystorageaccount --resource-group mygroup",
                ValueError,
                "Command is blocked: storage account create",
            ),
            (
                "az storage account delete --name mystorageaccount --resource-group mygroup",
                ValueError,
                "Command is blocked: storage account delete",
            ),
            (
                "az storage container create --name mycontainer --account-name mystorageaccount",
                ValueError,
                "Command is blocked: storage container create",
            ),
            (
                "az storage blob upload --file myfile --container-name mycontainer --account-name mystorageaccount",
                ValueError,
                "Command is blocked: storage blob upload",
            ),
            (
                "az storage blob delete --name myblob --container-name mycontainer --account-name mystorageaccount",
                ValueError,
                "Command is blocked: storage blob delete",
            ),
            # State-modifying AKS operations
            (
                "az aks create --name mycluster --resource-group mygroup",
                ValueError,
                "Command is blocked: aks create",
            ),
            (
                "az aks delete --name mycluster --resource-group mygroup",
                ValueError,
                "Command is blocked: aks delete",
            ),
            (
                "az aks scale --name mycluster --resource-group mygroup --node-count 5",
                ValueError,
                "Command is blocked: aks scale",
            ),
            (
                "az aks upgrade --name mycluster --resource-group mygroup",
                ValueError,
                "Command is blocked: aks upgrade",
            ),
            (
                "az aks get-credentials --name mycluster --resource-group mygroup",
                ValueError,
                "Command is blocked: aks get-credentials",
            ),
            # Sensitive Key Vault operations
            (
                "az keyvault secret list --vault-name mykeyvault",
                ValueError,
                "Command is blocked: keyvault secret",
            ),
            (
                "az keyvault secret show --name mysecret --vault-name mykeyvault",
                ValueError,
                "Command is blocked: keyvault secret",
            ),
            (
                "az keyvault key list --vault-name mykeyvault",
                ValueError,
                "Command is blocked: keyvault key",
            ),
            (
                "az keyvault certificate list --vault-name mykeyvault",
                ValueError,
                "Command is blocked: keyvault certificate",
            ),
            (
                "az keyvault set-policy --name mykeyvault --object-id 12345",
                ValueError,
                "Command is blocked: keyvault set-policy",
            ),
            # State-modifying App Service operations
            (
                "az webapp create --name mywebapp --resource-group mygroup",
                ValueError,
                "Command is blocked: webapp create",
            ),
            (
                "az webapp delete --name mywebapp --resource-group mygroup",
                ValueError,
                "Command is blocked: webapp delete",
            ),
            (
                "az webapp restart --name mywebapp --resource-group mygroup",
                ValueError,
                "Command is blocked: webapp restart",
            ),
            (
                "az webapp deploy --name mywebapp --resource-group mygroup",
                ValueError,
                "Command is blocked: webapp deploy",
            ),
            # Invalid service
            ("az nonexistent list", ValueError, "is not in the allowlist"),
            # Invalid subcommand for valid service
            ("az vm invalid-operation", ValueError, "is not in the allowlist"),
            ("az storage invalid-command", ValueError, "is not in the allowlist"),
            # Blocked operations in complex commands
            ("az ad user list", ValueError, "Command is blocked: ad"),
            ("az role assignment list", ValueError, "Command is blocked: role"),
            ("az policy definition list", ValueError, "Command is blocked: policy"),
            (
                "az deployment group create --resource-group mygroup",
                ValueError,
                "Command is blocked: deployment",
            ),
            # Extension and configuration operations
            (
                "az extension add --name myextension",
                ValueError,
                "Command is blocked: extension",
            ),
            (
                "az configure --defaults group=mygroup",
                ValueError,
                "Command is blocked: configure",
            ),
            # DevOps operations
            ("az devops project list", ValueError, "Command is blocked: devops"),
            ("az repos list", ValueError, "Command is blocked: repos"),
            (
                "az pipelines run --name mypipeline",
                ValueError,
                "Command is blocked: pipelines",
            ),
            # Credential-revealing operations
            (
                "az monitor log-analytics workspace get-shared-keys --workspace-name myworkspace --resource-group mygroup",
                ValueError,
                "Command is blocked: monitor log-analytics workspace get-shared-keys",
            ),
        ],
    )
    def test_azure_unsafe_commands(
        self, command: str, expected_exception: type, partial_error_message_content: str
    ):
        """Test that unsafe Azure commands are properly rejected."""
        config = BashExecutorConfig()
        with pytest.raises(expected_exception) as exc_info:
            make_command_safe(command, config=config)

        if partial_error_message_content:
            assert partial_error_message_content in str(exc_info.value)


class TestAzureCliEdgeCases:
    """Test edge cases and error conditions for Azure CLI parsing."""

    def test_azure_with_grep_combination(self):
        """Test Azure commands combined with grep."""
        config = BashExecutorConfig()

        # Valid combination
        result = make_command_safe("az vm list | grep running", config=config)
        assert result == "az vm list | grep running"

        # Invalid - unsafe Azure command with grep
        with pytest.raises(ValueError):
            make_command_safe(
                "az vm create --name test --resource-group mygroup | grep success",
                config=config,
            )

    def test_azure_empty_service_or_operation(self):
        """Test Azure commands with missing service or operation."""
        config = BashExecutorConfig()

        # Missing service should fail at argument parsing level
        with pytest.raises((argparse.ArgumentError, ValueError)):
            make_command_safe("az", config=config)

    def test_azure_help_commands(self):
        """Test Azure help commands are allowed."""
        config = BashExecutorConfig()

        # General help command
        result = make_command_safe("az help", config=config)
        assert result == "az help"

        # Service with help flag
        result = make_command_safe("az vm list --help", config=config)
        assert result == "az vm list --help"

    def test_azure_complex_valid_parameters(self):
        """Test Azure commands with complex but valid parameters."""
        config = BashExecutorConfig()

        # Complex VM command with multiple filters
        complex_cmd = "az vm list --resource-group mygroup --query '[?powerState==`VM running`].{Name:name, Status:powerState}' --output table"
        result = make_command_safe(complex_cmd, config=config)
        assert "az vm list" in result
        assert "--resource-group mygroup" in result
        assert "--query" in result
        assert "--output table" in result

    def test_azure_nested_subcommands(self):
        """Test Azure commands with nested subcommands."""
        config = BashExecutorConfig()

        # Valid nested command
        result = make_command_safe(
            "az network vnet subnet list --vnet-name myvnet --resource-group mygroup",
            config=config,
        )
        assert (
            result
            == "az network vnet subnet list --vnet-name myvnet --resource-group mygroup"
        )

        # Invalid nested subcommand
        with pytest.raises(ValueError):
            make_command_safe(
                "az network vnet subnet create --name mysubnet --vnet-name myvnet --resource-group mygroup",
                config=config,
            )

    def test_azure_case_sensitivity(self):
        """Test that Azure commands handle case appropriately."""
        config = BashExecutorConfig()

        # Service names should be lowercase
        with pytest.raises(ValueError):
            make_command_safe("az VM list", config=config)

        # Subcommands should match exactly
        with pytest.raises(ValueError):
            make_command_safe("az vm LIST", config=config)

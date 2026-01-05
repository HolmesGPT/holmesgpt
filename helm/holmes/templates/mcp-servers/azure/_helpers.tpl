{{/*
Define the LLM instructions for Azure MCP
*/}}
{{- define "holmes.azureMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.azure.llmInstructions -}}
{{ .Values.mcpAddons.azure.llmInstructions }}
{{- else -}}
IMPORTANT: When investigating Kubernetes issues, ALWAYS check if Azure infrastructure could be the root cause. Many K8s problems originate from Azure-level configurations.

## When to Check Azure

**MUST check Azure when investigating:**
- Connection timeouts or network issues between pods/services
- Pod scheduling failures or node issues (might be AKS node pool problems)
- PersistentVolume mounting failures (Azure Disk/Files issues)
- Ingress/LoadBalancer not accessible (Azure LB/Application Gateway)
- Sudden performance degradation (Azure resource limits/throttling)
- Access denied errors (Azure RBAC/Network policies)
- Storage or database connection issues
- **TLS/SSL errors** (expired certs in Key Vault, App Gateway SSL issues, cert-manager failures)
- **Certificate validation failures** (mTLS between services, ingress cert problems)

## Azure Activity Log (Audit Trail)

The Activity Log shows WHO did WHAT and WHEN. Always check it for recent changes:
```bash
# Find all changes in last 24 hours
az monitor activity-log list --start-time $(date -u -d '24 hours ago' --iso-8601) --query "[?level!='Informational'].{Time:eventTimestamp, Operation:operationName.localizedValue, Status:status.value, Caller:caller}" --output table

# Find changes to specific resource
az monitor activity-log list --resource-id /subscriptions/{sub}/resourceGroups/{rg}/providers/{provider}/{type}/{name} --start-time $(date -u -d '7 days ago' --iso-8601)

# Find who made changes
az monitor activity-log list --caller user@company.com --start-time $(date -u -d '7 days ago' --iso-8601)
```

## Historical Kubernetes Logs in Azure

**CRITICAL**: If Azure logging is enabled for the AKS cluster, you can retrieve logs for Kubernetes resources that no longer exist! This is invaluable for investigating crashed pods, deleted deployments, or terminated jobs.

```bash
# Query Log Analytics for deleted pod logs
az monitor log-analytics query -w WORKSPACE_ID --analytics-query "ContainerLogV2 | where PodName == 'crashed-pod-xxx' | project TimeGenerated, LogMessage, ContainerName" --timespan PT24H

# Get logs for all pods in a namespace from the last hour
az monitor log-analytics query -w WORKSPACE_ID --analytics-query "ContainerLogV2 | where PodNamespace == 'production' | where TimeGenerated > ago(1h)" --timespan PT1H
```

## Network Issues Investigation

### NSG and Firewall Rules
```bash
# Check NSG rules on subnet
az network nsg rule list -g RG_NAME --nsg-name NSG_NAME --output table

# Check effective NSG rules on a NIC
az network nic show-effective-nsg -g RG_NAME -n NIC_NAME

# Find recent NSG changes
az monitor activity-log list --namespace Microsoft.Network --resource-type networkSecurityGroups --start-time $(date -u -d '24 hours ago' --iso-8601)
```

### Load Balancer & Application Gateway
```bash
# Check Load Balancer health
az network lb probe list -g RG_NAME --lb-name LB_NAME

# Check Application Gateway backend health
az network application-gateway show-backend-health -g RG_NAME -n APPGW_NAME

# Check if Application Gateway is blocking traffic
az network application-gateway waf-config show -g RG_NAME --gateway-name APPGW_NAME
```

## Permissions/RBAC Issues

```bash
# Check role assignments for a resource
az role assignment list --scope /subscriptions/{sub}/resourceGroups/{rg}/providers/{provider}/{type}/{name}

# Check service principal permissions
az role assignment list --assignee SP_APP_ID --output table

# Find recent RBAC changes
az monitor activity-log list --namespace Microsoft.Authorization --start-time $(date -u -d '24 hours ago' --iso-8601)

# Check if Managed Identity is configured
az aks show -g RG_NAME -n CLUSTER_NAME --query identity
```

## Certificate Issues

**Common scenarios**: SSL cert expired, cert rotation failed, ingress TLS errors, mTLS failures

### Key Vault Certificates
```bash
# List certificates in Key Vault
az keyvault certificate list --vault-name VAULT_NAME --output table

# Check certificate details and expiry
az keyvault certificate show --vault-name VAULT_NAME -n CERT_NAME --query "attributes.expires"

# Check if certificate auto-rotation is configured
az keyvault certificate show --vault-name VAULT_NAME -n CERT_NAME --query policy.issuerParameters

# Find recent Key Vault operations
az monitor activity-log list --namespace Microsoft.KeyVault --start-time $(date -u -d '24 hours ago' --iso-8601)
```

### Application Gateway SSL
```bash
# List SSL certificates on App Gateway
az network application-gateway ssl-cert list -g RG_NAME --gateway-name APPGW_NAME --output table

# Check listeners using certificates
az network application-gateway http-listener list -g RG_NAME --gateway-name APPGW_NAME --query "[].{Name:name, Protocol:protocol, SslCert:sslCertificate.id}"

# Check if WAF is blocking due to SSL/TLS issues
az monitor activity-log list --resource-id /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Network/applicationGateways/{gw} --start-time $(date -u -d '24 hours ago' --iso-8601)
```

### AKS Ingress Certificates
```bash
# Check if cert-manager is having issues with Azure DNS
az network dns zone list --output table
az network dns record-set txt list -g RG_NAME -z ZONE_NAME

# Check service principal permissions for DNS challenge
az role assignment list --assignee SP_APP_ID --scope /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Network/dnszones/{zone}
```

### App Service/Function Certificates
```bash
# List custom domains and their SSL state
az webapp config hostname list -g RG_NAME --webapp-name APP_NAME --output table

# Check SSL bindings
az webapp config ssl list -g RG_NAME --query "[].{Name:name, ExpirationDate:expirationDate, Thumbprint:thumbprint}"

# Check if managed certificate renewal failed
az monitor activity-log list --namespace Microsoft.Web --resource-type certificates --start-time $(date -u -d '7 days ago' --iso-8601)
```

## Storage & Database Issues

### Storage Account
```bash
# Check storage account network rules
az storage account show -g RG_NAME -n STORAGE_NAME --query networkRuleSet

# Check if private endpoints are configured
az network private-endpoint list -g RG_NAME

# Recent storage configuration changes
az monitor activity-log list --namespace Microsoft.Storage --start-time $(date -u -d '24 hours ago' --iso-8601)
```

### Database (SQL/PostgreSQL/MySQL)
```bash
# Check firewall rules
az sql server firewall-rule list -g RG_NAME -s SERVER_NAME
az postgres server firewall-rule list -g RG_NAME -s SERVER_NAME

# Check VNet rules
az sql server vnet-rule list -g RG_NAME -s SERVER_NAME

# Check connection policy
az sql server conn-policy show -g RG_NAME -s SERVER_NAME
```

## Cost Analysis

```bash
# Get current costs by resource group (using consumption API)
az consumption usage list --start-date 2026-01-01 --end-date 2026-01-04 --query "[].{ResourceGroup:resourceGroup, Cost:pretaxCost, Service:meterDetails.meterCategory}" --output table

# List budgets
az consumption budget list --output table

# Get cost recommendations
az advisor recommendation list --category Cost

# Find expensive resources
az resource list --query "[].{Name:name, Type:type, ResourceGroup:resourceGroup}" --output table
```

## Investigation Workflow

1. **When K8s issue detected**: First check with kubectl, then investigate Azure if:
   - Network connectivity problems exist
   - Resource provisioning fails
   - Performance suddenly degrades
   - Authentication/authorization errors occur

2. **Always check Activity Log** to find what changed and when

3. **Cross-reference timings** between when issues started and Azure changes

4. **For historical data**: Use Log Analytics queries if Azure monitoring is enabled

Remember: Many Kubernetes issues have Azure infrastructure as the root cause. Always investigate both layers.
{{- end -}}
{{- end }}

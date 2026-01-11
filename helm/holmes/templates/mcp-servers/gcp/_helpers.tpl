{{/*
GCP MCP LLM Instructions for gcloud
*/}}
{{- define "holmes.gcpMcp.gcloud.llmInstructions" -}}
{{- if .Values.mcpAddons.gcp.llmInstructions.gcloud -}}
{{ .Values.mcpAddons.gcp.llmInstructions.gcloud }}
{{- else -}}
Use this server to investigate GCP infrastructure and GKE issues across multiple projects.

IMPORTANT: The gcloud MCP uses the 'run_gcloud_command' tool. Pass gcloud arguments as an array in the 'args' parameter.
Do NOT include 'gcloud' itself in the args array - just the subcommands and flags.

MULTI-PROJECT SUPPORT: Always specify --project flag to query specific projects.
The service account can access multiple projects if granted appropriate IAM roles.

Correct format examples:
- To run "gcloud projects list": args: ["projects", "list"]
- To run "gcloud compute instances list --project my-project": args: ["compute", "instances", "list", "--project", "my-project"]
- To run "gcloud container clusters list --project prod-project --region us-central1": args: ["container", "clusters", "list", "--project", "prod-project", "--region", "us-central1"]

Best practices for multi-project queries:
- ALWAYS include --project flag for resource queries
- First list available projects: args: ["projects", "list"]
- Then query specific projects: args: ["compute", "instances", "list", "--project", "PROJECT_ID"]
- You can query different projects in the same investigation

Note: The service account must have permissions in all target projects.

Example multi-project investigations:
- List all accessible projects: args: ["projects", "list", "--format=json"]
- Check GKE clusters across projects:
  - Project A: args: ["container", "clusters", "list", "--project", "project-a", "--region", "us-central1"]
  - Project B: args: ["container", "clusters", "list", "--project", "project-b", "--region", "us-east1"]
- Compare compute instances:
  - Dev: args: ["compute", "instances", "list", "--project", "dev-project"]
  - Prod: args: ["compute", "instances", "list", "--project", "prod-project"]
- Check firewall rules: args: ["compute", "firewall-rules", "list", "--project", "PROJECT_ID"]
- Review service accounts: args: ["iam", "service-accounts", "list", "--project", "PROJECT_ID"]
- Query audit logs: args: ["logging", "read", "logName:cloudaudit.googleapis.com", "--project", "PROJECT_ID", "--limit", "50"]

WHEN TO USE THIS MCP:
• GKE platform issues: Node scheduling failures, cluster autoscaling, workload identity problems
• Cloud networking: Load balancer errors, Cloud NAT issues, cross-project connectivity, SSL certificates
• IAM/Permission errors: 403/permission denied that aren't Kubernetes RBAC, service account issues
• GCP resource problems: Quotas, compute capacity, persistent disk issues
• Managed services: Cloud SQL, Cloud Run, Pub/Sub configuration issues
• Configuration changes: Need to understand what changed at GCP level (use with observability for audit logs)

Common investigations:
- Node issues: args: ["container", "node-pools", "describe", "POOL", "--cluster", "CLUSTER", "--project", "PROJECT"]
- SSL certificates: args: ["compute", "ssl-certificates", "list", "--global", "--project", "PROJECT"]
- Resource quotas: args: ["compute", "project-info", "describe", "--project", "PROJECT"]
- VPC peering: args: ["compute", "networks", "peerings", "list", "--network", "NETWORK", "--project", "PROJECT"]
- Cloud NAT: args: ["compute", "routers", "nats", "list", "--router", "ROUTER", "--region", "REGION", "--project", "PROJECT"]
{{- end -}}
{{- end -}}

{{/*
GCP MCP LLM Instructions for observability
*/}}
{{- define "holmes.gcpMcp.observability.llmInstructions" -}}
{{- if .Values.mcpAddons.gcp.llmInstructions.observability -}}
{{ .Values.mcpAddons.gcp.llmInstructions.observability }}
{{- else -}}
Use this server for GCP-level logs, metrics, traces, and monitoring - especially for audit trails, managed services, and HISTORICAL DATA.

KEY ADVANTAGE: This MCP can retrieve historical logs for deleted Kubernetes resources (pods, jobs, etc.) that are no longer available via kubectl.
Perfect for investigating issues with resources that have already been terminated or replaced.

WHEN TO USE:
• Historical investigations: Logs from deleted pods, completed jobs, or terminated nodes
• Audit investigations: Who made what changes, correlate config changes with errors
• GCP service logs: Cloud SQL, Cloud Run, Cloud Functions, BigQuery logs
• Cross-service tracing: Distributed traces across GCP services
• Cloud metrics: GCP resource utilization, quotas, managed service metrics
• Alert investigation: GCP monitoring alerts and policies
• Permission/auth failures: Cloud audit logs show authorization attempts

CRITICAL: Always answer log questions with actual log entries first.
After providing results, include markdown links with descriptive names that describe WHAT you're filtering for, not just the project:
- Good: [OutOfMemory Errors in Payment Service](https://console.cloud.google.com/logs/query;query=...)
- Good: [SSL Certificate Renewal Failures](https://console.cloud.google.com/logs/query;query=...)
- Good: [Database Connection Timeouts - Last 24h](https://console.cloud.google.com/logs/query;query=...)
- Bad: [View PROJECT_NAME logs](https://console.cloud.google.com/logs/query;query=...)
The link name should describe the filter/search criteria, not the project name.

Available tools:
- List log entries from a project (including historical)
- List log names, buckets, views, sinks, scopes
- List metric descriptors and time series data
- List alert policies
- Search and get traces
- List error group statistics

Common patterns with link examples:
- Deleted pod logs: filter='resource.labels.pod_name="POD_NAME" AND timestamp>"START_TIME"'
  Link: [Historical Logs for Deleted Pod POD_NAME](...)
- OOM kills: filter='jsonPayload.reason="OOMKilling" OR textPayload:"Out of memory"'
  Link: [OutOfMemory Errors](...)
- Audit trail: filter='logName=~"cloudaudit" AND protoPayload.methodName=~"SetIamPolicy"'
  Link: [IAM Permission Changes](...)
- Find changes near errors: filter='logName=~"cloudaudit" AND timestamp>"ERROR_TIME-30m"'
  Link: [Configuration Changes Before Error](...)
- GCP service errors: filter='resource.type="cloud_run_revision" AND severity>=ERROR'
  Link: [Cloud Run Deployment Errors](...)
- Permission issues: filter='protoPayload.status.code=7'
  Link: [Permission Denied Errors](...)
- Quota errors: filter='textPayload:"QUOTA_EXCEEDED" OR jsonPayload.error.code=8'
  Link: [Quota Exceeded Events](...)
{{- end -}}
{{- end -}}

{{/*
GCP MCP LLM Instructions for storage
*/}}
{{- define "holmes.gcpMcp.storage.llmInstructions" -}}
{{- if .Values.mcpAddons.gcp.llmInstructions.storage -}}
{{ .Values.mcpAddons.gcp.llmInstructions.storage }}
{{- else -}}
Use this server when issues involve Cloud Storage buckets or objects.

WHEN TO USE:
• Storage access errors: 403/404 errors accessing GCS from applications
• Data availability: Missing files, lifecycle deletions, versioning issues
• Performance problems: Slow uploads/downloads, regional latency
• Cost issues: Unexpected storage costs, need storage analysis
• Compliance: Encryption, retention, audit requirements for stored data

Available operations:
- List and describe buckets
- List and describe objects
- Check IAM policies and permissions
- Review bucket configurations
- Analyze storage class and lifecycle policies

Common investigations:
- Access denied: First check get_bucket_iam_policy(), then object ACLs
- Missing data: Use list_objects(versions=true) and check lifecycle rules
- Cost analysis: Review storage classes and find large/old objects
- Recovery: Check soft delete policy and object versions
- Performance: Check bucket location and CORS configuration

When providing storage-related findings, include relevant links:
[View Bucket in Console](https://console.cloud.google.com/storage/browser/BUCKET_NAME?project=PROJECT)
{{- end -}}
{{- end -}}

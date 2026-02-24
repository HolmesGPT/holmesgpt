{{/*
Define the LLM instructions for Prefect MCP
*/}}
{{- define "holmes.prefectMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.prefect.llmInstructions -}}
{{ .Values.mcpAddons.prefect.llmInstructions }}
{{- else -}}
This MCP server provides access to Prefect for workflow orchestration monitoring and troubleshooting.

## When to Use This MCP Server

Use the Prefect MCP when investigating:
- Failed or crashed flow runs
- Workflow scheduling issues
- Worker health and work pool problems
- Task run failures and retries
- Deployment configuration issues

## Investigation Workflow

When investigating a failed flow run:
1. Get the flow run details to understand what failed
2. Retrieve the logs for the failed flow/task run
3. Check if the deployment is healthy and workers are running
4. Look at recent runs of the same flow to identify patterns (intermittent vs persistent failures)

When checking overall health:
1. Check work pool and work queue status for stuck or backlogged runs
2. Look for flows in a "Crashed" or "Failed" state
3. Verify deployments are active and scheduled

## Important Guidelines

- Always retrieve logs for failed runs - they contain the actual error messages
- Check work pool status when runs are stuck in "Pending" state
- Compare recent run history to identify if failures are new or recurring
- Look at task-level failures within a flow run for more specific diagnosis
{{- end -}}
{{- end -}}

{{/*
Define the LLM instructions for Confluence MCP
*/}}
{{- define "holmes.confluenceMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.confluenceMcp.llmInstructions -}}
{{ .Values.mcpAddons.confluenceMcp.llmInstructions }}
{{- else -}}
This MCP server provides access to Confluence for searching and retrieving documentation.

## When to Use This MCP Server

Before every investigation, search Confluence for matching runbooks that may contain relevant procedures or troubleshooting steps.

Use the Confluence MCP when:
- You need to find runbooks or standard operating procedures
- You need to look up internal documentation about services or infrastructure
- You want to find post-mortem reports from previous incidents
- You need to retrieve architecture documentation or service ownership information

## Investigation Workflow

1. **Search for runbooks**: Use confluence_search with CQL to find relevant pages
2. **Retrieve page content**: Get the full page content for matching results
3. **Follow procedures**: If a runbook is found, follow its steps during the investigation

## Important Guidelines

- Search using relevant keywords from the alert or issue being investigated
- Use CQL (Confluence Query Language) for precise searches (e.g., `text ~ "runbook" AND text ~ "service-name"`)
- Always check for runbooks before starting manual investigation
- Page content may be in HTML format - extract the relevant text and procedures
{{- end -}}
{{- end -}}

{{/*
Define the LLM instructions for Sentry MCP
*/}}
{{- define "holmes.sentryMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.sentry.llmInstructions -}}
{{ .Values.mcpAddons.sentry.llmInstructions }}
{{- else -}}
This MCP server provides access to Sentry for error tracking and monitoring.

## When to Use This MCP Server

Use the Sentry MCP when investigating:
- Application errors, exceptions, or crashes
- Error spikes or new error patterns
- Stack traces that need deeper context
- Performance issues related to unhandled exceptions

## Investigation Workflow

1. **Identify the project**: List organizations and projects to find the relevant project
2. **Search for errors**: Use search_errors with Sentry query syntax to find matching issues
3. **Get issue details**: Retrieve full issue details including frequency, first/last seen, and assigned owner
4. **Analyze stack traces**: Get event details with full stack traces to understand the root cause
5. **Check patterns**: Look at issue events to determine if the error is intermittent or persistent

## Important Guidelines

- Always start by listing projects to find the correct project slug
- Use Sentry search syntax for filtering (e.g., `is:unresolved`, `assigned:me`, `level:error`)
- When analyzing errors, always retrieve the full event with stack trace
- Check both the frequency and the timeline of issues to understand impact
- Look for related issues that may share the same root cause
{{- end -}}
{{- end -}}

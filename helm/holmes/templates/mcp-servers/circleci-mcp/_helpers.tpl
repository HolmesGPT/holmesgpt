{{/*
Define the LLM instructions for CircleCI MCP
*/}}
{{- define "holmes.circleciMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.circleciMcp.llmInstructions -}}
{{ .Values.mcpAddons.circleciMcp.llmInstructions }}
{{- else -}}
This MCP server provides access to CircleCI pipelines, workflows, jobs, and deployments.

## Navigation

CircleCI resources form a chain — use the id from each level to query the next:

    list_runs → get_run → list_run_workflows → get_workflow → list_workflow_jobs → get_job

Enter at list_runs. Skip levels when you already have an id — for example, go straight to
list_workflow_jobs when you have a workflow id.

## Diagnosing a Failed Job

Start with get_job — it is cheap and identifies the failed step and its exit code.

- If the step ran tests: use list_job_tests (filtered to failures, far cheaper than logs).
- If you need the actual output: use get_job_logs — keep tail_lines small to avoid token overflow.
- If the job died with nothing in its logs: check get_job_resource_usage for an OOM kill.
- To find files the job persisted: use list_job_artifacts.

## When to Use This MCP Server

**Always use it when you see:**
- A failing CircleCI pipeline, workflow, or job in an alert or user message
- Deployment failures that may originate from a CI build
- Questions about what changed in a recent pipeline run

**What to do:**
- Identify the failing job and step before reading logs
- Prefer list_job_tests over raw logs for test failures — it is faster and already filtered
- Correlate the failure timing with recent pipeline triggers (commit, PR, scheduled run)

## Important Guidelines

- Run ids, workflow ids, and job ids are distinct UUIDs — never substitute one for another.
- Nothing accepts a job number or a CircleCI web URL as an id.
- Projects and orgs accept either a slug ("gh/org/repo") or a UUID.
- Keep tail_lines small when calling get_job_logs — logs can be very large.
- For deploy subsystem queries (list_deployments, list_deploy_components, etc.) use the
  project/org identifiers directly — you do not need a pipeline run id.
{{- end -}}
{{- end -}}

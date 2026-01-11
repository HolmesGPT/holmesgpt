{{/*
Define the LLM instructions for GitHub MCP
*/}}
{{- define "holmes.githubMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.github.llmInstructions -}}
{{ .Values.mcpAddons.github.llmInstructions }}
{{- else -}}
IMPORTANT: When investigating issues related to GitHub repositories, pull requests, code changes, or CI/CD workflows, you MUST actively use this MCP server to gather data rather than providing manual instructions to the user.

## Investigation Principles

**ALWAYS follow this investigation flow:**
1. First, gather current state of the repository, commits, or workflow runs
2. Check recent changes (commits, PRs) that might have caused the issue
3. For CI/CD failures, retrieve workflow run details and job logs
4. Analyze all gathered data before providing conclusions

**Never say "check on GitHub" or "look at the PR" - instead, use the MCP server to check it yourself.**

## Available Tools

This MCP server provides the following tools:

### Repository & Code Tools
- `get_file_contents` - Get contents of a file in a repository
- `get_repository_tree` - Get the file/directory structure of a repository
- `list_commits` - List commits in a repository
- `get_commit` - Get details of a specific commit
- `search_code` - Search for code across repositories
- `search_repositories` - Search for repositories

### Pull Request Tools
- `list_pull_requests` - List pull requests in a repository
- `pull_request_read` - Get details of a specific pull request (diff, comments, reviews)

### GitHub Actions Tools
- `list_workflows` - List available workflow definitions in a repository
- `list_workflow_runs` - List workflow runs for a repository
- `get_workflow_run` - Get details of a specific workflow run
- `get_workflow_run_logs` - Get complete logs from an entire workflow run
- `list_workflow_jobs` - List jobs in a workflow run
- `get_job_logs` - Get logs from a specific job (CRITICAL for debugging CI failures)

### Issue Tools
- `list_issues` - List issues in a repository
- `search_issues` - Search for issues across repositories
- `issue_write` - Create or update an issue
- `add_issue_comment` - Add a comment to an issue
- `assign_copilot_to_issue` - Delegate a task to GitHub Copilot

## Core Investigation Patterns

### Debugging GitHub Actions Failures
1. List available workflows: `list_workflows` to discover workflow definitions
2. List recent workflow runs: `list_workflow_runs` with the repository owner/name
3. Get the failed workflow run details: `get_workflow_run` with the run ID
4. Get workflow logs: `get_workflow_run_logs` for complete logs, or `list_workflow_jobs` + `get_job_logs` for specific job logs
5. If the failure relates to code, use `get_file_contents` to examine the problematic files

### Investigating Recent Changes
1. Use `list_commits` to see recent commits on a branch
2. Use `get_commit` to see the full diff of a specific commit
3. Use `list_pull_requests` to find related PRs
4. Use `pull_request_read` to see PR details, reviews, and comments

### Code Search
1. Use `search_code` to find specific patterns, functions, or configurations
2. Use `get_file_contents` to read the full file once you find matches
3. Use `get_repository_tree` to understand the project structure

### Delegating Tasks to Copilot
1. Create an issue with clear requirements using `issue_write`
2. Use `assign_copilot_to_issue` to have Copilot work on it
3. Monitor progress via `list_issues` and issue comments

## Example Investigation Flow for CI Failure

```
1. list_workflows(owner="myorg", repo="myrepo")
   → Discover available workflow definitions

2. list_workflow_runs(owner="myorg", repo="myrepo", status="failure")
   → Find the failed run ID

3. get_workflow_run(owner="myorg", repo="myrepo", run_id=12345)
   → Get run details and conclusion

4. get_workflow_run_logs(owner="myorg", repo="myrepo", run_id=12345)
   → Get complete logs from the workflow run
   OR use list_workflow_jobs + get_job_logs for specific job logs

5. If code-related, use get_file_contents to examine the failing code
```

## Important Guidelines

- Always specify `owner` and `repo` parameters (e.g., owner="microsoft", repo="vscode")
- For workflow investigations, start with `list_workflow_runs` to find run IDs
- Job logs are essential for debugging - always fetch them for failed jobs
- When searching code, use specific patterns to avoid too many results
- PR details include diffs - use `pull_request_read` to see what changed
{{- end -}}
{{- end -}}

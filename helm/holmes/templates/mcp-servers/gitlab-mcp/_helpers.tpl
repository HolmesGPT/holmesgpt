{{/*
Define the LLM instructions for GitLab MCP
*/}}
{{- define "holmes.gitlabMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.gitlabMcp.llmInstructions -}}
{{ .Values.mcpAddons.gitlabMcp.llmInstructions }}
{{- else -}}
This MCP server provides access to GitLab projects, merge requests, issues, pipelines, and code.

IMPORTANT: When you see stack traces, exceptions, or error messages that reference code files, functions, classes, or line numbers - you MUST use this MCP server to look up the relevant code and investigate. Do not just report the error; find and analyze the actual code.

IMPORTANT: After diagnosing an issue, always suggest how to fix it. If you can implement the fix, offer to create a branch and MR. If you need help, offer to create a GitLab issue or delegate to GitLab Duo.

## When to Use This MCP Server

**Always use it when you see:**
- Stack traces or exceptions with file paths, function names, or line numbers
- Error messages mentioning specific code modules or classes
- CI/CD or GitLab pipeline failures
- Issues that might be caused by recent commits or configuration changes

**What to do:**
- Search for and read the relevant source code
- Check recent commits to understand what changed
- Find who made changes and when
- Look for existing issues or MRs about the problem

## Parameter Handling Notes

When calling GitLab MCP tools:
- For optional string parameters (e.g., `search`, `since`, `until`, `path`, `author`, `topic`),
  pass an empty string `""` instead of `null` when you don't need to filter by that field.
- For optional enum parameters (e.g., `visibility`, `state`, `scope`), only pass a valid enum
  value — never pass `null`. Omit or use a broadly-inclusive value if you don't want to filter.
- For optional numeric parameters (e.g., `min_access_level`), only pass a valid integer —
  never pass `null`. Use the lowest valid value (e.g., `10` for Guest) if you want no filtering.
- The `search_repositories` tool requires a non-empty `search` string — it does not support
  listing all projects. Use `list_projects` with `membership: true` to list accessible repos.

## Investigation Scenarios

### Code Issues
When you detect code issues from logs, exceptions, or other sources:
1. Search for the relevant code in the project
2. Check recent commits to find when the problematic code was introduced
3. Identify who made the change and the associated MR
4. Explain why the failure is happening based on the code analysis
5. Check if there are existing open issues or MRs addressing this problem
6. Search closed issues for similar past problems and their solutions

### Configuration Issues
Many users manage configuration in GitLab. When investigating:
1. Check recent commits to configuration files (YAML, JSON, env files, etc.)
2. Identify what changed, when, and who made the change
3. Compare current config with previous versions to spot the breaking change
4. Correlate the change timestamp with when the issue started

### Pipeline/CI Failures
When investigating GitLab CI/CD failures:
1. Check the pipeline status and fetch the job logs (job traces)
2. Identify which stage and job failed
3. Check if the .gitlab-ci.yml (or included CI config) was recently modified
4. If triggered by an MR, examine the MR diff to find the cause
5. Check if it's a flaky test by searching for related issues

### General Investigation Steps
- Always correlate failure timing with recent commits and merges
- Check CODEOWNERS or recent contributors to identify who to involve
- Search for existing issues before suggesting to create new ones
- Look at the MR that triggered the failure when applicable

## Proactive Fixing

Always try to understand how to fix the issue, not just diagnose it.

**Before suggesting a fix:**
- Check if you have Developer/Maintainer access to create branches
- Search for existing MRs that might already address the issue
- Identify the appropriate target branch

**When you know the fix:**
- Offer to create a branch and implement the fix
- Open an MR with a clear description of the problem and solution
- Suggest appropriate reviewers/approvers based on CODEOWNERS or recent contributors

**When you need human help:**
- Offer to create a GitLab issue with your findings
- Suggest assigning the issue to relevant team members
- If GitLab Duo is available, offer to delegate the fix to GitLab Duo

## Important Guidelines

- Always specify the project (namespace/path or project ID) when calling tools
- Fetch job traces for failed pipeline jobs - they contain the actual error messages
- Use code search to find relevant files before reading them
- Check MR diffs (changes) to understand what changed
{{- end -}}
{{- end -}}

{{/*
Define the LLM instructions for GitHub MCP
*/}}
{{- define "holmes.githubMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.github.llmInstructions -}}
{{ .Values.mcpAddons.github.llmInstructions }}
{{- else -}}
This MCP server provides access to GitHub repositories, pull requests, issues, workflows, and code. Use it to investigate issues, find root causes, and suggest or implement fixes.

## Investigation Scenarios

### Code Issues
When you detect code issues from logs, exceptions, or other sources:
1. Search for the relevant code in the repository
2. Check recent commits to find when the problematic code was introduced
3. Identify who made the change and the associated PR
4. Explain why the failure is happening based on the code analysis
5. Check if there are existing open issues or PRs addressing this problem
6. Search closed issues for similar past problems and their solutions

### Configuration Issues
Many users manage configuration in GitHub. When investigating:
1. Check recent commits to configuration files (YAML, JSON, env files, etc.)
2. Identify what changed, when, and who made the change
3. Compare current config with previous versions to spot the breaking change
4. Correlate the change timestamp with when the issue started

### Workflow/Actions Failures
When investigating CI/CD failures:
1. Check the workflow run status and fetch the logs
2. Identify which job and step failed
3. Check if the workflow definition was recently modified
4. If triggered by a PR, examine the PR diff to find the cause
5. Check if it's a flaky test by searching for related issues

### General Investigation Steps
- Always correlate failure timing with recent commits and merges
- Check CODEOWNERS or recent contributors to identify who to involve
- Search for existing issues before suggesting to create new ones
- Look at the PR that triggered the failure when applicable

## Proactive Fixing

Always try to understand how to fix the issue, not just diagnose it.

**Before suggesting a fix:**
- Check if you have write access to create branches
- Search for existing PRs that might already address the issue
- Identify the appropriate base branch

**When you know the fix:**
- Offer to create a branch and implement the fix
- Create a PR with a clear description of the problem and solution
- Suggest appropriate reviewers based on CODEOWNERS or recent contributors

**When you need human help:**
- Offer to create a GitHub issue with your findings
- Suggest assigning the issue to relevant team members
- If Copilot is available, offer to delegate the fix to Copilot

## Important Guidelines

- Always specify owner and repo parameters when calling tools
- Fetch logs for failed workflow jobs - they contain the actual error messages
- Use code search to find relevant files before reading them
- Check PR diffs to understand what changed
{{- end -}}
{{- end -}}

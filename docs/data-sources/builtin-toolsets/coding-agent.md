# Coding Agent

Delegate code fixes to an autonomous coding agent that can clone a repository, apply changes, and open a pull request. After Holmes investigates an issue and identifies a fix, it can hand off the remediation to a coding agent (powered by [Claude Code](https://code.claude.com/)) that makes the actual code changes.

## Prerequisites

- [Claude Code CLI](https://code.claude.com/docs/en/installation) installed and available in PATH
- `ANTHROPIC_API_KEY` environment variable set (the coding agent uses its own LLM calls)
- A GitHub or GitLab personal access token with permission to clone repos and create PRs
- `git` installed

!!! note
    The coding agent uses a **separate** LLM from Holmes itself. Holmes performs the investigation, then passes a task description to the coding agent which uses Claude Code to make changes. You need an `ANTHROPIC_API_KEY` for the coding agent even if Holmes uses a different LLM provider.

## Configuration

=== "GitHub"

    Add the following to **~/.holmes/config.yaml**:

    ```yaml
    toolsets:
      coding_agent:
        enabled: true
        config:
          git_provider_type: github
          git_token: ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  # GitHub PAT with 'repo' scope
    ```

    **Creating a GitHub token:**

    1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
    2. Click **Generate new token (classic)**
    3. Select the `repo` scope (full control of private repositories)
    4. Copy the token and use it as `git_token`

    For fine-grained tokens, grant **Read and Write** access to **Contents** and **Pull requests** on the target repositories.

=== "GitLab"

    Add the following to **~/.holmes/config.yaml**:

    ```yaml
    toolsets:
      coding_agent:
        enabled: true
        config:
          git_provider_type: gitlab
          git_token: glpat-xxxxxxxxxxxxxxxxxxxx  # GitLab PAT with 'api' scope
    ```

    **Creating a GitLab token:**

    1. Go to **User Settings > Access Tokens** (or for project tokens: **Project Settings > Access Tokens**)
    2. Create a token with the `api` scope
    3. Copy the token and use it as `git_token`

    Self-hosted GitLab instances are supported — the toolset extracts the API URL from the repository URL automatically.

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      toolsets:
        coding_agent:
          enabled: true
          config:
            git_provider_type: github
            git_token: "{{ env.GITHUB_TOKEN }}"
    ```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `git_provider_type` | string | `github` | Git provider: `github` or `gitlab` |
| `git_token` | string | *required* | Personal access token for clone + PR creation |
| `max_turns` | int | `30` | Max agentic turns for the coding agent |
| `model` | string | `null` | Model override for the coding agent |
| `draft_pr` | bool | `true` | Create PRs as drafts |
| `timeout_seconds` | int | `600` | Max time (seconds) for the agent to complete |
| `allowed_tools` | list | *safe defaults* | Tools the agent can use |
| `disallowed_tools` | list | *safe defaults* | Tools blocked for the agent |

## Common Use Cases

```
Investigate the failing deployment and open a PR to fix it in https://github.com/org/backend-api
```

```
The health check endpoint is returning 503. Check the logs, find the root cause,
and create a fix PR in https://github.com/org/my-service (branch: main)
```

```
Memory usage is spiking in the payment service. Investigate and if you find a
code-level fix, submit a PR to https://gitlab.com/org/payment-service
```

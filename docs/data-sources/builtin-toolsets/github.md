# Git/GitHub

By enabling this toolset, HolmesGPT will be able to interact with GitHub repositories, read files with line numbers, list repository contents, manage pull requests, and make file changes.

## Prerequisites

1. A GitHub repository URL (e.g., `owner/repo`)
2. A GitHub Fine-grained Personal Access Token (scoped token) with the following permissions:
   - **Contents**: Read and write (required for reading files, creating branches, and making commits on branches to create PRs)
   - **Pull requests**: Read and write (required for creating and updating PRs)

You can create a fine-grained token at [GitHub Settings > Developer settings > Personal access tokens > Fine-grained tokens](https://github.com/settings/tokens?type=beta). When creating the token, select the specific repository and grant the permissions listed above.

## Configuration

=== "Holmes CLI"

    First, set the following environment variables:

    ```bash
    export GIT_REPO="owner/repo"  # e.g., "facebook/react"
    export GIT_CREDENTIALS="<your GitHub personal access token>"
    export GIT_BRANCH="main"  # optional, defaults to "main"
    ```

    Then add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

    ```yaml
    toolsets:
      git:
        enabled: true
        config:
          git_repo: "owner/repo"  # if not set via environment variable
          git_credentials: "<token>"  # if not set via environment variable
          git_branch: "main"  # optional, defaults to "main"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    To test, run:

    ```bash
    holmes ask "List the files in the GitHub repository and read the README file"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
      additionalEnvVars:
        - name: GIT_REPO
          value: "owner/repo"
        - name: GIT_CREDENTIALS
          value: "<your GitHub personal access token>"
        - name: GIT_BRANCH
          value: "main"  # optional
      toolsets:
        git:
          enabled: true
    ```

## Configuration Options

The toolset can be configured via environment variables or config file:

```yaml
toolsets:
  git:
    enabled: true
    config:
      git_repo: "owner/repo"  # GitHub repository (e.g., "facebook/react")
      git_credentials: "<token>"  # GitHub personal access token
      git_branch: "main"  # Branch to use (defaults to "main")
```

**Note:** Environment variables (`GIT_REPO`, `GIT_CREDENTIALS`, `GIT_BRANCH`) take precedence over config file settings.

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| `git_read_file_with_line_numbers` | Read a file from the configured branch with line numbers for each line |
| `git_list_files` | List all files and directories in the configured branch (recursive) |
| `git_list_open_prs` | List all open pull requests in the repository |
| `git_execute_changes` | Make line-based changes to files and optionally create or update PRs |
| `git_update_pr` | Update an existing PR that was created by this tool |

## Tool Details

### git_execute_changes

This tool modifies files line-by-line and can create or update pull requests. It requires a two-step workflow:
1. First run with `dry_run=true` to preview changes
2. Then run with `dry_run=false` to commit changes

**Key Parameters:**
- `line`: Line number where the change occurs
- `command`: Operation type (`insert`, `update`, or `remove`)
- `commit_pr`: Either a PR title (for new PRs) or PR number like `#123` (for updating existing PRs)
- `open_pr`: Set to `true` to create a new PR, `false` to just commit to a branch

**Branch Naming:** When creating new PRs, branches are automatically named as `feature/{pr_title}` with spaces replaced by underscores.

### git_update_pr

This tool updates existing PRs that were created by `git_execute_changes`. It uses the same line-based modification approach and dry_run workflow.

### Reading and Listing Files

- `git_read_file_with_line_numbers` and `git_list_files` operate only on the branch configured in `git_branch` (defaults to "main")
- To read files from other branches, you would need to reconfigure the toolset with a different branch

## Important Notes

- The toolset can only modify PRs it created (tracked internally)
- All file paths should be relative to the repository root
- This toolset is marked as **experimental** and its interface may change in future versions

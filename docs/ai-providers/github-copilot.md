# GitHub Copilot

Configure HolmesGPT to use AI models through your [GitHub Copilot](https://github.com/features/copilot){:target="_blank"} subscription.

!!! note "GitHub Copilot vs GitHub Models"
    This page covers **GitHub Copilot** (subscription-based, uses `github_copilot/` prefix). For **GitHub Models** (token-based, uses `github/` prefix), see the [GitHub Models](github.md) page.

## Prerequisites

- A GitHub Copilot subscription (individual, business, or enterprise)
- LiteLLM handles authentication automatically via OAuth device flow — no API key needed

## Required Headers

GitHub Copilot's API requires IDE-identifying headers on every request. You must configure these headers via the `EXTRA_HEADERS` environment variable or the `extra_headers` field in your model list configuration:

```json
{
  "Editor-Version": "vscode/1.85.1",
  "Editor-Plugin-Version": "copilot-chat/0.26.7",
  "Copilot-Integration-Id": "vscode-chat",
  "User-Agent": "GithubCopilot/1.155.0"
}
```

Without these headers, requests will fail with `"missing Editor-Version header for IDE auth"`.

## Configuration

=== "Holmes CLI"

    ```bash
    export EXTRA_HEADERS='{"Editor-Version": "vscode/1.85.1", "Editor-Plugin-Version": "copilot-chat/0.26.7", "Copilot-Integration-Id": "vscode-chat", "User-Agent": "GithubCopilot/1.155.0"}'

    holmes ask "what pods are failing?" --model="github_copilot/claude-sonnet-4.5"
    ```

    On first run, LiteLLM will prompt you to authorize the device via a GitHub URL. After authorization, the token is cached locally.

=== "Holmes Helm Chart"

    ```yaml
    # values.yaml
    additionalEnvVars:
      - name: EXTRA_HEADERS
        value: '{"Editor-Version": "vscode/1.85.1", "Editor-Plugin-Version": "copilot-chat/0.26.7", "Copilot-Integration-Id": "vscode-chat", "User-Agent": "GithubCopilot/1.155.0"}'

    modelList:
      copilot-claude:
        model: github_copilot/claude-sonnet-4.5
        extra_headers:
          Editor-Version: "vscode/1.85.1"
          Editor-Plugin-Version: "copilot-chat/0.26.7"
          Copilot-Integration-Id: "vscode-chat"
          User-Agent: "GithubCopilot/1.155.0"

    config:
      model: "copilot-claude"
    ```

=== "Robusta Helm Chart"

    ```yaml
    # values.yaml
    holmes:
      additionalEnvVars:
        - name: EXTRA_HEADERS
          value: '{"Editor-Version": "vscode/1.85.1", "Editor-Plugin-Version": "copilot-chat/0.26.7", "Copilot-Integration-Id": "vscode-chat", "User-Agent": "GithubCopilot/1.155.0"}'

      modelList:
        copilot-claude:
          model: github_copilot/claude-sonnet-4.5
          extra_headers:
            Editor-Version: "vscode/1.85.1"
            Editor-Plugin-Version: "copilot-chat/0.26.7"
            Copilot-Integration-Id: "vscode-chat"
            User-Agent: "GithubCopilot/1.155.0"

      config:
        model: "copilot-claude"
    ```

## Additional Resources

- [LiteLLM GitHub Copilot docs](https://docs.litellm.ai/docs/providers/github_copilot){:target="_blank"}
- [GitHub Copilot plans](https://github.com/features/copilot){:target="_blank"}

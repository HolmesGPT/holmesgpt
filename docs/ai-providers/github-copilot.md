# GitHub Copilot

Configure HolmesGPT to use AI models through your [GitHub Copilot](https://github.com/features/copilot){:target="_blank"} subscription.

!!! note "GitHub Copilot vs GitHub Models"
    This page covers **GitHub Copilot** (subscription-based, uses `github_copilot/` prefix). For **GitHub Models** (token-based, uses `github/` prefix), see the [GitHub Models](github.md) page.

## Prerequisites

- A GitHub Copilot subscription (individual, business, or enterprise)
- LiteLLM handles authentication automatically via OAuth device flow — no API key needed

## Required Headers

GitHub Copilot's API requires IDE-identifying headers (`Editor-Version`, `Editor-Plugin-Version`, `Copilot-Integration-Id`, `User-Agent`) on every request. Without them, requests fail with `"missing Editor-Version header for IDE auth"`.

Configure them via the `extra_headers` field in your model list configuration, or the `EXTRA_HEADERS` environment variable — see the examples below.

## Configuration

In Kubernetes, Holmes can't complete the device authorization from inside a pod: authorize once with the Holmes CLI, then give Holmes the token file LiteLLM stored at `~/.config/litellm/github_copilot/access-token`. To re-authenticate, delete that file and `~/.config/litellm/github_copilot/api-key.json` (LiteLLM reuses an unexpired key without reading the token), run the Holmes CLI again, then delete the secret with `kubectl delete secret holmes-github-copilot -n <namespace>`, create it again and restart the Holmes pod.

=== "Holmes CLI"

    **Create `~/.holmes/model_list.yaml`:**

    ```yaml
    copilot-claude:
      model: github_copilot/claude-sonnet-4.5
      extra_headers:
        Editor-Version: "vscode/1.85.1"
        Editor-Plugin-Version: "copilot-chat/0.26.7"
        Copilot-Integration-Id: "vscode-chat"
        User-Agent: "GithubCopilot/1.155.0"
    ```

    **Run Holmes:**

    ```bash
    holmes ask "what pods are failing?" --model="copilot-claude"
    ```

    On first run, LiteLLM will prompt you to authorize the device via a GitHub URL. After authorization, the token is cached locally.

    **Alternative — environment variable:**

    ```bash
    export EXTRA_HEADERS='{"Editor-Version": "vscode/1.85.1", "Editor-Plugin-Version": "copilot-chat/0.26.7", "Copilot-Integration-Id": "vscode-chat", "User-Agent": "GithubCopilot/1.155.0"}'

    holmes ask "what pods are failing?" --model="github_copilot/claude-sonnet-4.5"
    ```

=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-github-copilot \
      --from-file=access-token=$HOME/.config/litellm/github_copilot/access-token \
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    modelList:
      copilot-claude:
        model: github_copilot/claude-sonnet-4.5
        extra_headers:
          Editor-Version: "vscode/1.85.1"
          Editor-Plugin-Version: "copilot-chat/0.26.7"
          Copilot-Integration-Id: "vscode-chat"
          User-Agent: "GithubCopilot/1.155.0"

    additionalEnvVars:
      - name: MODEL
        value: "copilot-claude"
      - name: GITHUB_COPILOT_ACCESS_TOKEN_FILE
        value: "/etc/github-copilot/access-token"
      # LiteLLM writes its short-lived Copilot key here; /tmp is writable in the pod
      - name: GITHUB_COPILOT_TOKEN_DIR
        value: "/tmp/github-copilot"

    additionalVolumes:
      - name: github-copilot-token
        secret:
          secretName: holmes-github-copilot

    additionalVolumeMounts:
      - name: github-copilot-token
        mountPath: /etc/github-copilot
        readOnly: true
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-github-copilot \
      --from-file=access-token=$HOME/.config/litellm/github_copilot/access-token \
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      modelList:
        copilot-claude:
          model: github_copilot/claude-sonnet-4.5
          extra_headers:
            Editor-Version: "vscode/1.85.1"
            Editor-Plugin-Version: "copilot-chat/0.26.7"
            Copilot-Integration-Id: "vscode-chat"
            User-Agent: "GithubCopilot/1.155.0"

      additionalEnvVars:
        - name: MODEL
          value: "copilot-claude"
        - name: GITHUB_COPILOT_ACCESS_TOKEN_FILE
          value: "/etc/github-copilot/access-token"
        # LiteLLM writes its short-lived Copilot key here; /tmp is writable in the pod
        - name: GITHUB_COPILOT_TOKEN_DIR
          value: "/tmp/github-copilot"

      additionalVolumes:
        - name: github-copilot-token
          secret:
            secretName: holmes-github-copilot

      additionalVolumeMounts:
        - name: github-copilot-token
          mountPath: /etc/github-copilot
          readOnly: true
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Additional Resources

- [LiteLLM GitHub Copilot docs](https://docs.litellm.ai/docs/providers/github_copilot){:target="_blank"}
- [GitHub Copilot plans](https://github.com/features/copilot){:target="_blank"}

# Install sofka Plugin

Integrate HolmesGPT into your [sofka](https://sofka.rs/){:target="\_blank"} Kubernetes terminal for instant analysis.

### Prerequisites

-   **sofka must be installed** - See the [sofka installation guide](https://sofka.rs/#install){:target="\_blank"}
-   **HolmesGPT CLI and API key** - Follow the [CLI Installation Guide](cli-installation.md) to install Holmes and configure your AI provider

### Plugin Options

sofka reads external commands from its configuration file, `~/.config/sofka/config.toml` (or `$XDG_CONFIG_HOME/sofka/config.toml`). Add the entries below to that file, or drop them into a file under `~/.config/sofka/conf.d/` to keep them separate from your other settings. sofka also accepts `config.yaml` and `config.yml`.

The plugins use `output = "terminal"`, so sofka suspends its UI, hands the terminal to Holmes, and returns to the resource view when Holmes exits.

??? note "Basic Plugin (Shift + H) - Quick investigation with predefined question"

    ```toml
    [[plugins]]
    name = "Ask HolmesGPT"
    key = "shift-h"
    palette = "holmes-ask"
    shell = true
    output = "terminal"
    mutating = false
    scopes = []  # all resource kinds
    args = ["$CONTEXT", "$NAME", "$RESOURCE", "$NAMESPACE"]
    command = '''
    CTX="$1"; OBJ="$2"; RES="$3"; TARGET_NS="$4"
    QUESTION="why is $OBJ of $RES in -n $TARGET_NS not working as expected"
    CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "")
    if [ -z "$CTX" ] || [ "$CURRENT_CONTEXT" = "$CTX" ]; then
      # Already using the correct context, run HolmesGPT directly
      holmes ask "$QUESTION"
    else
      # Create a temporary kubeconfig so HolmesGPT uses the context sofka is viewing
      # without permanently switching the user's kubectl context
      TEMP_KUBECONFIG=$(mktemp)
      kubectl config view --raw > "$TEMP_KUBECONFIG"
      KUBECONFIG="$TEMP_KUBECONFIG" kubectl config use-context "$CTX" >/dev/null
      KUBECONFIG="$TEMP_KUBECONFIG" holmes ask "$QUESTION"
      rm -f "$TEMP_KUBECONFIG"
    fi
    '''
    ```

??? note "Advanced Plugin (Shift + Q) - Interactive plugin with custom questions"

    ```toml
    [[plugins]]
    name = "Custom HolmesGPT Ask"
    key = "shift-q"
    palette = "holmes-custom"
    shell = true
    output = "terminal"
    mutating = false
    scopes = []  # all resource kinds
    args = ["$CONTEXT", "$NAME", "$RESOURCE", "$NAMESPACE"]
    command = '''
    CTX="$1"; OBJ="$2"; RES="$3"; TARGET_NS="$4"
    INSTRUCTIONS="# Edit the line below. Lines starting with '#' will be ignored."
    DEFAULT_ASK_COMMAND="why is $OBJ of $RES in -n $TARGET_NS not working as expected"
    QUESTION_FILE=$(mktemp)
    printf '%s\n%s\n' "$INSTRUCTIONS" "$DEFAULT_ASK_COMMAND" > "$QUESTION_FILE"
    # Open the question in the default text editor
    ${EDITOR:-nano} "$QUESTION_FILE"
    QUESTION=$(grep -v '^#' "$QUESTION_FILE")
    rm -f "$QUESTION_FILE"
    if [ -z "$QUESTION" ]; then
      echo "No question provided."
      exit 0
    fi
    echo "Running: holmes ask '$QUESTION'"
    CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "")
    if [ -z "$CTX" ] || [ "$CURRENT_CONTEXT" = "$CTX" ]; then
      # Already using the correct context, run HolmesGPT directly
      holmes ask "$QUESTION"
    else
      # Create a temporary kubeconfig so HolmesGPT uses the context sofka is viewing
      # without permanently switching the user's kubectl context
      TEMP_KUBECONFIG=$(mktemp)
      kubectl config view --raw > "$TEMP_KUBECONFIG"
      KUBECONFIG="$TEMP_KUBECONFIG" kubectl config use-context "$CTX" >/dev/null
      KUBECONFIG="$TEMP_KUBECONFIG" holmes ask "$QUESTION"
      rm -f "$TEMP_KUBECONFIG"
    fi
    '''
    ```

Read more about sofka plugins [here](https://github.com/nklmilojevic/sofka/blob/main/docs/plugins.md){:target="\_blank"} and about the configuration file [here](https://github.com/nklmilojevic/sofka/blob/main/docs/configuration.md){:target="\_blank"}.

??? tip "Coming from K9s?"

    sofka can convert your existing K9s setup - run `sofka import k9s` to carry over aliases, plugins, hotkeys, and skins. The configuration above is the native sofka equivalent of the [HolmesGPT K9s plugin](k9s-installation.md).

### Usage

1. Run sofka and select any Kubernetes resource
2. Press **Shift + H** for quick analysis or **Shift + Q** for custom questions
3. When Holmes exits, sofka returns to the resource view automatically - press **Ctrl + C** to interrupt a running investigation

## Next Steps

-   **[Recommended Setup](../data-sources/recommended-setup.md)** - Connect metrics, logs, and cloud providers to unlock deeper investigations
-   **[All Data Sources](../data-sources/index.md)** - Browse the full list of 38+ built-in integrations

## Need Help?

-   **[Join our Slack](https://cloud-native.slack.com/archives/C0A1SPQM5PZ){:target="\_blank"}** - Get help from the community
-   **[Request features on GitHub](https://github.com/HolmesGPT/holmesgpt/issues){:target="\_blank"}** - Suggest improvements or report bugs
-   **[Troubleshooting guide](../reference/troubleshooting.md)** - Common issues and solutions

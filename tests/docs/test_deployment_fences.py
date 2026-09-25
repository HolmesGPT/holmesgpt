import re
import textwrap
from pathlib import Path

import markdown
import pytest
from mkdocs.commands.build import build
from mkdocs.config import load_config

from docs.custom_fences import DeploymentFenceError

REPO = Path(__file__).resolve().parents[2]


def text(html):
    """The text of rendered HTML, without the markup syntax highlighting adds."""
    return re.sub(r"<[^>]+>", "", html)


@pytest.fixture(scope="module")
def site_config():
    """The Markdown pipeline mkdocs.yml configures for every page."""
    return load_config(str(REPO / "mkdocs.yml"))


@pytest.fixture
def convert(site_config, monkeypatch):
    monkeypatch.chdir(REPO)  # pymdownx.snippets resolves base_path from the cwd

    def convert(text, page="data-sources/builtin-toolsets/victorialogs.md", **mdx):
        configs = {**site_config["mdx_configs"], **mdx}
        configs["docs.custom_fences"] = {"page": page}
        md = markdown.Markdown(
            extensions=site_config["markdown_extensions"], extension_configs=configs
        )
        return md.convert(textwrap.dedent(text))

    return convert


def test_toolset_config_renders_the_three_standard_tabs(convert):
    fence = """\
        ## Configuration

        ```yaml-toolset-config
        toolsets:
          victorialogs:
            enabled: true
            config:
              password: "{{ env.VICTORIALOGS_PASSWORD }}"
        ```
        """
    hand_written = """\
        ## Configuration

        === "Holmes CLI"

            Set the environment variable:

            ```bash
            export VICTORIALOGS_PASSWORD=your-victorialogs-password
            ```

            Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

            ```yaml
            toolsets:
              victorialogs:
                enabled: true
                config:
                  password: "{{ env.VICTORIALOGS_PASSWORD }}"
            ```

            --8<-- "snippets/toolset_refresh_warning.md"

        === "Holmes Helm Chart"

            Create a Kubernetes secret in the namespace Holmes runs in:

            ```bash
            kubectl create secret generic holmes-victorialogs \\
              --from-literal=VICTORIALOGS_PASSWORD=your-victorialogs-password \\
              -n <namespace>
            ```

            When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

            ```yaml
            extraEnvVarsSecrets:
              - holmes-victorialogs

            toolsets:
              victorialogs:
                enabled: true
                config:
                  password: "{{ env.VICTORIALOGS_PASSWORD }}"
            ```

            Apply the configuration:

            ```bash
            helm upgrade holmesgpt robusta/holmes -f values.yaml
            ```

        === "Robusta Helm Chart"

            Create a Kubernetes secret in the namespace Holmes runs in:

            ```bash
            kubectl create secret generic holmes-victorialogs \\
              --from-literal=VICTORIALOGS_PASSWORD=your-victorialogs-password \\
              -n <namespace>
            ```

            When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

            ```yaml
            holmes:
              extraEnvVarsSecrets:
                - holmes-victorialogs

              toolsets:
                victorialogs:
                  enabled: true
                  config:
                    password: "{{ env.VICTORIALOGS_PASSWORD }}"
            ```

            Apply the configuration:

            ```bash
            helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
            ```
        """
    html = convert(fence)
    assert html == convert(hand_written)
    assert 'id="configuration-holmes-helm-chart"' in html
    assert "holmes toolset refresh" in text(html)  # the snippet include is expanded


def test_helm_values_without_a_secret_renders_the_two_helm_tabs(convert):
    fence = """\
        ```yaml-helm-values
        customClusterRoleRules:
          - apiGroups: ["argoproj.io"]
            resources: ["applications"]
            verbs: ["get", "list"]
        ```
        """
    hand_written = """\
        === "Holmes Helm Chart"

            When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

            ```yaml
            customClusterRoleRules:
              - apiGroups: ["argoproj.io"]
                resources: ["applications"]
                verbs: ["get", "list"]
            ```

            Apply the configuration:

            ```bash
            helm upgrade holmesgpt robusta/holmes -f values.yaml
            ```

        === "Robusta Helm Chart"

            When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

            ```yaml
            holmes:
              customClusterRoleRules:
                - apiGroups: ["argoproj.io"]
                  resources: ["applications"]
                  verbs: ["get", "list"]
            ```

            Apply the configuration:

            ```bash
            helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
            ```
        """
    assert convert(fence) == convert(hand_written)


def test_env_vars_set_by_the_chart_are_not_secret_keys_and_chart_keys_stay_out_of_the_cli(
    convert,
):
    fence = """\
        ```yaml-toolset-config
        additionalEnvVars:
          - name: MODEL
            value: "{{ env.MODEL }}"

        # Datadog
        toolsets:
          datadog/logs:
            config:
              api_key: "{{ env.DATADOG_API_KEY }}"
              app_key: "{{ env.DATADOG_APP_KEY }}"
        ```
        """
    hand_written = """\
        === "Holmes CLI"

            Set the environment variables:

            ```bash
            export DATADOG_API_KEY=your-datadog-api-key
            export DATADOG_APP_KEY=your-datadog-app-key
            ```

            Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

            ```yaml
            # Datadog
            toolsets:
              datadog/logs:
                config:
                  api_key: "{{ env.DATADOG_API_KEY }}"
                  app_key: "{{ env.DATADOG_APP_KEY }}"
            ```

            --8<-- "snippets/toolset_refresh_warning.md"

        === "Holmes Helm Chart"

            Create a Kubernetes secret in the namespace Holmes runs in:

            ```bash
            kubectl create secret generic holmes-datadog \\
              --from-literal=DATADOG_API_KEY=your-datadog-api-key \\
              --from-literal=DATADOG_APP_KEY=your-datadog-app-key \\
              -n <namespace>
            ```

            When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

            ```yaml
            extraEnvVarsSecrets:
              - holmes-datadog

            additionalEnvVars:
              - name: MODEL
                value: "{{ env.MODEL }}"

            # Datadog
            toolsets:
              datadog/logs:
                config:
                  api_key: "{{ env.DATADOG_API_KEY }}"
                  app_key: "{{ env.DATADOG_APP_KEY }}"
            ```

            Apply the configuration:

            ```bash
            helm upgrade holmesgpt robusta/holmes -f values.yaml
            ```

        === "Robusta Helm Chart"

            Create a Kubernetes secret in the namespace Holmes runs in:

            ```bash
            kubectl create secret generic holmes-datadog \\
              --from-literal=DATADOG_API_KEY=your-datadog-api-key \\
              --from-literal=DATADOG_APP_KEY=your-datadog-app-key \\
              -n <namespace>
            ```

            When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

            ```yaml
            holmes:
              extraEnvVarsSecrets:
                - holmes-datadog

              additionalEnvVars:
                - name: MODEL
                  value: "{{ env.MODEL }}"

              # Datadog
              toolsets:
                datadog/logs:
                  config:
                    api_key: "{{ env.DATADOG_API_KEY }}"
                    app_key: "{{ env.DATADOG_APP_KEY }}"
            ```

            Apply the configuration:

            ```bash
            helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
            ```
        """
    page = "data-sources/builtin-toolsets/datadog.md"
    assert convert(fence, page=page) == convert(hand_written, page=page)


def test_a_group_reading_an_earlier_groups_secret_has_no_secret_step(convert):
    page = """\
        ## A

        ```yaml-helm-values
        mcp_servers:
          a:
            config:
              token: "{{ env.TOKEN }}"
        ```

        ## B

        ```yaml-helm-values
        mcp_servers:
          b:
            config:
              token: "{{ env.TOKEN }}"
        ```
        """
    first, second = map(text, convert(page).split('<h2 id="b">'))
    assert first.count("kubectl create secret generic holmes-victorialogs") == 2
    assert first.count("extraEnvVarsSecrets") == 2
    assert "kubectl" not in second and "extraEnvVarsSecrets" not in second


TOKEN_FENCE = """\
```yaml-helm-values
mcp_servers:
  a:
    config:
      token: "{{ env.TOKEN }}"
```
"""

TOKEN_TABS = """\
=== "Holmes Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-victorialogs \\
      --from-literal=TOKEN=your-token \\
      -n <namespace>
    ```

    When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

    ```yaml
    extraEnvVarsSecrets:
      - holmes-victorialogs

    mcp_servers:
      a:
        config:
          token: "{{ env.TOKEN }}"
    ```

    Apply the configuration:

    ```bash
    helm upgrade holmesgpt robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    Create a Kubernetes secret in the namespace Holmes runs in:

    ```bash
    kubectl create secret generic holmes-victorialogs \\
      --from-literal=TOKEN=your-token \\
      -n <namespace>
    ```

    When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

    ```yaml
    holmes:
      extraEnvVarsSecrets:
        - holmes-victorialogs

      mcp_servers:
        a:
          config:
            token: "{{ env.TOKEN }}"
    ```

    Apply the configuration:

    ```bash
    helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```
"""


def test_a_fence_in_an_included_snippet_renders_as_on_the_page(convert, tmp_path):
    (tmp_path / "group.md").write_text(TOKEN_FENCE)
    snippets = {"pymdownx.snippets": {"base_path": [str(tmp_path), "docs"]}}
    included = convert('--8<-- "group.md"\n', **snippets)
    assert included == convert(TOKEN_TABS, **snippets)
    assert "holmes-victorialogs" in text(included)


def test_an_indented_fence_renders_as_indented_tabs(convert):
    def in_list(text):
        return "- Step\n\n" + textwrap.indent(text, "    ")

    html = convert(in_list(TOKEN_FENCE))
    assert html == convert(in_list(TOKEN_TABS))
    assert html.count('<div class="tabbed-set') == 1


@pytest.mark.parametrize(
    "block",
    [
        pytest.param("```yaml-helm-values\nkey: [a\n```\n", id="invalid-yaml"),
        pytest.param("```yaml-helm-values\n- a\n```\n", id="not-a-mapping"),
        pytest.param(
            "```yaml-toolset-config\ncustomClusterRoleRules: []\n```\n",
            id="toolset-config-without-cli-keys",
        ),
        pytest.param("```yaml-helm-values title=x\nkey: 1\n```\n", id="fence-options"),
    ],
)
def test_a_fence_that_cannot_be_rendered_fails_the_build(convert, block):
    with pytest.raises(DeploymentFenceError):
        convert(block)


def test_a_secret_needs_the_page(convert):
    with pytest.raises(DeploymentFenceError):
        convert('```yaml-helm-values\nx: "{{ env.X }}"\n```\n', page="")


def test_mkdocs_names_the_secret_after_the_page(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "splunk.md").write_text(
        '# Splunk\n\n```yaml-helm-values\nx: "{{ env.SPLUNK_TOKEN }}"\n```\n'
    )
    (tmp_path / "mkdocs.yml").write_text(
        textwrap.dedent(f"""\
            site_name: test
            markdown_extensions:
              - docs.custom_fences
              - pymdownx.superfences
              - pymdownx.tabbed:
                  alternate_style: true
            hooks:
              - {REPO / "docs" / "custom_fences.py"}
            """)
    )
    build(load_config(str(tmp_path / "mkdocs.yml")))
    html = (tmp_path / "site" / "splunk" / "index.html").read_text()
    assert "kubectl create secret generic holmes-splunk" in text(html)

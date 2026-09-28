import re
import textwrap
from pathlib import Path

import markdown
import pytest
from mkdocs.commands.build import build
from mkdocs.config import load_config

from docs.custom_fences import TabFenceError

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


def test_a_group_reading_an_earlier_groups_secret_reuses_it_with_a_note(convert):
    page = """\
        ## Set up A

        ```yaml-helm-values
        mcp_servers:
          a:
            config:
              token: "{{ env.TOKEN }}"
        ```

        ## B

        ```yaml-toolset-config
        mcp_servers:
          b:
            config:
              token: "{{ env.TOKEN }}"
        ```
        """
    hand_written_b = """\
        ## B

        In Kubernetes, this reuses the `holmes-victorialogs` Kubernetes secret created in the [Set up A](#set-up-a) section above.

        === "Holmes CLI"

            Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:

            ```yaml
            mcp_servers:
              b:
                config:
                  token: "{{ env.TOKEN }}"
            ```

            --8<-- "snippets/toolset_refresh_warning.md"

        === "Holmes Helm Chart"

            When using the **standalone Holmes Helm Chart**, update your `values.yaml`:

            ```yaml
            extraEnvVarsSecrets:
              - holmes-victorialogs

            mcp_servers:
              b:
                config:
                  token: "{{ env.TOKEN }}"
            ```

            Apply the configuration:

            ```bash
            helm upgrade holmesgpt robusta/holmes -f values.yaml
            ```

        === "Robusta Helm Chart"

            When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:

            ```yaml
            holmes:
              extraEnvVarsSecrets:
                - holmes-victorialogs

              mcp_servers:
                b:
                  config:
                    token: "{{ env.TOKEN }}"
            ```

            Apply the configuration:

            ```bash
            helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
            ```
        """
    html = convert(page)
    group_a = page.split("        ## B")[0]
    assert html == convert(group_a + hand_written_b)
    first = html.split('<h2 id="b">')[0]
    assert text(first).count("kubectl create secret generic holmes-victorialogs") == 2
    assert '<h2 id="set-up-a">' in first  # the note links to the heading's own id


def test_the_reuse_note_links_to_the_id_the_heading_gets(convert):
    page = """\
        ## Setup

        ## Setup

        ```yaml-helm-values
        x: "{{ env.X }}"
        ```

        ## Custom {#my-id}

        ```yaml-helm-values {secret-qualifier=y}
        y: "{{ env.Y }}"
        ```

        ## Later

        ```yaml-helm-values
        x: "{{ env.X }}"
        ```

        ```yaml-helm-values
        y: "{{ env.Y }}"
        ```
        """
    html = convert(page)
    assert '<h2 id="setup_1">' in html and '<h2 id="my-id">' in html
    later = text(html.split('<h2 id="later">')[1])
    assert (
        "Reuses the holmes-victorialogs Kubernetes secret created in the Setup section above"
        in later
    )
    assert (
        "extraEnvVarsSecrets:\n  - holmes-victorialogs-y" in later
        and "kubectl" not in later
    )
    assert 'href="#setup_1"' in html and 'href="#my-id"' in html


QUALIFIED_PAGE = """\
    ## Basic auth

    ```yaml-helm-values
    password: "{{ env.PASSWORD }}"
    ```

    ## Bearer token

    ```yaml-helm-values {secret-qualifier=token}
    token: "{{ env.TOKEN }}"
    ```
    """


def test_a_secret_qualifier_names_a_second_secret_on_the_page(convert):
    bearer = text(convert(QUALIFIED_PAGE).split('<h2 id="bearer-token">')[1])
    assert "kubectl create secret generic holmes-victorialogs-token" in bearer
    assert "extraEnvVarsSecrets:\n  - holmes-victorialogs-token" in bearer
    assert "holmes-victorialogs\n" not in bearer and "Reuses" not in bearer


def test_a_second_secret_without_a_qualifier_fails_the_build(convert):
    with pytest.raises(TabFenceError, match="secret-qualifier"):
        convert(QUALIFIED_PAGE.replace(" {secret-qualifier=token}", ""))


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
        pytest.param(
            "```yaml-helm-values title=x\nkey: 1\n```\n", id="options-without-braces"
        ),
        pytest.param(
            "```yaml-helm-values {title=x}\nkey: 1\n```\n", id="unknown-option"
        ),
        pytest.param(
            '```yaml-helm-values {secret-qualifier=a b}\nx: "{{ env.X }}"\n```\n',
            id="option-leftover",
        ),
        pytest.param(
            "```yaml-helm-values {secret-qualifier=a}\nkey: 1\n```\n",
            id="qualifier-without-a-secret",
        ),
        pytest.param(
            '```yaml-helm-values\nx: "{{ env.X }}"\n```\n\n'
            '## B\n\n```yaml-helm-values\nx: "{{ env.X }}"\n```\n',
            id="reuse-of-a-secret-created-under-no-heading",
        ),
        pytest.param(
            "```robusta-region {secret-qualifier=a}\nhttps://api.robusta.dev\n```\n",
            id="region-option-it-does-not-take",
        ),
    ],
)
def test_a_fence_that_cannot_be_rendered_fails_the_build(convert, block):
    with pytest.raises(TabFenceError):
        convert(block)


def test_robusta_region_renders_the_region_tabs_as_written_by_hand(convert):
    fence = """\
        ## Selecting a Region

        ```robusta-region {lang=yaml}
        url: "https://api.robusta.dev/api"
        ```

        1. Open the platform:

            ```robusta-region
            [platform.robusta.dev](https://platform.robusta.dev/)
            ```
        """
    hand_written = """\
        ## Selecting a Region

        === "US"

            ```yaml
            url: "https://api.robusta.dev/api"
            ```

        === "EU"

            ```yaml
            url: "https://api.eu.robusta.dev/api"
            ```

        === "AP"

            ```yaml
            url: "https://api.ap.robusta.dev/api"
            ```

        1. Open the platform:

            === "US"

                [platform.robusta.dev](https://platform.robusta.dev/)

            === "EU"

                [platform.eu.robusta.dev](https://platform.eu.robusta.dev/)

            === "AP"

                [platform.ap.robusta.dev](https://platform.ap.robusta.dev/)
        """
    html = convert(fence)
    assert html == convert(hand_written)
    assert html == convert(fence)  # the same page always gets the same tab ids
    assert (
        'id="selecting-a-region-eu"' in html and 'id="selecting-a-region-eu_1"' in html
    )
    assert 'href="https://platform.ap.robusta.dev/"' in html


def test_without_toc_headings_have_no_ids_for_a_reuse_note(monkeypatch):
    monkeypatch.chdir(REPO)
    md = markdown.Markdown(
        extensions=["docs.custom_fences", "pymdownx.superfences", "pymdownx.tabbed"],
        extension_configs={"docs.custom_fences": {"page": "a.md"}},
    )
    fence = '```yaml-helm-values\nx: "{{ env.X }}"\n```\n'
    with pytest.raises(TabFenceError, match="no heading"):
        md.convert(f"## A\n\n{fence}\n## B\n\n{fence}")


def test_a_secret_needs_the_page(convert):
    with pytest.raises(TabFenceError):
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

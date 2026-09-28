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
            helm upgrade holmes robusta/holmes -f values.yaml
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
            helm upgrade holmes robusta/holmes -f values.yaml
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
            helm upgrade holmes robusta/holmes -f values.yaml
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


REUSE_FENCE = """\
```yaml-toolset-config {reuse}
mcp_servers:
  b:
    config:
      token: "{{ env.TOKEN }}"
```
"""

REUSE_TABS = """\
=== "Holmes CLI"

    Set the environment variable:

    ```bash
    export TOKEN=your-token
    ```

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
    helm upgrade holmes robusta/holmes -f values.yaml
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


def test_a_reuse_fence_mounts_and_exports_the_secret_without_creating_it(convert):
    assert convert(REUSE_FENCE) == convert(REUSE_TABS)


def test_a_reuse_fence_with_a_qualifier_mounts_the_qualified_secret(convert):
    qualified = REUSE_FENCE.replace("{reuse}", "{reuse secret-qualifier=token}")
    shown = text(convert(qualified))
    assert "extraEnvVarsSecrets:\n  - holmes-victorialogs-token" in shown
    assert "kubectl" not in shown and "export TOKEN=your-token" in shown


X_FENCE = '```yaml-helm-values\nx: "{{ env.X }}"\n```\n'


def test_secret_keys_come_from_keys_and_values_in_body_order_and_not_from_comments(
    convert,
):
    fence = """\
        ```yaml-toolset-config
        # Set {{ env.IN_A_COMMENT }} first.
        toolsets:
          x:
            config:  # or {{ env.IN_A_TRAILING_COMMENT }}
              zulu: "{{ env.ZULU }}"
              note: "a # is not a comment in a string: {{ env.IN_A_STRING }}"
              alpha: "{{ env.ALPHA }}"
              again: "{{ env.ZULU }}"
              "{{ env.IN_A_KEY }}": key
        ```
        """
    shown = text(convert(fence))
    keys = ["ZULU", "IN_A_STRING", "ALPHA", "IN_A_KEY"]
    assert re.findall(r"--from-literal=(\w+)=", shown) == 2 * keys
    assert re.findall(r"export (\w+)=", shown) == keys


def test_a_tilde_fence_renders_as_a_backtick_fence(convert):
    tilde = TOKEN_FENCE.replace("```", "~~~")
    assert convert(tilde) == convert(TOKEN_TABS)


def test_a_fence_in_a_blockquote_renders_as_tabs_written_in_it(convert):
    def quoted(body):
        return (
            "> Intro\n>\n"
            + textwrap.indent(body, "> ", lambda line: True).replace("> \n", ">\n")
            + ">\n> After\n"
        )

    html = convert(quoted(TOKEN_FENCE))
    assert html == convert(quoted(TOKEN_TABS))
    assert html.count("<blockquote>") == 1 and html.count('<div class="tabbed-set') == 1
    # A line of the quote's marker alone is an empty line of the body.
    spaced = convert(quoted(TOKEN_FENCE.replace("mcp_servers:", "mcp_servers:\n")))
    assert spaced.count('<div class="tabbed-set') == 1


def test_the_fences_expand_ahead_of_superfences_preserve_tabs(convert, site_config):
    superfences = {
        **site_config["mdx_configs"]["pymdownx.superfences"],
        "preserve_tabs": True,
    }
    html = convert(TOKEN_FENCE, **{"pymdownx.superfences": superfences})
    assert html == convert(TOKEN_TABS, **{"pymdownx.superfences": superfences})
    assert html.count('<div class="tabbed-set') == 1


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
    helm upgrade holmes robusta/holmes -f values.yaml
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


def test_a_fence_renders_the_same_whatever_the_page_holds_around_it(convert):
    def page(group):
        return f"## A\n\n{group}\n{group}\n## B\n\n{group}"

    assert convert(page(TOKEN_FENCE)) == convert(page(TOKEN_TABS))


def test_a_secret_qualifier_names_the_secret_after_the_page_and_the_qualifier(
    convert,
):
    fence = X_FENCE.replace("values", "values {secret-qualifier=token}")
    shown = text(convert(fence))
    assert "kubectl create secret generic holmes-victorialogs-token" in shown
    assert "extraEnvVarsSecrets:\n  - holmes-victorialogs-token" in shown


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
    "block, message",
    [
        pytest.param(
            "```yaml-helm-values\nkey: [a\n```\n",
            "is not valid YAML",
            id="invalid-yaml",
        ),
        pytest.param(
            "```yaml-helm-values\n- a\n```\n",
            "must be a YAML mapping",
            id="not-a-mapping",
        ),
        pytest.param(
            "```yaml-toolset-config\ncustomClusterRoleRules: []\n```\n",
            "sets none of toolsets, mcp_servers",
            id="toolset-config-without-cli-keys",
        ),
        *(
            pytest.param(
                f"```{fence}\n{body}```\n", "is empty", id=f"{fence}-{name}-body"
            )
            for fence in ("yaml-toolset-config", "yaml-helm-values", "multi-instance")
            for name, body in (("empty", ""), ("blank", "  \n\n"))
        ),
        pytest.param(
            X_FENCE.replace("values", "values{secret-qualifier=q}"),
            "takes its options after a space: yaml-helm-values{secret-qualifier=q}",
            id="options-with-no-space-after-the-name",
        ),
        pytest.param(
            "```multi-instance\ntoolset: a\nconfig:\n  x: 1\n```\n",
            "takes config as a string, a YAML block scalar (config: |), not dict",
            id="multi-instance-config-not-a-string",
        ),
        pytest.param(
            "```yaml-helm-values title=x\nkey: 1\n```\n",
            "takes options as",
            id="options-without-braces",
        ),
        pytest.param(
            "```yaml-helm-values {title=x}\nkey: 1\n```\n",
            "takes only the options secret-qualifier",
            id="unknown-option",
        ),
        pytest.param(
            '```yaml-helm-values {secret-qualifier=a b}\nx: "{{ env.X }}"\n```\n',
            "takes only the options secret-qualifier",
            id="option-leftover",
        ),
        pytest.param(
            "```yaml-helm-values {secret-qualifier=a}\nkey: 1\n```\n",
            "takes no secret-qualifier",
            id="qualifier-without-a-secret",
        ),
        pytest.param(
            "```yaml-helm-values {reuse}\nkey: 1\n```\n",
            "reads no secret, so it takes no reuse",
            id="reuse-without-a-secret",
        ),
        pytest.param(
            X_FENCE.replace("values", "values {reuse=yes}"),
            "takes reuse as a flag, {reuse}, with no value",
            id="flag-with-a-value",
        ),
        pytest.param(
            X_FENCE.replace("values", "values {secret-qualifier}"),
            "takes secret-qualifier with a value, {secret-qualifier=<value>}",
            id="option-without-a-value",
        ),
        pytest.param(
            X_FENCE.replace("values", "values {secret-qualifier=a secret-qualifier=b}"),
            "sets the option secret-qualifier more than once",
            id="repeated-option",
        ),
        pytest.param(
            X_FENCE.replace("values", f"values {{secret-qualifier={'a' * 240}}}"),
            "not a valid Kubernetes secret name",
            id="secret-name-over-253-characters",
        ),
        pytest.param(
            "```multi-instance\ntoolset: [a\n```\n",
            "is not valid YAML",
            id="multi-instance-invalid-yaml",
        ),
        pytest.param(
            "```multi-instance\n- a\n```\n",
            "must be a YAML mapping",
            id="multi-instance-not-a-mapping",
        ),
        pytest.param(
            "```multi-instance\ntoolset: a\n```\n",
            "requires the keys toolset and config",
            id="multi-instance-without-config",
        ),
        pytest.param(
            "```multi-instance\nconfig: |\n  a: 1\n```\n",
            "requires the keys toolset and config",
            id="multi-instance-without-toolset",
        ),
        pytest.param(
            "```multi-instance {lang=yaml}\ntoolset: a\nconfig: |\n  a: 1\n```\n",
            "takes no options",
            id="multi-instance-option",
        ),
        *(
            pytest.param(
                X_FENCE.replace("values", f'values {{secret-qualifier="{qualifier}"}}'),
                "takes a secret-qualifier of lowercase letters",
                id=f"qualifier-{qualifier}",
            )
            for qualifier in ("My Token", "Token", "a_b", "a.b", "-a", "a-", "")
        ),
        pytest.param(
            "```yaml-helm-values\nkey: 1\n",
            "has no closing ``` line",
            id="unclosed",
        ),
        pytest.param(
            "- Step 1\n\n    ```yaml-helm-values\n    key: 1\n\n"
            "- Step 2\n\n    ```\n    key: 2\n    ```\n",
            "has no closing ``` line",
            id="a-less-indented-line-ends-the-fence",
        ),
        pytest.param(
            "> ```yaml-helm-values\n> key: 1\n```\n",
            "has no closing ``` line",
            id="closing-line-outside-the-blockquote",
        ),
    ],
)
def test_a_fence_that_cannot_be_rendered_fails_the_build(convert, block, message):
    with pytest.raises(TabFenceError, match=re.escape(message)):
        convert(block)


def test_a_page_whose_name_makes_no_secret_name_fails_the_build(convert):
    with pytest.raises(TabFenceError, match="not a valid Kubernetes secret name"):
        convert(X_FENCE, page="data-sources/My_Page.md")


def test_multi_instance_renders_the_section_as_written_by_hand(convert):
    fence = """\
        ## Multiple Instances

        ```multi-instance
        toolset: prometheus/metrics
        name: Prometheus
        config: |
          prometheus_url: http://prometheus:9090

          timeout: 30
        ```
        """
    hand_written = """\
        ## Multiple Instances

        The Prometheus toolset can connect to more than one Prometheus instance. List each one under `instances:` with a unique `name`. Any config field set outside `instances:` becomes a default that every instance inherits, so shared settings only need to be written once.

        ```yaml
        toolsets:
          prometheus/metrics:
            enabled: true
            config:
              instances:
                - name: prod
                  prometheus_url: http://prometheus:9090

                  timeout: 30
                - name: staging
                  prometheus_url: http://prometheus:9090

                  timeout: 30
        ```

        When more than one instance is configured, HolmesGPT automatically adds an `instance` parameter to every Prometheus tool (so it can pick which instance to query) and a `prometheus_metrics_list_instances` tool to list the configured instances. With a single instance — including the flat config without `instances:` — the tools are unchanged and fully backwards compatible.

        See [Multiple Instances](../multi-instance-toolsets.md) for the full behaviour, including global defaults and health reporting.
        """
    page = "data-sources/builtin-toolsets/prometheus.md"
    assert convert(fence, page=page) == convert(hand_written, page=page)


def test_multi_instance_links_relative_to_the_page_and_shows_its_names_as_written(
    convert,
):
    fence = """\
        ```multi-instance
        toolset: a
        name: "*A* <b>"
        list_tool: find_a
        config: |
          x: 1
        ```
        """
    html = convert(fence, page="data-sources/a.md")
    assert 'href="multi-instance-toolsets.md"' in html
    assert "The *A* &lt;b&gt; toolset" in html and "<code>find_a</code>" in html
    assert 'href="data-sources/multi-instance-toolsets.md"' in convert(
        fence, page="a.md"
    )
    with pytest.raises(TabFenceError, match="no page was given"):
        convert(fence, page="")


def test_a_secret_needs_the_page(convert):
    with pytest.raises(TabFenceError, match="no page was given"):
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


def test_mkdocs_resolves_the_multi_instance_link_to_the_page(tmp_path):
    docs = tmp_path / "docs"
    (docs / "data-sources" / "builtin-toolsets").mkdir(parents=True)
    (docs / "data-sources" / "multi-instance-toolsets.md").write_text(
        "# Multiple Instances\n"
    )
    (docs / "data-sources" / "builtin-toolsets" / "a.md").write_text(
        "# A\n\n```multi-instance\ntoolset: a\nconfig: |\n  x: 1\n```\n"
    )
    (tmp_path / "mkdocs.yml").write_text(
        textwrap.dedent(f"""\
            site_name: test
            strict: true
            markdown_extensions:
              - docs.custom_fences
              - pymdownx.superfences
            hooks:
              - {REPO / "docs" / "custom_fences.py"}
            """)
    )
    build(load_config(str(tmp_path / "mkdocs.yml")))
    html = (
        tmp_path / "site" / "data-sources" / "builtin-toolsets" / "a" / "index.html"
    ).read_text()
    assert 'href="../../multi-instance-toolsets/"' in html

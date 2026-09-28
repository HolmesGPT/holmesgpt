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

        In Kubernetes, this reuses the `holmes-victorialogs` secret created in the [Set up A](#set-up-a) section above.

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

        ### Later Y

        ```yaml-helm-values
        y: "{{ env.Y }}"
        ```
        """
    html = convert(page)
    assert '<h2 id="setup_1">' in html and '<h2 id="my-id">' in html
    later = text(html.split('<h2 id="later">')[1])
    assert (
        "Reuses the holmes-victorialogs secret created in the Setup section above"
        in later
    )
    assert (
        "extraEnvVarsSecrets:\n  - holmes-victorialogs-y" in later
        and "kubectl" not in later
    )
    notes = re.findall(r"<p>Reuses .*?</p>", html)
    assert ['href="#setup_1"' in notes[0], 'href="#my-id"' in notes[1]] == [True, True]


def test_a_reusing_group_exports_the_keys_it_reads_in_the_secrets_order(convert):
    page = """\
        ## A

        ```yaml-helm-values
        x: "{{ env.ZULU }}"
        y: "{{ env.ALPHA }}"
        z: "{{ env.MIKE }}"
        ```

        ## B

        ```yaml-toolset-config
        toolsets:
          b:
            config:
              m: "{{ env.MIKE }}"
              z: "{{ env.ZULU }}"
        ```
        """
    cli = text(convert(page).split('<h2 id="b">')[1]).split("Add the following")[0]
    assert "Set the environment variables:" in cli
    assert re.findall(r"export (\w+)=", cli) == ["ZULU", "MIKE"]


X_FENCE = '```yaml-helm-values\nx: "{{ env.X }}"\n```\n'

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


def test_a_heading_like_line_in_a_code_block_is_not_the_notes_section(convert):
    code = "```bash\n# not a heading\n```\n"
    page = f"## Setup\n\n{code}\n{X_FENCE}\n## Later\n\n{X_FENCE}"
    note = re.findall(r"<p>Reuses .*?</p>", convert(page))
    assert len(note) == 1 and 'href="#setup">Setup</a>' in note[0]


@pytest.mark.parametrize(
    "heading, anchor",
    [
        pytest.param("Setup\n=====", "setup", id="level-1"),
        pytest.param("Setup\n-----", "setup", id="level-2"),
        pytest.param("Set up {#custom}\n---", "custom", id="explicit-id"),
        pytest.param("## Setup\n\nFirst line\nsecond line\n---", "setup", id="hr"),
    ],
)
def test_the_reuse_note_names_a_setext_heading(convert, heading, anchor):
    page = f"{heading}\n\n{X_FENCE}\n## Later\n\n{X_FENCE}"
    html = convert(page)
    note = re.findall(r"<p>Reuses .*?</p>", html)
    assert len(note) == 1 and f'href="#{anchor}"' in note[0]
    assert f'id="{anchor}"' in html


@pytest.mark.parametrize(
    "above, anchor, link_text",
    [
        pytest.param(
            "## Configure [Datadog](https://example.com)",
            "configure-datadog",
            "Configure Datadog",
            id="link",
        ),
        pytest.param("## :material-cog: Setup", "setup", "Setup", id="emoji"),
        pytest.param(
            "## Setup <small>beta</small>", "setup-beta", "Setup beta", id="inline-html"
        ),
        pytest.param("## Setup {.beta}", "setup", "Setup", id="attr-list-class"),
        pytest.param(
            "## Use `holmes` config",
            "use-holmes-config",
            "Use holmes config",
            id="code",
        ),
        pytest.param(
            "## Logs & Metrics", "logs-metrics", "Logs &amp; Metrics", id="ampersand"
        ),
        pytest.param(
            "## Logs &amp; Metrics",
            "logs-metrics",
            "Logs &amp; Metrics",
            id="entity-reference",
        ),
        pytest.param("## Setup ##", "setup", "Setup", id="closing-hashes"),
        pytest.param(
            "## Setup\n\n<!--\n## Old section\n-->",
            "setup",
            "Setup",
            id="heading-in-a-comment",
        ),
        pytest.param(
            "## Setup\n\n    indented code\n---",
            "setup",
            "Setup",
            id="indented-line-above-a-rule",
        ),
        pytest.param(
            "!!! note\n    ## Setup\n\n    t\n\n## Setup",
            "setup_1",
            "Setup",
            id="same-text-in-an-admonition",
        ),
        pytest.param(
            '=== "A"\n\n    ## Setup\n\n    t\n\n## Setup',
            "setup_1",
            "Setup",
            id="same-text-in-a-tab",
        ),
    ],
)
def test_the_reuse_note_links_to_the_heading_as_toc_renders_it(
    convert, above, anchor, link_text
):
    html = convert(f"{above}\n\n{X_FENCE}\n## Later\n\n{X_FENCE}")
    note = re.findall(r"<p>Reuses .*?</p>", html)
    assert len(note) == 1 and f'<a href="#{anchor}">{link_text}</a>' in note[0]
    assert re.search(rf'<h2[^>]* id="{anchor}"', html)
    assert "TABFENCEGROUP" not in html


def test_a_second_deployment_group_under_a_heading_fails_the_build(convert):
    page = f"## Setup\n\n{X_FENCE}\n```yaml-toolset-config\ntoolsets:\n  a: {{}}\n```\n"
    with pytest.raises(
        TabFenceError,
        match=re.escape(
            "the yaml-toolset-config fence starting 'toolsets:' on "
            "data-sources/builtin-toolsets/victorialogs.md sits under the same heading "
            """as the yaml-helm-values fence starting 'x: "{{ env.X }}"' (Setup)"""
        ),
    ):
        convert(page)
    with pytest.raises(TabFenceError, match=re.escape("(no heading)")):
        convert(f"{X_FENCE}\n{X_FENCE}")


def test_a_qualified_group_creates_its_own_secret_even_when_an_earlier_one_holds_its_keys(
    convert,
):
    page = f"## A\n\n{X_FENCE}\n## B\n\n" + X_FENCE.replace(
        "values", "values {secret-qualifier=q}"
    )
    b = text(convert(page).split('<h2 id="b">')[1])
    assert "kubectl create secret generic holmes-victorialogs-q" in b
    assert "Reuses" not in b


def test_a_group_reuses_the_first_earlier_secret_that_holds_its_keys(convert):
    page = """\
        ## A

        ```yaml-helm-values
        x: "{{ env.X }}"
        y: "{{ env.Y }}"
        ```

        ## B

        ```yaml-helm-values {secret-qualifier=b}
        x: "{{ env.X }}"
        ```

        ## C

        ```yaml-helm-values
        x: "{{ env.X }}"
        ```
        """
    note = re.findall(r"<p>Reuses .*?</p>", convert(page))
    assert len(note) == 1
    assert "<code>holmes-victorialogs</code>" in note[0] and 'href="#a"' in note[0]


def test_a_tilde_fence_renders_as_a_backtick_fence(convert):
    tilde = TOKEN_FENCE.replace("```", "~~~")
    assert convert(tilde) == convert(TOKEN_TABS)


def test_options_may_follow_the_fence_name_without_a_space(convert):
    fence = X_FENCE.replace("values", "values{secret-qualifier=q}")
    assert "holmes-victorialogs-q" in text(convert(fence))


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


def test_a_secret_qualifier_names_a_second_secret_on_the_page(convert):
    bearer = text(convert(QUALIFIED_PAGE).split('<h2 id="bearer-token">')[1])
    assert "kubectl create secret generic holmes-victorialogs-token" in bearer
    assert "extraEnvVarsSecrets:\n  - holmes-victorialogs-token" in bearer
    assert "holmes-victorialogs\n" not in bearer and "Reuses" not in bearer


def test_a_second_secret_without_a_qualifier_fails_the_build(convert):
    with pytest.raises(TabFenceError, match="give it a secret-qualifier"):
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
            "```robusta-region\nhttps://docs.example.com/x\n```\n",
            "holds no Robusta host",
            id="region-without-a-robusta-host",
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
            f"{X_FENCE}\n## B\n\n{X_FENCE}",
            "sits under no heading",
            id="reuse-of-a-secret-created-under-no-heading",
        ),
        pytest.param(
            f'## A\n\n{X_FENCE}\n## B\n\n```yaml-helm-values\nx: "{{{{ env.X }}}}"\n'
            'y: "{{ env.Y }}"\n```\n',
            "give it a secret-qualifier",
            id="more-keys-than-the-earlier-secret-holds",
        ),
        pytest.param(
            "```robusta-region {secret-qualifier=a}\nhttps://api.robusta.dev\n```\n",
            "takes only the options lang",
            id="region-option-it-does-not-take",
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
    with pytest.raises(TabFenceError, match="its heading has no id"):
        md.convert(f"## A\n\n{fence}\n## B\n\n{fence}")


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

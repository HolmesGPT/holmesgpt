"""
Custom fences for the MkDocs documentation.

- yaml-toolset-config: a setting that applies to the CLI and to Kubernetes. Holmes CLI, Holmes Helm
  Chart and Robusta Helm Chart tabs.
- yaml-helm-values: a chart-only setting, with no CLI counterpart. Holmes Helm Chart and Robusta Helm
  Chart tabs.
- robusta-region: Creates 3 tabs (US, EU, AP) for any text containing api.robusta.dev, platform.robusta.dev, or
  sp.robusta.dev. Plain URLs render as code blocks; markdown links `[text](url)` render as clickable links.
- multi-instance: the standard "Multiple Instances" section for a toolset. The body is YAML with
  `toolset` (the toolset's config key), `name` (its display name) and `config` (a single-instance
  config example, a block scalar). It links to the Multiple Instances page with a path relative to
  the page.

robusta-region is a superfences custom fence, registered in mkdocs.yml. The other three expand into
markdown before any other fence or tab is rendered, so each renders exactly as the same markdown
written by hand, tab ids included. Each renders from its own body and options and the page's path,
and reads nothing else on the page. The two deployment fences render the deployment tab standard.

Supported forms. Write the fence at the start of a line, opened by three backticks and the fence
name, and closed by the first line of three backticks:

    ```yaml-toolset-config
    ```yaml-toolset-config {reuse}
    ```yaml-toolset-config {secret-qualifier=<name>}
    ```multi-instance

`yaml-helm-values` takes the same forms as `yaml-toolset-config`. A `multi-instance` body has
`toolset`, `name` and `config`. Any other form of these fences fails the build with a message naming
the page and the line, and so does a body that is not valid YAML, and a page whose rendered HTML
shows a fence's markdown instead of its tabs (`on_post_page`).

The body of a deployment fence. The Holmes chart values, a block mapping whose first key starts at
the first column, then optionally a line `---` and the fields below, a second block mapping:

    modelList:
      gpt-4.1:
        api_key: "{{ env.OPENAI_API_KEY }}"
        model: openai/gpt-4.1
    ---
    secret:
      - --from-literal=OPENAI_API_KEY="sk-..."
    cli: |
      ```bash
      export OPENAI_API_KEY="your-openai-api-key"
      holmes ask "what pods are failing?"
      ```

Each Helm tab shows the values (under `holmes:` in the Robusta tab) and the chart's upgrade command.
The values are written as the page shows them, comments included.

Secrets. Every `{{ env.X }}` the values reference outside a comment line and set in no
`additionalEnvVars` entry is a key of the group's Kubernetes secret, in the order the values first
reference them. The secret is `holmes-<page file stem>`. The Helm tabs create it with
`kubectl create secret generic`, one `--from-literal=X=your-x` per key, and list it under
`extraEnvVarsSecrets`, which mounts each key as an env var; a derived CLI tab exports the same
variables.

`{secret-qualifier=<name>}` names the group's secret `holmes-<stem>-<name>`, for a group on the same
page that needs a secret with other keys. `<name>` is lowercase letters and digits, joined by `-`.

`{reuse}` is for a group whose secret an earlier group or step on the page creates: its Helm tabs
have no secret step, its values still list the secret, and a derived CLI tab still exports the keys.
The note naming the section that creates the secret is written by hand above the fence:

    In Kubernetes, this reuses the `<secret>` secret created in the [<section>](#<anchor>) section above.

Above a yaml-helm-values fence, which has no CLI tab, it reads "Reuses the ...".

Fields. Each is optional.

- `secret`: `--from-literal=X=<value>` and `--from-file=X=<path>` arguments of the group's secret.
  One for a derived key sets the value the page shows; one for a key the values never reference as
  `{{ env.X }}` (a variable the tool reads from the environment) adds it, after the derived keys.
- `named-secrets`: secrets the values name themselves, which are not listed in
  `extraEnvVarsSecrets`: a file mounted through `additionalVolumes`, or a secret an addon reads by
  `secretName`. A list of `name` and `keys`, `keys` being arguments as in `secret`. Their commands
  follow the group's secret in the same secret step.
- `deployment-values`: `[service-account]`, or `[service-account, holmes-deployment]`. Each Helm
  tab opens with the line that states the chart's names for them, for the placeholders the page's
  steps use.
- `cli` (yaml-toolset-config only): the Holmes CLI tab's markdown, for a CLI setup that is a
  different procedure. Without it the CLI tab is derived: the exports, the values' `toolsets` and
  `mcp_servers` keys for ~/.holmes/config.yaml, and the refresh warning. A fence with `cli` and no
  values has the Holmes CLI tab alone, for a toolset that runs only in the CLI.
- `test` (a derived CLI tab only): a command the CLI tab ends with, under "To test, run:".

The page hook. Secrets are named after the page, and the multi-instance link is relative to it; the
page reaches the extension through this module's `on_page_markdown` MkDocs hook, so mkdocs.yml lists
this file under `hooks:`. An MkDocs config that sets its own `hooks:`, including one that INHERITs
mkdocs.yml (the child's list replaces the parent's), must list this file too, or every fence fails
the build.
"""

import html
import posixpath
import re
from pathlib import PurePosixPath

import yaml  # type: ignore
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor

ROBUSTA_REGIONS = (("US", ""), ("EU", "eu"), ("AP", "ap"))
ROBUSTA_DOMAIN_RE = re.compile(r"\b(api|platform|sp)\.robusta\.dev\b")
MARKDOWN_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)\s]+)\)(\{[^}]*\})?$")


def _rewrite_robusta_domain(text: str, region_infix: str) -> str:
    """Rewrite api/platform/sp .robusta.dev to the regional variant."""
    if not region_infix:
        return text
    return ROBUSTA_DOMAIN_RE.sub(rf"\1.{region_infix}.robusta.dev", text)


def robusta_region_fence_format(source, language, css_class, options, md, **kwargs):
    """
    Render the source as three tabs (US, EU, AP), rewriting `api.robusta.dev`,
    `platform.robusta.dev` and `sp.robusta.dev` to the regional subdomain in each tab.

    Auto-detects two input shapes:

    1. A markdown link `[text](url)` (with optional `{...}` attribute list) →
       renders as a clickable link per region.
    2. Anything else → renders as a code block per region. Pass `lang=<name>`
       in the fence options to set syntax highlighting (e.g. `lang=yaml`).

    Usage:

        ```robusta-region
        https://api.robusta.dev/litellm/model_prices_and_context_window.json
        ```

        ```robusta-region
        [platform.robusta.dev](https://platform.robusta.dev/)
        ```

        ````robusta-region lang=yaml
        holmes:
          additionalEnvVars:
            - name: ROBUSTA_API_ENDPOINT
              value: "https://api.robusta.dev"
        ````
    """
    inner = source.strip()
    # Inline `{lang=yaml}` attrs arrive via kwargs['attrs']; config-level options
    # come from mkdocs.yml (currently unused).
    attrs = kwargs.get("attrs") or {}
    inner_lang = attrs.get("lang") or (options or {}).get("lang") or ""
    lang_class_attr = (
        f' class="language-{html.escape(inner_lang)}"' if inner_lang else ""
    )

    link_match = MARKDOWN_LINK_RE.match(inner)

    # Markdown is built once per page, so the count numbers the page's groups and
    # two builds give the same ids; the prefix keeps them apart from tabbed's own.
    md.robusta_region_groups = getattr(md, "robusta_region_groups", 0) + 1
    group_name = f"__tabbed_robusta_region_{md.robusta_region_groups}"

    inputs_html = ""
    labels_html = ""
    blocks_html = ""

    for index, (region_name, region_infix) in enumerate(ROBUSTA_REGIONS, start=1):
        tab_id = f"{group_name}_{index}"
        checked_attr = ' checked="checked"' if index == 1 else ""
        inputs_html += (
            f'<input{checked_attr} id="{tab_id}" name="{group_name}" type="radio">\n'
        )
        labels_html += f'<label for="{tab_id}">{region_name}</label>\n'

        if link_match:
            link_text, link_url, _attrs = link_match.groups()
            regional_text = _rewrite_robusta_domain(link_text, region_infix)
            regional_url = _rewrite_robusta_domain(link_url, region_infix)
            inner_html = (
                f'<p><a href="{html.escape(regional_url)}">'
                f"{html.escape(regional_text)}</a></p>"
            )
        else:
            regional_content = _rewrite_robusta_domain(inner, region_infix)
            inner_html = (
                f"<pre><code{lang_class_attr}>{html.escape(regional_content)}"
                "</code></pre>"
            )

        blocks_html += f'<div class="tabbed-block">{inner_html}</div>\n'

    return (
        '<div class="tabbed-set" data-tabs="1:3">\n'
        f"{inputs_html}"
        f'<div class="tabbed-labels">\n{labels_html}</div>\n'
        f'<div class="tabbed-content">\n{blocks_html}</div>\n'
        "</div>"
    )


# The name mkdocs.yml lists this module under in `markdown_extensions`; the hook
# passes each page's path to the extension through this key of `mdx_configs`.
EXTENSION_NAME = "docs.custom_fences"
NO_PAGE = (
    f"but no page was given to the {EXTENSION_NAME} extension: "
    "list docs/custom_fences.py under hooks: in the MkDocs config"
)

TOOLSET_CONFIG_FENCE = "yaml-toolset-config"
HELM_VALUES_FENCE = "yaml-helm-values"
MULTI_INSTANCE_FENCE = "multi-instance"
# The page every multi-instance section links to, as a path under docs/.
MULTI_INSTANCE_PAGE = "data-sources/multi-instance-toolsets.md"

ENV_REFERENCE_RE = re.compile(r"\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# A line that opens one of the three fences in any form ...
FENCE_OPENING_RE = re.compile(
    r"^[ \t>]*(?:`{3,}|~{3,})\s*\.?"
    rf"(?:{TOOLSET_CONFIG_FENCE}|{HELM_VALUES_FENCE}|{MULTI_INSTANCE_FENCE})"
)
# ... and the forms pages write.
SUPPORTED_OPENING_RE = re.compile(
    rf"^```(?:(?P<multi>{MULTI_INSTANCE_FENCE})"
    rf"|(?P<deployment>{TOOLSET_CONFIG_FENCE}|{HELM_VALUES_FENCE})"
    r"(?: \{(?P<option>reuse|secret-qualifier=(?P<qualifier>[a-z0-9]+(?:-[a-z0-9]+)*))\})?)$"
)
CLOSING_LINE = "```"
# The line of a deployment fence body that ends the values and starts its fields.
FIELDS_SEPARATOR = "---"
# A key of a secret, as one argument of `kubectl create secret generic`.
SECRET_ARGUMENT_RE = re.compile(
    r"--from-(?P<kind>literal|file)=(?P<key>[A-Za-z0-9_.-]+)=(?P<value>\S.*)"
)
# The top-level keys of the values that are Holmes config, which a derived CLI tab shows.
CLI_CONFIG_KEYS = ("toolsets", "mcp_servers")
# The chart-specific names a Helm tab can state, and the lines that state them.
DEPLOYMENT_VALUES = {
    ("service-account",): ". Use it as `<service-account>` on this page.",
    ("service-account", "holmes-deployment"): (
        " in the deployment `{release}-holmes`. Use them as `<service-account>` and "
        "`<holmes-deployment>` on this page."
    ),
}
SERVICE_ACCOUNT_LINE = (
    "Holmes runs as the service account `{release}-holmes-service-account` (the chart's "
    "default; if you set `customServiceAccountName`, it runs as that name, and with "
    "`createServiceAccount: false`, as the namespace's `default` service account)"
)

HOLMES_VALUES_CAPTION = (
    "When using the **standalone Holmes Helm Chart**, update your `values.yaml`:"
)
ROBUSTA_VALUES_CAPTION = "When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:"
CLI_CONFIG_CAPTION = "Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:"
SECRET_CAPTION = "Create a Kubernetes secret in the namespace Holmes runs in:"
SECRETS_CAPTION = "Create the Kubernetes secrets in the namespace Holmes runs in:"
APPLY_CAPTION = "Apply the configuration:"
TEST_CAPTION = "To test, run:"
HOLMES_UPGRADE_COMMAND = "helm upgrade holmes robusta/holmes -f values.yaml"
ROBUSTA_UPGRADE_COMMAND = "helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>"
MULTI_INSTANCE_LEAD = "List each one under `instances:` with a unique `name`."
REFRESH_WARNING_INCLUDE = '--8<-- "snippets/toolset_refresh_warning.md"'


class TabFenceError(Exception):
    """A tab fence that cannot be rendered; raised from a preprocessor, it fails the build."""


class FenceBodyError(Exception):
    """A fence body that cannot be rendered, with the reason; the preprocessor
    adds the page and line."""


def _code_block(language: str, text: str) -> str:
    return f"```{language}\n{text}\n```"


def _indent(text: str, prefix: str) -> str:
    """Prefix every non-empty line."""
    return "\n".join(prefix + line if line else line for line in text.split("\n"))


def _tab(label: str, elements: list) -> str:
    return f'=== "{label}"\n\n' + _indent("\n\n".join(elements), "    ")


def _secret_placeholder(key: str) -> str:
    """The value a reader replaces: `DATADOG_API_KEY` gives `your-datadog-api-key`."""
    return "your-" + key.lower().replace("_", "-")


def _load(text: str):
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise FenceBodyError(f"the fence body is not valid YAML: {e}") from e


def _block_mapping(body: str):
    """The mapping `body` loads as, if it is a block mapping whose first key starts at
    the first column, below any comment lines, else None."""
    data = _load(body)
    first = next(
        (line for line in body.split("\n") if line.strip() and not line.startswith("#")), ""
    )
    return data if isinstance(data, dict) and re.match(r"[A-Za-z_]", first) else None


def _multi_instance_section(body: str, page: str):
    """The standard "Multiple Instances" section for the toolset `body` names, or
    None if the body is not a supported form: its config example nested under
    `instances:` twice, the tools multiple instances add, and a link to the
    Multiple Instances page."""
    spec = _block_mapping(body)
    if spec is None or set(spec) != {"toolset", "name", "config"}:
        return None
    toolset, name, config = spec["toolset"], spec["name"], spec["config"]
    if not all(isinstance(value, str) and value.strip() for value in spec.values()):
        return None
    config = config.strip()
    # The wrapper names the discovery tool by replacing '/' with '_' in the toolset name.
    list_tool = toolset.replace("/", "_") + "_list_instances"
    # `config` is a YAML block scalar, which YAML has already dedented.
    fields = _indent(config, " " * 10)
    example = (
        f"toolsets:\n  {toolset}:\n    enabled: true\n    config:\n      instances:\n"
        f"        - name: prod\n{fields}\n        - name: staging\n{fields}"
    )
    link = posixpath.relpath(MULTI_INSTANCE_PAGE, posixpath.dirname(page))
    return "\n\n".join(
        [
            f"The {name} toolset can connect to more than one {name} instance. "
            f"{MULTI_INSTANCE_LEAD} Any config field "
            "set outside `instances:` becomes a default that every instance inherits, "
            "so shared settings only need to be written once.",
            _code_block("yaml", example),
            "When more than one instance is configured, HolmesGPT automatically adds "
            f"an `instance` parameter to every {name} tool (so it can pick which "
            f"instance to query) and a `{list_tool}` tool to list the configured "
            "instances. With a single instance — including the flat config without "
            "`instances:` — the tools are unchanged and fully backwards compatible.",
            f"See [Multiple Instances]({link}) for the full behaviour, including "
            "global defaults and health reporting.",
        ]
    )


def _secret_arguments(arguments) -> dict:
    """{key: (kind, value)} for a list of `--from-literal=K=V` / `--from-file=K=PATH`
    arguments, in their order."""
    if not isinstance(arguments, list) or not arguments:
        raise FenceBodyError("a secret's keys are a list of --from-literal / --from-file arguments")
    keys: dict = {}
    for argument in arguments:
        match = SECRET_ARGUMENT_RE.fullmatch(argument) if isinstance(argument, str) else None
        if match is None or match["key"] in keys:
            raise FenceBodyError(f"not a --from-literal=KEY=VALUE or --from-file=KEY=PATH argument of a new key: {argument!r}")
        keys[match["key"]] = (match["kind"], match["value"])
    return keys


def _secret_command(name: str, keys: dict) -> str:
    return " \\\n".join(
        [f"kubectl create secret generic {name}"]
        + [f"  --from-{kind}={key}={value}" for key, (kind, value) in keys.items()]
        + ["  -n <namespace>"]
    )


def _export(key: str, kind: str, value: str) -> str:
    return f"export {key}={value}" if kind == "literal" else f'export {key}="$(cat {value})"'


def _cli_config(values_text: str) -> str:
    """The Holmes config part of the values: each top-level CLI_CONFIG_KEYS key with
    the comment lines directly above it, in the values' order."""
    sections: list = []
    for line in values_text.split("\n"):
        key = re.match(r"([A-Za-z_][A-Za-z0-9_]*):", line)
        if key or not sections:
            # Comment lines directly above a key belong to it.
            comments: list = []
            while sections and sections[-1][1] and sections[-1][1][-1].startswith("#"):
                comments.insert(0, sections[-1][1].pop())
            sections.append((key[1] if key else None, comments + [line]))
        else:
            sections[-1][1].append(line)
    return "\n\n".join(
        "\n".join(lines).strip("\n") for key, lines in sections if key in CLI_CONFIG_KEYS
    )


def _environment_keys(values_text: str, values: dict, given: dict) -> dict:
    """{key: (kind, value)} of the group's env secret: every `{{ env.X }}` the values
    reference outside a comment and set in no `additionalEnvVars` entry, in the order
    the values first reference them, then the keys `given` adds; a key `given` names
    takes its value from there, any other key the placeholder value."""
    plain = {
        entry.get("name")
        for entry in values.get("additionalEnvVars") or []
        if isinstance(entry, dict)
    }
    code = "\n".join(
        line for line in values_text.split("\n") if not line.lstrip().startswith("#")
    )
    referenced = [key for key in dict.fromkeys(ENV_REFERENCE_RE.findall(code)) if key not in plain]
    keys = {key: given.get(key, ("literal", _secret_placeholder(key))) for key in referenced}
    keys.update({key: argument for key, argument in given.items() if key not in keys})
    return keys


def _deployment_section(opening, body: str, page: str):
    """The tab group of the deployment tab standard for a deployment fence body,
    or None if the body is not a supported form."""
    lines = body.split("\n")
    split = lines.index(FIELDS_SEPARATOR) if FIELDS_SEPARATOR in lines else len(lines)
    values_text = "\n".join(lines[:split]).strip("\n")
    fields_text = "\n".join(lines[split + 1 :]).strip("\n")
    values = _block_mapping(values_text) if values_text else {}
    fields = _block_mapping(fields_text) if split < len(lines) else {}
    if values is None or fields is None or not set(fields) <= {
        "secret", "named-secrets", "cli", "test", "deployment-values"
    }:
        return None
    toolset_config = opening["deployment"] == TOOLSET_CONFIG_FENCE
    cli = fields.get("cli")
    test = fields.get("test")
    if cli is not None and not (toolset_config and isinstance(cli, str) and cli.strip() and test is None):
        return None
    if test is not None and not (toolset_config and isinstance(test, str) and test.strip()):
        return None

    if not values_text:
        # A setting with no Kubernetes counterpart: the Holmes CLI tab alone.
        if cli is None or opening["option"] or set(fields) != {"cli"}:
            return None
        return _tab("Holmes CLI", [cli.strip("\n")])

    given = _secret_arguments(fields["secret"]) if "secret" in fields else {}
    keys = _environment_keys(values_text, values, given)
    secret = ""
    if keys:
        secret = f"holmes-{PurePosixPath(page).stem}"
        if opening["qualifier"]:
            secret += f"-{opening['qualifier']}"
    elif opening["option"]:
        return None
    commands = [_secret_command(secret, keys)] if keys and opening["option"] != "reuse" else []
    named = fields.get("named-secrets", [])
    if not isinstance(named, list) or not all(
        isinstance(entry, dict) and set(entry) == {"name", "keys"} and isinstance(entry["name"], str)
        for entry in named
    ):
        return None
    commands += [_secret_command(entry["name"], _secret_arguments(entry["keys"])) for entry in named]

    deployment_values = fields.get("deployment-values")
    if deployment_values is not None and (
        not isinstance(deployment_values, list) or tuple(deployment_values) not in DEPLOYMENT_VALUES
    ):
        return None

    values_text = f"extraEnvVarsSecrets:\n  - {secret}\n\n{values_text}" if secret else values_text
    tabs = []
    if toolset_config:
        if cli is None:
            config = _cli_config(values_text)
            if not config:
                return None
            exports = []
            if keys:
                exports = [
                    "Set the environment variable:" if len(keys) == 1 else "Set the environment variables:",
                    _code_block("bash", "\n".join(_export(key, *argument) for key, argument in keys.items())),
                ]
            elements = exports + [CLI_CONFIG_CAPTION, _code_block("yaml", config), REFRESH_WARNING_INCLUDE]
            if test is not None:
                elements += [TEST_CAPTION, _code_block("bash", test.strip("\n"))]
        else:
            elements = [cli.strip("\n")]
        tabs.append(_tab("Holmes CLI", elements))

    for label, release, caption, values_block, upgrade in (
        ("Holmes Helm Chart", "holmes", HOLMES_VALUES_CAPTION, values_text, HOLMES_UPGRADE_COMMAND),
        (
            "Robusta Helm Chart",
            "robusta",
            ROBUSTA_VALUES_CAPTION,
            "holmes:\n" + _indent(values_text, "  "),
            ROBUSTA_UPGRADE_COMMAND,
        ),
    ):
        elements = []
        if deployment_values is not None:
            line = SERVICE_ACCOUNT_LINE + DEPLOYMENT_VALUES[tuple(deployment_values)]
            elements.append(line.format(release=release))
        if commands:
            elements += [
                SECRET_CAPTION if len(commands) == 1 else SECRETS_CAPTION,
                _code_block("bash", "\n\n".join(commands)),
            ]
        elements += [
            caption,
            _code_block("yaml", values_block),
            APPLY_CAPTION,
            _code_block("bash", upgrade),
        ]
        tabs.append(_tab(label, elements))
    return "\n\n".join(tabs)


class TabFencePreprocessor(Preprocessor):
    """Replace each fence with its markdown.

    Registered after pymdownx.snippets, so it also sees fences inside included
    snippet files, and before superfences and tabbed, which then render the
    group as they render hand-written tabs: tab ids come from tabbed's slugs,
    as every other tab on the page gets them. The snippet includes the group
    itself carries are expanded by the snippets extension's own parser."""

    def __init__(self, md, page: str):
        super().__init__(md)
        self.page = page

    def run(self, lines):
        out: list = []
        i = 0
        while i < len(lines):
            if not FENCE_OPENING_RE.match(lines[i]):
                out.append(lines[i])
                i += 1
                continue
            if not self.page:
                raise TabFenceError(f"a custom fence needs the page's path, {NO_PAGE}")
            opening = SUPPORTED_OPENING_RE.match(lines[i])
            end = next(
                (j for j in range(i + 1, len(lines)) if lines[j] == CLOSING_LINE), None
            )
            group = None
            if opening and end:
                body = "\n".join(lines[i + 1 : end]).strip("\n")
                try:
                    group = (
                        _multi_instance_section(body, self.page)
                        if opening["multi"]
                        else _deployment_section(opening, body, self.page)
                    )
                except FenceBodyError as e:
                    raise TabFenceError(f"{self.page}:{i + 1}: {e}") from e
            if group is None:
                raise TabFenceError(
                    f"{self.page}:{i + 1}: unsupported form of a custom fence: "
                    f"{lines[i].strip()!r}. See the docstring of docs/custom_fences.py "
                    "for the supported forms"
                )
            expansion = group.split("\n")
            expansion = self.md.preprocessors["snippet"].parse_snippets(expansion)
            out.extend(["", *expansion, ""])
            i = end + 1
        return out


class TabFencesExtension(Extension):
    def __init__(self, **kwargs):
        self.config = {
            "page": [
                "",
                "Path of the page being converted; its file stem names the page's secrets",
            ]
        }
        super().__init__(**kwargs)

    def extendMarkdown(self, md):
        # After pymdownx.snippets (32), so a fence in an included file expands
        # too. Before every other preprocessor that reads fences or the page's
        # text: pymdownx.critic (31.1), the raw-block stash superfences adds
        # with preserve_tabs (31.05), whitespace normalization (30) and
        # superfences (25), which then see the expansion as hand-written tabs.
        md.preprocessors.register(
            TabFencePreprocessor(md, self.getConfig("page")), "tab_fences", 31.5
        )


def makeExtension(**kwargs):
    return TabFencesExtension(**kwargs)


def on_page_markdown(markdown, page, config, **kwargs):
    """MkDocs hook: give the tab fences the path of the page being built.

    MkDocs builds each page's Markdown instance from `mdx_configs` right after
    this event."""
    config["mdx_configs"].setdefault(EXTENSION_NAME, {})["page"] = page.file.src_uri
    return markdown


# Text an expansion contains, which a page shows literally when its markdown is
# rendered as text.
EXPANSION_MARKERS = (
    HOLMES_VALUES_CAPTION,
    ROBUSTA_VALUES_CAPTION,
    CLI_CONFIG_CAPTION.partition(". Create")[0],
    MULTI_INSTANCE_LEAD,
)
TAB_MARKDOWN_RE = re.compile(r'(?m)^\s*=== "')


def _text(rendered: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", rendered))


def unrendered_expansion(output: str) -> bool:
    """Whether the rendered page shows fence or tab markdown as text: a tab label
    line outside a code block, or a caption of an expansion anywhere."""
    outside_code = re.sub(r"<pre\b.*?</pre>", "", output, flags=re.S)
    return bool(TAB_MARKDOWN_RE.search(_text(outside_code))) or any(
        marker in _text(output) for marker in EXPANSION_MARKERS
    )


def on_post_page(output, page, config, **kwargs):
    """MkDocs hook: fail a page that shows a fence's markdown instead of its tabs.

    The raw lines of a page cannot show the contexts that leave the markdown of
    an expansion on the page as text: raw HTML, and a code block that holds the
    fence."""
    if unrendered_expansion(output):
        raise TabFenceError(
            f"{page.file.src_uri} shows the markdown of a tab fence instead of tabs; "
            "a fence must stand at the start of a line, outside raw HTML and code blocks"
        )
    return output

"""
Custom fences for the MkDocs documentation.

- yaml-toolset-config: a Holmes config body, `toolsets:` and its content. Holmes CLI, Holmes Helm
  Chart and Robusta Helm Chart tabs. The CLI tab shows the body for ~/.holmes/config.yaml.
- yaml-helm-values: a Holmes chart values body, for chart-only settings. Holmes Helm Chart and
  Robusta Helm Chart tabs.
- robusta-region: Creates 3 tabs (US, EU, AP) for any text containing api.robusta.dev, platform.robusta.dev, or
  sp.robusta.dev. Plain URLs render as code blocks; markdown links `[text](url)` render as clickable links.
- multi-instance: the standard "Multiple Instances" section for a toolset. The body is YAML with
  `toolset` (the toolset's config key), `name` (its display name) and `config` (a single-instance
  config example, a block scalar). It links to the Multiple Instances page with a path relative to
  the page.

robusta-region is a superfences custom fence, registered in mkdocs.yml. The other three expand into
markdown before any other fence or tab is rendered, so each renders exactly as the same markdown
written by hand, tab ids included. Each renders from its own body and options and the page's path,
and reads nothing else on the page.

Supported forms. Write the fence at the start of a line, opened by three backticks and the fence
name, and closed by the first line of three backticks:

    ```yaml-toolset-config
    ```yaml-toolset-config {reuse}
    ```yaml-toolset-config {secret-qualifier=<name>}
    ```multi-instance

`yaml-helm-values` takes the same forms as `yaml-toolset-config`. The body of a deployment fence is
a block mapping whose first key starts at the first column; a `yaml-toolset-config` body has
`toolsets` as its only key. A `multi-instance` body has `toolset`, `name` and `config`. Any other form of
these three fences fails the build with a message naming the page and the line, and so does a page
whose rendered HTML shows a fence's markdown instead of its tabs (`on_post_page`).

Each Helm tab of the two deployment fences shows the values (under `holmes:` in the Robusta tab) and
the chart's upgrade command.

Secrets. Every `{{ env.X }}` in the body is a key of the group's Kubernetes secret, in the order the
body first references them. The secret is `holmes-<page file stem>`. The Helm tabs create it with
`kubectl create secret generic`, one `--from-literal=X=your-x` per key, and list it under
`extraEnvVarsSecrets`, which mounts each key as an env var; the CLI tab exports the same variables.

`{secret-qualifier=<name>}` names the group's secret `holmes-<stem>-<name>`, for a group on the same
page that needs a secret with other keys. `<name>` is lowercase letters and digits, joined by `-`.

`{reuse}` is for a group whose secret an earlier group on the page creates: its Helm tabs have no
secret step, its values still list the secret, and its CLI tab still exports the keys. The note
naming the section that creates the secret is written by hand above the fence:

    Reuses the `<secret>` secret created in the [<section>](#<anchor>) section above.

Above a yaml-toolset-config fence, which has a CLI tab, it reads "In Kubernetes, this reuses ...".

The page hook. Secrets are named after the page, and the multi-instance link is relative to it; the
page reaches the extension through this module's `on_page_markdown` MkDocs hook, so mkdocs.yml lists
this file under `hooks:`. An MkDocs config that sets its own `hooks:`, including one that INHERITs
mkdocs.yml (the child's list replaces the parent's), must list this file too, or every fence fails
the build.
"""

import html
import posixpath
import re
import uuid
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

    tab_group_id = str(uuid.uuid4()).replace("-", "_")
    group_name = f"__tabbed_{tab_group_id}"

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

HOLMES_VALUES_CAPTION = (
    "When using the **standalone Holmes Helm Chart**, update your `values.yaml`:"
)
ROBUSTA_VALUES_CAPTION = "When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:"
CLI_CONFIG_CAPTION = "Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:"
SECRET_CAPTION = "Create a Kubernetes secret in the namespace Holmes runs in:"
APPLY_CAPTION = "Apply the configuration:"
HOLMES_UPGRADE_COMMAND = "helm upgrade holmes robusta/holmes -f values.yaml"
ROBUSTA_UPGRADE_COMMAND = "helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>"
MULTI_INSTANCE_LEAD = "List each one under `instances:` with a unique `name`."
REFRESH_WARNING_INCLUDE = '--8<-- "snippets/toolset_refresh_warning.md"'


class TabFenceError(Exception):
    """A tab fence that cannot be rendered; raised from a preprocessor, it fails the build."""


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


def _deployment_group(
    fence: str, body: str, secret: str, keys: list, creates_secret: bool
) -> str:
    """The tab group of the deployment tab standard for one fence body.

    `secret` is the secret the values mount, "" for none, and `keys` are the env
    vars the values read from it. The Helm tabs have a secret step only when
    `creates_secret`; the CLI tab exports the keys either way, since the CLI
    reads them from the shell whichever group creates the Kubernetes secret."""
    secret_step = []
    exports = []
    values = f"extraEnvVarsSecrets:\n  - {secret}\n\n{body}" if secret else body
    if creates_secret:
        command = " \\\n".join(
            [f"kubectl create secret generic {secret}"]
            + [f"  --from-literal={key}={_secret_placeholder(key)}" for key in keys]
            + ["  -n <namespace>"]
        )
        secret_step = [SECRET_CAPTION, _code_block("bash", command)]
    if keys:
        exports = [
            "Set the environment variable:"
            if len(keys) == 1
            else "Set the environment variables:",
            _code_block(
                "bash",
                "\n".join(f"export {key}={_secret_placeholder(key)}" for key in keys),
            ),
        ]

    tabs = []
    if fence == TOOLSET_CONFIG_FENCE:
        tabs.append(
            _tab(
                "Holmes CLI",
                exports
                + [
                    CLI_CONFIG_CAPTION,
                    _code_block("yaml", body),
                    REFRESH_WARNING_INCLUDE,
                ],
            )
        )
    tabs.append(
        _tab(
            "Holmes Helm Chart",
            secret_step
            + [
                HOLMES_VALUES_CAPTION,
                _code_block("yaml", values),
                APPLY_CAPTION,
                _code_block("bash", HOLMES_UPGRADE_COMMAND),
            ],
        )
    )
    tabs.append(
        _tab(
            "Robusta Helm Chart",
            secret_step
            + [
                ROBUSTA_VALUES_CAPTION,
                _code_block("yaml", "holmes:\n" + _indent(values, "  ")),
                APPLY_CAPTION,
                _code_block("bash", ROBUSTA_UPGRADE_COMMAND),
            ],
        )
    )
    return "\n\n".join(tabs)


def _block_mapping(body: str):
    """The mapping `body` loads as, if it is a block mapping whose first key starts at
    the first column, else None."""
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) and re.match(r"[A-Za-z_]", body) else None


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


def _deployment_section(opening, body: str, page: str):
    """The tab group for a deployment fence body, or None if the body is not a
    supported form."""
    fence = opening["deployment"]
    data = _block_mapping(body)
    if data is None or (fence == TOOLSET_CONFIG_FENCE and set(data) != {"toolsets"}):
        return None
    # Every env var the body references is a key of the group's secret, which
    # extraEnvVarsSecrets mounts whole. The keys keep the body's order.
    keys = list(dict.fromkeys(ENV_REFERENCE_RE.findall(body)))
    if not keys:
        return (
            None if opening["option"] else _deployment_group(fence, body, "", [], False)
        )
    secret = f"holmes-{PurePosixPath(page).stem}"
    if opening["qualifier"]:
        secret += f"-{opening['qualifier']}"
    return _deployment_group(fence, body, secret, keys, opening["option"] != "reuse")


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
                group = (
                    _multi_instance_section(body, self.page)
                    if opening["multi"]
                    else _deployment_section(opening, body, self.page)
                )
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

"""
Custom fences for the MkDocs documentation.

Tab fences expand into tab markdown before any other fence or tab is rendered, so they render
exactly as the same tabs written by hand, ids included. A fence may sit indented in a list or in
a blockquote; it expands there.

- yaml-toolset-config: a Holmes config body. Holmes CLI, Holmes Helm Chart and Robusta Helm Chart
  tabs. The CLI tab shows the body's `toolsets` and `mcp_servers` keys for ~/.holmes/config.yaml.
- yaml-helm-values: a Holmes chart values body, for chart-only settings. Holmes Helm Chart and
  Robusta Helm Chart tabs.
- robusta-region: 3 tabs (US, EU, AP) for any text containing api.robusta.dev, platform.robusta.dev, or
  sp.robusta.dev, each with the domains rewritten to the region's. Plain text renders as a code block
  (`{lang=<name>}` sets its language); a markdown link `[text](url)` renders as a clickable link.

Each Helm tab of the two deployment fences shows the values (under `holmes:` in the Robusta tab) and
the chart's upgrade command.

Secrets. Every `{{ env.X }}` in a value of the body (not in a YAML comment) that no
`additionalEnvVars` entry sets by name is a key of the group's Kubernetes secret, in the order the
body first references them. The secret is `holmes-<page file stem>`. The Helm tabs create it with
`kubectl create secret generic`, one `--from-literal=X=your-x` per key, and list it under
`extraEnvVarsSecrets`, which mounts each key as an env var; the CLI tab exports the same variables.

`{secret-qualifier=<name>}` after the fence name names the group's secret `holmes-<stem>-<name>`,
for a group on the same page that needs a secret with other keys. `<name>` is lowercase letters,
digits and `-`, starting and ending with a letter or digit.

Reuse. A fence without a qualifier whose keys are all keys of a secret an earlier fence on the page
created reuses that secret: its Helm tabs have no secret step, its values still list the secret, its
CLI tab still exports the keys (in the secret's order), and a note above the group names the section
of the group that creates it:

    Reuses the `<secret>` secret created in the [<section>](#<anchor>) section above.

Above a yaml-toolset-config group, which has a CLI tab, it reads "In Kubernetes, this reuses ...".
The section is the last ATX or setext heading above the creating fence. Its link text is the
heading's raw markdown, and its anchor is the toc extension's slug of that raw text (or the
heading's `{#id}`), with toc's `_1` suffix for a repeated id. A heading whose text has markup (a
link, an emoji, inline HTML) gets an anchor the page does not have, since toc slugs the rendered
text. A heading in an admonition, a tab, a list or a blockquote is not followed, so the note names
the heading above it. MkDocs reports a link to a missing anchor only at INFO, so neither fails the
build.

A fence that cannot render as its tabs raises TabFenceError, which fails the build: a body that is
not a YAML mapping, a yaml-toolset-config body with neither `toolsets` nor `mcp_servers`, an option
the fence does not take, a qualifier or page name that makes no valid Kubernetes secret name, a
second secret of the same name on a page, a reuse whose creating fence sits under no heading, a
fence with no closing line, and a secret with no page.

The page hook. Secrets are named after the page, and the page reaches the extension through this
module's `on_page_markdown` MkDocs hook, so mkdocs.yml lists this file under `hooks:`. An MkDocs
config that sets its own `hooks:`, including one that INHERITs mkdocs.yml (the child's list
replaces the parent's), must list this file too, or every fence that reads a secret fails the build.

Superfences formatter:
- multi-instance: the standard "Multiple Instances" section for a toolset.
"""

import html
import re
from pathlib import PurePosixPath

import yaml  # type: ignore
from markdown.extensions import Extension
from markdown.extensions.toc import unique
from markdown.preprocessors import Preprocessor
from pymdownx.superfences import SuperFencesException

ROBUSTA_REGIONS = (("US", ""), ("EU", "eu"), ("AP", "ap"))
ROBUSTA_DOMAIN_RE = re.compile(r"\b(api|platform|sp)\.robusta\.dev\b")
MARKDOWN_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)\s]+)\)(\{[^}]*\})?$")


def _rewrite_robusta_domain(text: str, region_infix: str) -> str:
    """Rewrite api/platform/sp .robusta.dev to the regional variant."""
    if not region_infix:
        return text
    return ROBUSTA_DOMAIN_RE.sub(rf"\1.{region_infix}.robusta.dev", text)


# The name mkdocs.yml lists this module under in `markdown_extensions`; the hook
# passes each page's path to the extension through this key of `mdx_configs`.
EXTENSION_NAME = "docs.custom_fences"

TOOLSET_CONFIG_FENCE = "yaml-toolset-config"
HELM_VALUES_FENCE = "yaml-helm-values"
REGION_FENCE = "robusta-region"
# The header options each tab fence takes, as `{name=value}` after the fence name.
FENCE_OPTIONS = {
    TOOLSET_CONFIG_FENCE: ("secret-qualifier",),
    HELM_VALUES_FENCE: ("secret-qualifier",),
    REGION_FENCE: ("lang",),
}

# Top-level keys of a Holmes config body that the CLI reads from
# ~/.holmes/config.yaml; every other top-level key is a chart value.
CLI_CONFIG_KEYS = ("toolsets", "mcp_servers")

ENV_REFERENCE_RE = re.compile(r"\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# A fence's opening line; `indent` is the spaces and blockquote markers before it.
FENCE_START_RE = re.compile(r"^(?P<indent>[ >]*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
FENCE_OPTIONS_RE = re.compile(r"^\{(?P<options>[^}]*)\}$")
FENCE_OPTION_RE = re.compile(
    r'(?P<name>[A-Za-z][\w-]*)=(?:"(?P<quoted>[^"]*)"|(?P<bare>[^\s"]+))'
)
# An ATX heading, with an optional attr_list id: `## Title {#id}`.
HEADING_RE = re.compile(
    r"^#{1,6}\s+(?P<text>.*?)(?:\s+#+)?\s*(?:\{[^}]*#(?P<id>[\w-]+)[^}]*\})?\s*$"
)
# A setext heading's text line, with an optional attr_list id, and its underline.
SETEXT_TEXT_RE = re.compile(
    r"^ {0,3}(?P<text>\S.*?)\s*(?:\{[^}]*#(?P<id>[\w-]+)[^}]*\})?\s*$"
)
SETEXT_UNDERLINE_RE = re.compile(r"^[=-]+ *$")
# Kubernetes names a Secret by a DNS-1123 subdomain; a qualifier is one label of it.
DNS1123_LABEL = r"[a-z0-9]([-a-z0-9]*[a-z0-9])?"
DNS1123_LABEL_RE = re.compile(rf"^{DNS1123_LABEL}$")
DNS1123_SUBDOMAIN_RE = re.compile(rf"^{DNS1123_LABEL}(\.{DNS1123_LABEL})*$")
DNS1123_SUBDOMAIN_MAX_LENGTH = 253

HOLMES_VALUES_CAPTION = (
    "When using the **standalone Holmes Helm Chart**, update your `values.yaml`:"
)
ROBUSTA_VALUES_CAPTION = "When using the **Robusta Helm Chart** (which includes HolmesGPT), update your `generated_values.yaml`:"
CLI_CONFIG_CAPTION = "Add the following to **~/.holmes/config.yaml**. Create the file if it doesn't exist:"
SECRET_CAPTION = "Create a Kubernetes secret in the namespace Holmes runs in:"
APPLY_CAPTION = "Apply the configuration:"
HOLMES_UPGRADE_COMMAND = "helm upgrade holmesgpt robusta/holmes -f values.yaml"
ROBUSTA_UPGRADE_COMMAND = "helm upgrade robusta robusta/robusta -f generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>"
REFRESH_WARNING_INCLUDE = '--8<-- "snippets/toolset_refresh_warning.md"'


class TabFenceError(Exception):
    """A tab fence that cannot be rendered; raised from a preprocessor, it fails the build."""


def _code_block(language: str, text: str) -> str:
    return f"```{language}\n{text}\n```"


def _indent(text: str, prefix: str) -> str:
    """Prefix every non-empty line. An empty line gets the prefix without its
    trailing spaces: empty for an indent, `>` for a blockquote, which a truly
    empty line would end."""
    return "\n".join(
        prefix + line if line else prefix.rstrip() for line in text.split("\n")
    )


def _tab(label: str, elements: list) -> str:
    return f'=== "{label}"\n\n' + _indent("\n\n".join(elements), "    ")


def _secret_placeholder(key: str) -> str:
    """The value a reader replaces: `DATADOG_API_KEY` gives `your-datadog-api-key`."""
    return "your-" + key.lower().replace("_", "-")


def _cli_config(body: str) -> str:
    """The top-level keys of `body` the CLI reads, as the body has them.

    A key's block runs from its line to the next top-level key; blank lines and
    column-0 comments directly above a key belong to that key."""
    blocks: list = []
    pending: list = []
    for line in body.split("\n"):
        if not line.strip() or line.startswith("#"):
            pending.append(line)
        elif line[0] != " " or not blocks:
            blocks.append([line.split(":", 1)[0].strip(), pending + [line]])
            pending = []
        else:
            blocks[-1][1] += pending + [line]
            pending = []
    kept = "\n".join(
        line for key, lines in blocks if key in CLI_CONFIG_KEYS for line in lines
    )
    return kept.strip("\n")


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
                    _code_block("yaml", _cli_config(body)),
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


def _fence_end(lines: list, start: int, indent: str, fence: str):
    """The index of the line closing the fence opened at `start`, or None.

    Follows superfences: the closing line is the opening fence string at the
    opening indentation, and a non-empty line indented less ends the search. In
    a blockquote, the indentation holds its markers, and a line of the markers
    alone is an empty line of the quote."""
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if not line.strip() or line.rstrip() == indent.rstrip():
            continue
        if not line.startswith(indent):
            return None
        if line[len(indent) :].rstrip() == fence:
            return i
    return None


def _region_tabs(body: str, lang: str) -> str:
    """US, EU and AP tabs, each with `body` rewritten to the region's domains:
    as a paragraph when `body` is a markdown link, as a code block otherwise."""
    is_link = MARKDOWN_LINK_RE.match(body)
    return "\n\n".join(
        _tab(
            region,
            [
                _rewrite_robusta_domain(body, infix)
                if is_link
                else _code_block(lang, _rewrite_robusta_domain(body, infix))
            ],
        )
        for region, infix in ROBUSTA_REGIONS
    )


class TabFencePreprocessor(Preprocessor):
    """Replace each tab fence with its tab group's markdown.

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
        # The secrets deployment groups earlier on this page create: name -> (keys, section).
        self.secrets: dict = {}
        # The last heading above the current line, as (text, id), and the ids used so far.
        self.section = None
        self.heading_ids: set = set()
        i = 0
        while i < len(lines):
            match = FENCE_START_RE.match(lines[i])
            end = match and _fence_end(lines, i, match["indent"], match["fence"])
            info = match["info"].strip() if match else ""
            fence, _, header = info.partition(" ")
            if match and not end and fence in FENCE_OPTIONS:
                # Superfences no longer knows these fences, so an unexpanded one
                # would render as plain code.
                raise TabFenceError(
                    f"the {fence} fence on {self.page or 'this page'} has no closing "
                    f"{match['fence']} line with the opening line's indentation "
                    f"{match['indent']!r} before it"
                )
            if not end:
                self._track_heading(lines, i)
                out.append(lines[i])
                i += 1
                continue
            if fence not in FENCE_OPTIONS:
                out.extend(lines[i : end + 1])
            else:
                indent = match["indent"]
                body = "\n".join(line[len(indent) :] for line in lines[i + 1 : end])
                where = f"the {fence} fence on {self.page or 'this page'}"
                options = self._options(where, fence, header.strip())
                if fence == REGION_FENCE:
                    group = _region_tabs(body.strip(), options.get("lang", ""))
                else:
                    group = self._group(where, fence, options, body.strip("\n"))
                expansion = group.split("\n")
                if "snippet" in self.md.preprocessors:
                    expansion = self.md.preprocessors["snippet"].parse_snippets(
                        expansion
                    )
                expansion = ["", *expansion, ""]
                out.extend(_indent("\n".join(expansion), indent).split("\n"))
            i = end + 1
        return out

    def _track_heading(self, lines: list, i: int) -> None:
        """Follow the page's headings, giving each the id the toc extension gives it.

        `lines[i]` is an ATX heading, or the text of a setext heading when it
        opens a block and the next line underlines it."""
        heading = HEADING_RE.match(lines[i])
        if not heading and (i == 0 or not lines[i - 1].strip()):
            if i + 1 < len(lines) and SETEXT_UNDERLINE_RE.match(lines[i + 1]):
                heading = SETEXT_TEXT_RE.match(lines[i])
        if not heading or "toc" not in self.md.treeprocessors:
            return
        toc = self.md.treeprocessors["toc"]
        text = heading["text"]
        anchor = heading["id"] or toc.slugify(html.unescape(text), toc.sep)
        self.section = (text, unique(anchor, self.heading_ids))

    @staticmethod
    def _options(where: str, fence: str, header: str) -> dict:
        match = FENCE_OPTIONS_RE.match(header)
        if header and not match:
            raise TabFenceError(f"{where} takes options as {{name=value}}: {header}")
        text = match["options"] if match else ""
        options = {
            option["name"]: option["quoted"]
            if option["quoted"] is not None
            else option["bare"]
            for option in FENCE_OPTION_RE.finditer(text)
        }
        leftover = FENCE_OPTION_RE.sub("", text).strip()
        unknown = sorted(set(options) - set(FENCE_OPTIONS[fence]))
        if leftover or unknown:
            raise TabFenceError(
                f"{where} takes only the options {', '.join(FENCE_OPTIONS[fence])}: {header}"
            )
        return options

    def _group(self, where: str, fence: str, options: dict, body: str) -> str:
        try:
            data = yaml.safe_load(body)
        except yaml.YAMLError as e:
            raise TabFenceError(f"{where} is not valid YAML: {e}") from e
        if not isinstance(data, dict):
            raise TabFenceError(f"{where} must be a YAML mapping")
        if fence == TOOLSET_CONFIG_FENCE and not set(data) & set(CLI_CONFIG_KEYS):
            raise TabFenceError(
                f"{where} sets none of {', '.join(CLI_CONFIG_KEYS)}; "
                f"use {HELM_VALUES_FENCE} for chart-only values"
            )

        # Every env var the body's values reference (not its comments) and no
        # additionalEnvVars entry sets is a key of the group's secret, which
        # extraEnvVarsSecrets mounts whole. The keys keep the body's order.
        set_by_chart = {
            entry.get("name")
            for entry in data.get("additionalEnvVars") or []
            if isinstance(entry, dict)
        }
        keys = [
            key
            for key in dict.fromkeys(
                reference
                for token in yaml.scan(body)
                if isinstance(token, yaml.ScalarToken)
                for reference in ENV_REFERENCE_RE.findall(token.value)
            )
            if key not in set_by_chart
        ]
        qualifier = options.get("secret-qualifier")
        if qualifier is not None and not DNS1123_LABEL_RE.match(qualifier):
            raise TabFenceError(
                f"{where} takes a secret-qualifier of lowercase letters, digits and "
                f"'-', starting and ending with a letter or digit, so the secret "
                f"name is a valid Kubernetes name: {qualifier!r}"
            )
        if not keys:
            if qualifier:
                raise TabFenceError(
                    f"{where} reads no secret, so it takes no secret-qualifier"
                )
            return _deployment_group(fence, body, "", [], False)
        if not self.page:
            raise TabFenceError(
                f"{where} reads a secret, which is named after the page, "
                f"but no page was given to the {EXTENSION_NAME} extension: list "
                "docs/custom_fences.py under hooks: in the MkDocs config"
            )

        # A group whose keys a secret an earlier group created holds reuses that
        # secret: its values still mount it, its Helm tabs have no secret step,
        # its CLI tab still exports the keys, and a note names the section that
        # creates it. The note holds only in Kubernetes, so above a Holmes CLI
        # tab it says so.
        if not qualifier:
            for secret, (created, section) in self.secrets.items():
                if not set(keys) <= set(created):
                    continue
                if section is None:
                    raise TabFenceError(
                        f"{where} reuses the secret {secret}, but the group that "
                        "creates it sits under no heading for the note to name"
                    )
                note = (
                    (
                        "In Kubernetes, this reuses"
                        if fence == TOOLSET_CONFIG_FENCE
                        else "Reuses"
                    )
                    + f" the `{secret}` secret created in the "
                    f"[{section[0]}](#{section[1]}) section above."
                )
                keys = [key for key in created if key in keys]  # the secret's order
                return (
                    note + "\n\n" + _deployment_group(fence, body, secret, keys, False)
                )

        secret = f"holmes-{PurePosixPath(self.page).stem}"
        if qualifier:
            secret += f"-{qualifier}"
        if (
            not DNS1123_SUBDOMAIN_RE.match(secret)
            or len(secret) > DNS1123_SUBDOMAIN_MAX_LENGTH
        ):
            raise TabFenceError(
                f"{where} names its secret {secret} after the page's file name, and "
                "that is not a valid Kubernetes secret name (a DNS-1123 subdomain "
                f"of at most {DNS1123_SUBDOMAIN_MAX_LENGTH} characters)"
            )
        if secret in self.secrets:
            raise TabFenceError(
                f"{where} creates the secret {secret}, which an earlier group on the page "
                "creates with other keys; give it a secret-qualifier"
            )
        self.secrets[secret] = (keys, self.section)
        return _deployment_group(fence, body, secret, keys, True)


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
            TabFencePreprocessor(md, self.getConfig("page")),
            "tab_fences",
            31.5,
        )


def makeExtension(**kwargs):
    return TabFencesExtension(**kwargs)


def on_page_markdown(markdown, page, config, **kwargs):
    """MkDocs hook: give the tab fences the path of the page being built.

    MkDocs builds each page's Markdown instance from `mdx_configs` right after
    this event."""
    config["mdx_configs"].setdefault(EXTENSION_NAME, {})["page"] = page.file.src_uri
    return markdown


# Central page that documents how multi-instance toolsets work. Linked from every
# rendered ``multi-instance`` block so each toolset page doesn't repeat the prose.
MULTI_INSTANCE_DOC_URL = "/data-sources/multi-instance-toolsets/"


def _reindent(text: str, spaces: int) -> str:
    """Dedent ``text`` to its common leading whitespace, then indent every
    non-empty line by ``spaces``. Used to nest a flat config example under
    ``instances:`` at the correct YAML depth."""
    lines = text.strip("\n").split("\n")
    nonempty = [ln for ln in lines if ln.strip()]
    base = min((len(ln) - len(ln.lstrip()) for ln in nonempty), default=0)
    pad = " " * spaces
    return "\n".join(pad + ln[base:] if ln.strip() else "" for ln in lines)


class MultiInstanceFenceError(SuperFencesException):
    """Superfences catches any other exception a fence raises and renders the
    block as plain code; this one fails the build."""


def multi_instance_fence_format(source, language, css_class, options, md, **kwargs):
    """Render the standard "Multiple Instances" section for a toolset.

    The fence body is YAML with three keys:

        ```multi-instance
        toolset: grafana/dashboards   # the toolset key used in config examples
        name: Grafana                 # human-readable name (optional; derived from toolset)
        config: |                     # a single-instance config example for this toolset
          api_url: <your grafana url>
          api_key: <your api key>
        ```

    It emits a note admonition that:
    - explains the toolset can connect to several instances via ``instances:``;
    - shows the supplied config example nested under ``instances:`` (two entries);
    - notes the auto-injected ``instance`` parameter and ``<toolset>_list_instances``
      tool that appear when more than one instance is configured;
    - links to the central Multiple Instances page for the full behaviour.

    The same component renders identically for every toolset, so each page imports
    it in one fenced block instead of repeating the prose.
    """
    try:
        spec = yaml.safe_load(source) or {}
    except yaml.YAMLError as e:
        raise MultiInstanceFenceError(
            f"multi-instance fence body is not valid YAML: {e}"
        ) from e
    if not isinstance(spec, dict):
        raise MultiInstanceFenceError(
            "multi-instance fence body must be a YAML mapping"
        )
    toolset = str(spec.get("toolset", "")).strip()
    name = str(spec.get("name") or toolset or "this").strip()
    config = str(spec.get("config", "")).strip()
    if not toolset or not config:
        raise MultiInstanceFenceError(
            "multi-instance fence requires 'toolset' and 'config' keys in its YAML body"
        )

    # The wrapper names the discovery tool by replacing '/' with '_' in the toolset name.
    list_tool = spec.get("list_tool") or (toolset.replace("/", "_") + "_list_instances")

    fields = _reindent(config, 10)
    yaml_example = (
        "toolsets:\n"
        f"  {toolset}:\n"
        "    enabled: true\n"
        "    config:\n"
        "      instances:\n"
        f"        - name: prod\n{fields}\n"
        f"        - name: staging\n{fields}\n"
    )

    name_e = html.escape(name)
    list_tool_e = html.escape(str(list_tool))
    return (
        f"<p>The {name_e} toolset can connect to more than one {name_e} instance. "
        "List each one under <code>instances:</code> with a unique <code>name</code>. "
        "Any config field set outside <code>instances:</code> becomes a default that "
        "every instance inherits, so shared settings only need to be written once.</p>\n"
        f'<pre><code class="language-yaml">{html.escape(yaml_example)}</code></pre>\n'
        "<p>When more than one instance is configured, HolmesGPT automatically adds an "
        f"<code>instance</code> parameter to every {name_e} tool (so it can pick which "
        f"instance to query) and a <code>{list_tool_e}</code> tool to list the configured "
        "instances. With a single instance — including the flat config without "
        "<code>instances:</code> — the tools are unchanged and fully backwards "
        "compatible.</p>\n"
        f'<p>See <a href="{MULTI_INSTANCE_DOC_URL}">Multiple Instances</a> for the full '
        "behaviour, including global defaults and health reporting.</p>"
    )

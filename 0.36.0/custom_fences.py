"""
Custom fence processors for MkDocs documentation.

Fences available:
- yaml-toolset-config: Creates 3 tabs (Holmes CLI, Holmes Helm Chart, Robusta Helm Chart) for toolset configurations
- yaml-helm-values: Creates 2 tabs (Holmes Helm Chart, Robusta Helm Chart) for Helm-only configurations like permissions
- robusta-region: Creates 3 tabs (US, EU, AP) for any text containing api.robusta.dev, platform.robusta.dev, or
  sp.robusta.dev. Plain URLs render as code blocks; markdown links `[text](url)` render as clickable links.
"""

import html
import re
import uuid

import yaml  # type: ignore

ROBUSTA_REGIONS = (("US", ""), ("EU", "eu"), ("AP", "ap"))
ROBUSTA_DOMAIN_RE = re.compile(r"\b(api|platform|sp)\.robusta\.dev\b")
MARKDOWN_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)\s]+)\)(\{[^}]*\})?$")


def _rewrite_robusta_domain(text: str, region_infix: str) -> str:
    """Rewrite api/platform/sp .robusta.dev to the regional variant."""
    if not region_infix:
        return text
    return ROBUSTA_DOMAIN_RE.sub(rf"\1.{region_infix}.robusta.dev", text)


def toolset_config_fence_format(source, language, css_class, options, md, **kwargs):
    """
    Format YAML content into Holmes CLI, Holmes Helm Chart, and Robusta Helm Chart tabs for toolset configuration.
    This fence does NOT process Jinja2, so {{ env.VAR }} stays as-is.
    """
    # Generate unique IDs for this tab group to prevent conflicts
    tab_group_id = str(uuid.uuid4()).replace("-", "_")
    tab_id_1 = f"__tabbed_{tab_group_id}_1"
    tab_id_2 = f"__tabbed_{tab_group_id}_2"
    tab_id_3 = f"__tabbed_{tab_group_id}_3"
    group_name = f"__tabbed_{tab_group_id}"

    # Escape HTML in the source to prevent XSS
    escaped_source = html.escape(source)

    # Strip any leading/trailing whitespace
    yaml_content = source.strip()

    # Indent the yaml content for Robusta (add 2 spaces to each line under holmes:)
    robusta_yaml_lines = yaml_content.split("\n")
    robusta_yaml_indented = "\n".join(
        "  " + line if line else "" for line in robusta_yaml_lines
    )

    # Build the tabbed HTML structure for CLI, Holmes Helm, and Robusta
    tabs_html = f"""
<div class="tabbed-set" data-tabs="1:3">
<input checked="checked" id="{tab_id_1}" name="{group_name}" type="radio">
<input id="{tab_id_2}" name="{group_name}" type="radio">
<input id="{tab_id_3}" name="{group_name}" type="radio">
<div class="tabbed-labels">
<label for="{tab_id_1}">Holmes CLI</label>
<label for="{tab_id_2}">Holmes Helm Chart</label>
<label for="{tab_id_3}">Robusta Helm Chart</label>
</div>
<div class="tabbed-content">
<div class="tabbed-block">
<p>Add the following to <strong>~/.holmes/config.yaml</strong>. Create the file if it doesn't exist:</p>
<pre><code class="language-yaml">{escaped_source}</code></pre>
</div>
<div class="tabbed-block">
<p>When using the <strong>standalone Holmes Helm Chart</strong>, update your <code>values.yaml</code>:</p>
<pre><code class="language-yaml">{escaped_source}</code></pre>
<p>Apply the configuration:</p>
<pre><code class="language-bash">helm upgrade holmes holmes/holmes --values=values.yaml</code></pre>
</div>
<div class="tabbed-block">
<p>When using the <strong>Robusta Helm Chart</strong> (which includes HolmesGPT), update your <code>generated_values.yaml</code>:</p>
<pre><code class="language-yaml">holmes:
{html.escape(robusta_yaml_indented)}</code></pre>
<p>Apply the configuration:</p>
<pre><code class="language-bash">helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=&lt;YOUR_CLUSTER_NAME&gt;</code></pre>
</div>
</div>
</div>"""

    return tabs_html


def helm_tabs_fence_format(source, language, css_class, options, md, **kwargs):
    """
    Format YAML content into Holmes and Robusta Helm Chart tabs.
    This fence does NOT process Jinja2, so {{ env.VAR }} stays as-is.
    """
    # Generate unique IDs for this tab group to prevent conflicts
    tab_group_id = str(uuid.uuid4()).replace("-", "_")
    tab_id_1 = f"__tabbed_{tab_group_id}_1"
    tab_id_2 = f"__tabbed_{tab_group_id}_2"
    group_name = f"__tabbed_{tab_group_id}"

    # Escape HTML in the source to prevent XSS
    escaped_source = html.escape(source)

    # Strip any leading/trailing whitespace
    yaml_content = source.strip()

    # Indent the yaml content for Robusta (add 2 spaces to each line)
    robusta_yaml_lines = yaml_content.split("\n")
    robusta_yaml_indented = "\n".join(
        "  " + line if line else "" for line in robusta_yaml_lines
    )

    # Build the tabbed HTML structure
    tabs_html = f"""
<div class="tabbed-set" data-tabs="1:2">
<input checked="checked" id="{tab_id_1}" name="{group_name}" type="radio">
<input id="{tab_id_2}" name="{group_name}" type="radio">
<div class="tabbed-labels">
<label for="{tab_id_1}">Holmes Helm Chart</label>
<label for="{tab_id_2}">Robusta Helm Chart</label>
</div>
<div class="tabbed-content">
<div class="tabbed-block">
<p>When using the <strong>standalone Holmes Helm Chart</strong>, update your <code>values.yaml</code>:</p>
<pre><code class="language-yaml">{escaped_source}</code></pre>
<p>Apply the configuration:</p>
<pre><code class="language-bash">helm upgrade holmes holmes/holmes --values=values.yaml</code></pre>
</div>
<div class="tabbed-block">
<p>When using the <strong>Robusta Helm Chart</strong> (which includes HolmesGPT), update your <code>generated_values.yaml</code> (note: add the <code>holmes:</code> prefix):</p>
<pre><code class="language-yaml">enableHolmesGPT: true
holmes:
{html.escape(robusta_yaml_indented)}</code></pre>
<p>Apply the configuration:</p>
<pre><code class="language-bash">helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=&lt;YOUR_CLUSTER_NAME&gt;</code></pre>
</div>
</div>
</div>"""

    return tabs_html


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
    spec = yaml.safe_load(source) or {}
    toolset = str(spec.get("toolset", "")).strip()
    name = str(spec.get("name") or toolset or "this").strip()
    config = str(spec.get("config", "")).strip()
    if not toolset or not config:
        raise ValueError(
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

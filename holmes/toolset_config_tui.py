"""Interactive TUI for configuring toolsets.

Entry points:
  - CLI:         ``holmes toolset config``
  - Interactive:  ``/config`` slash command
"""

import copy
import io
import logging
import traceback
import types
import webbrowser
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type, Union, get_args, get_origin

import yaml  # type: ignore
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.filters import Condition
from prompt_toolkit.styles import Style as PTStyle
from pydantic import BaseModel
from rich.console import Console

from holmes.config import DEFAULT_CONFIG_LOCATION, Config
from holmes.core.tools import Toolset, ToolsetStatusEnum, ToolsetType
from holmes.utils.pydantic_utils import PydanticUndefined

logger = logging.getLogger(__name__)

# ── Colour constants (keep consistent with interactive.py) ────────────
STATUS_COLOR = "yellow"
ERROR_COLOR = "red"
HELP_COLOR = "cyan"

# ── Pydantic type‑introspection helpers ───────────────────────────────

try:
    from typing import Annotated  # Python 3.9+
except ImportError:  # pragma: no cover
    Annotated = None  # type: ignore

# PEP 604 ``X | Y`` unions have origin ``types.UnionType`` (Python 3.10+).
_UNION_TYPES: Tuple[Any, ...] = (Union,)
_UnionType = getattr(types, "UnionType", None)
if _UnionType is not None:
    _UNION_TYPES = (Union, _UnionType)


def _extract_base_model_subclass(annotation: Any) -> Optional[Type[BaseModel]]:
    """Best-effort extraction of a BaseModel subclass from a type annotation."""
    if annotation is None:
        return None
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        if args:
            return _extract_base_model_subclass(args[0])
    if origin in _UNION_TYPES:
        args = [a for a in get_args(annotation) if a is not type(None)]  # noqa: E721
        if len(args) == 1:
            return _extract_base_model_subclass(args[0])
        return None
    try:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
    except TypeError:
        return None
    return None


def _resolve_primitive_type(annotation: Any) -> str:
    """Map a Python type annotation to a simple type tag."""
    if annotation is None:
        return "str"

    origin = get_origin(annotation)

    # Unwrap Optional / Union[X, None] (typing.Union and PEP 604 X | None)
    if origin in _UNION_TYPES:
        args = [a for a in get_args(annotation) if a is not type(None)]  # noqa: E721
        if len(args) == 1:
            return _resolve_primitive_type(args[0])
        return "str"

    if origin is Annotated:
        args = get_args(annotation)
        if args:
            return _resolve_primitive_type(args[0])

    # Check for BaseModel subclass
    nested = _extract_base_model_subclass(annotation)
    if nested is not None:
        return "model"

    # Check dict/list origins
    if origin in (dict, Dict):
        return "dict"
    if origin in (list, List):
        return "list"

    # Primitives
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is bool:
        return "bool"
    if annotation is str:
        return "str"

    return "str"


# ── Tree data‑model ──────────────────────────────────────────────────


@dataclass
class ConfigFieldNode:
    """One row in the config tree."""

    key: str
    field_type: str  # "str" | "int" | "float" | "bool" | "dict" | "list" | "model"
    value: Any = None
    title: str = ""
    description: str = ""
    required: bool = False
    children: List["ConfigFieldNode"] = field(default_factory=list)
    parent: Optional["ConfigFieldNode"] = None
    is_header: bool = False
    depth: int = 0
    dict_key: Optional[str] = None  # editable key name for dict children
    explicitly_set: bool = False  # True when user has edited this field


def build_tree_from_schema(
    config_class: Type[BaseModel],
    current_values: Dict[str, Any],
    depth: int = 0,
    parent: Optional[ConfigFieldNode] = None,
) -> List[ConfigFieldNode]:
    """Walk *config_class*.model_fields and build a flat‑ish list of tree nodes."""
    nodes: List[ConfigFieldNode] = []
    for field_name, field_info in config_class.model_fields.items():
        if getattr(field_info, "exclude", False):
            continue

        annotation = getattr(field_info, "annotation", None)
        ftype = _resolve_primitive_type(annotation)
        title = getattr(field_info, "title", None) or field_name
        description = getattr(field_info, "description", None) or ""
        required = getattr(field_info, "is_required", lambda: False)()

        # Current value - track whether it was explicitly provided
        cur = current_values.get(field_name)
        was_explicit = field_name in current_values

        # Default fallback
        if cur is None and not was_explicit:
            default = getattr(field_info, "default", PydanticUndefined)
            default_factory = getattr(field_info, "default_factory", None)
            if default is not PydanticUndefined and default is not None:
                cur = default
            elif default_factory is not None:
                try:
                    cur = default_factory()
                except Exception:
                    cur = None

        node = ConfigFieldNode(
            key=field_name,
            field_type=ftype,
            value=cur if ftype not in ("dict", "list", "model") else None,
            title=title,
            description=description,
            required=required,
            depth=depth,
            parent=parent,
            is_header=ftype in ("dict", "list", "model"),
            explicitly_set=was_explicit,
        )

        if ftype == "model":
            nested_cls = _extract_base_model_subclass(annotation)
            if nested_cls is not None:
                child_values = cur if isinstance(cur, dict) else {}
                node.children = build_tree_from_schema(
                    nested_cls, child_values, depth + 1, node
                )

        elif ftype == "dict":
            if isinstance(cur, dict):
                for i, (k, v) in enumerate(cur.items()):
                    child = ConfigFieldNode(
                        key=str(i),
                        field_type="str",
                        value=v,
                        dict_key=k,
                        depth=depth + 1,
                        parent=node,
                    )
                    node.children.append(child)

        elif ftype == "list":
            if isinstance(cur, list):
                for i, v in enumerate(cur):
                    child = ConfigFieldNode(
                        key=str(i),
                        field_type="str",
                        value=v,
                        depth=depth + 1,
                        parent=node,
                    )
                    node.children.append(child)

        nodes.append(node)
    return nodes


def _flatten_tree(nodes: List[ConfigFieldNode]) -> List[ConfigFieldNode]:
    """Flatten nested tree into a list preserving visual order."""
    flat: List[ConfigFieldNode] = []
    for node in nodes:
        flat.append(node)
        if node.children:
            flat.extend(_flatten_tree(node.children))
    return flat


def tree_to_dict(nodes: List[ConfigFieldNode]) -> Dict[str, Any]:
    """Convert the top-level tree nodes back to a plain config dict."""
    result: Dict[str, Any] = {}
    for node in nodes:
        if node.is_header and node.children:
            if node.field_type == "dict":
                result[node.key] = {
                    (c.dict_key if c.dict_key is not None else c.key): c.value
                    for c in node.children
                    if c.dict_key is None or c.dict_key  # skip entries with empty dict_key
                }
            elif node.field_type == "list":
                result[node.key] = [c.value for c in node.children]
            elif node.field_type == "model":
                result[node.key] = tree_to_dict(node.children)
        elif node.is_header and not node.children:
            # Empty dict/list/model – preserve empty container
            if node.field_type == "dict":
                result[node.key] = {}
            elif node.field_type == "list":
                result[node.key] = []
            elif node.field_type == "model":
                result[node.key] = {}
        else:
            if node.value is not None or node.explicitly_set:
                result[node.key] = node.value
    return result


# ── Config file save / merge ─────────────────────────────────────────


def set_toolset_config(
    toolsets: Dict[str, Any],
    toolset_name: str,
    config_dict: Dict[str, Any],
) -> None:
    """Set ``toolsets[toolset_name]`` to ``{"enabled": True, "config": config_dict}``."""
    if toolset_name not in toolsets or not isinstance(toolsets.get(toolset_name), dict):
        toolsets[toolset_name] = {}
    toolsets[toolset_name]["enabled"] = True
    toolsets[toolset_name]["config"] = config_dict


def save_config_to_file(
    config_file_path: Path,
    toolset_name: str,
    config_dict: Dict[str, Any],
) -> Tuple[bool, str]:
    """Merge *config_dict* into the YAML config file under ``toolsets.<name>``.

    Returns (success, message).  Never prints to stdout/stderr so the TUI
    stays intact.
    """
    config_file = Path(config_file_path)
    existing: Dict[str, Any] = {}
    if config_file.exists():
        with open(config_file, "r") as f:
            existing = yaml.safe_load(f) or {}

    if "toolsets" not in existing:
        existing["toolsets"] = {}
    set_toolset_config(existing["toolsets"], toolset_name, config_dict)

    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w") as f:
            yaml.dump(existing, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        return False, f"Failed to write {config_file}: {e}"

    return True, f"Configuration saved to {config_file}"


def _get_existing_config(toolset: Toolset, config: Config) -> Dict[str, Any]:
    """Return the existing config dict for *toolset* from the loaded Config, or ``{}``."""
    if config.toolsets and toolset.name in config.toolsets:
        ts_entry = config.toolsets[toolset.name]
        if isinstance(ts_entry, dict) and ts_entry.get("config"):
            return dict(ts_entry["config"])
    return {}


def run_config_test(toolset: Toolset, config_dict: Dict[str, Any]) -> Tuple[bool, str]:
    """Run prerequisite checks against *config_dict* and return (ok, message).

    All stdout/stderr/logging output is captured so it doesn't leak into the TUI.
    The captured output is appended to the returned message.
    """
    test_toolset = copy.copy(toolset)
    test_toolset.config = config_dict
    test_toolset.enabled = True
    test_toolset.status = ToolsetStatusEnum.DISABLED
    test_toolset.error = None

    # Capture every form of output that prerequisites might produce:
    #   1. logger.info / logger.warning  → temporary logging handler
    #   2. print() / sys.stdout writes   → redirect_stdout
    #   3. sys.stderr writes             → redirect_stderr
    log_buf = io.StringIO()
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    log_handler = logging.StreamHandler(log_buf)
    log_handler.setLevel(logging.DEBUG)
    root_logger = logging.getLogger()

    # Temporarily replace *all* root-logger handlers so that pre-existing
    # handlers (e.g. RichHandler) don't write to the real console while
    # the TUI is active.
    saved_handlers = root_logger.handlers
    saved_level = root_logger.level
    root_logger.handlers = [log_handler]
    root_logger.setLevel(logging.DEBUG)

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            test_toolset.check_prerequisites(silent=True)
    except Exception:
        stderr_buf.write(traceback.format_exc())
    finally:
        root_logger.handlers = saved_handlers
        root_logger.setLevel(saved_level)

    # Build result message
    captured = ""
    for buf in (stdout_buf, stderr_buf, log_buf):
        text = buf.getvalue().strip()
        if text:
            captured += text + "\n"

    if test_toolset.status == ToolsetStatusEnum.ENABLED:
        msg = "Prerequisites passed"
        if captured:
            msg += "\n" + captured
        return True, msg

    msg = f"Failed: {test_toolset.error or 'unknown error'}"
    if captured:
        msg += "\n" + captured
    return False, msg


# ── prompt_toolkit TUI helpers ────────────────────────────────────────

_MENU_STYLE = PTStyle.from_dict(
    {
        "hint": "#666666",
        "selected": "bold",
        "status-ok": "#00ff00 bold",
        "status-fail": "#ff0000 bold",
        "header": "bold underline",
        "dim": "#888888",
        "button": "bold",
        "button-selected": "bold reverse",
    }
)


def _run_selection_menu(
    items: List[str],
    title: str = "",
    hint: str = "Esc to cancel",
) -> Optional[int]:
    """Generic arrow-key menu. Returns selected index or None on cancel."""
    if not items:
        return None
    selected = [0]
    result: List[Optional[int]] = [None]

    def _get_text():
        lines: List[Tuple[str, str]] = []
        if title:
            lines.append(("class:header", f"  {title}\n\n"))
        for i, item in enumerate(items):
            if i == selected[0]:
                lines.append(("class:selected", f"  > {item}\n"))
            else:
                lines.append(("", f"    {item}\n"))
        lines.append(("class:hint", f"\n  {hint}"))
        return lines

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    @kb.add("left")
    def _up(_event: Any) -> None:
        selected[0] = (selected[0] - 1) % len(items)

    @kb.add("down")
    @kb.add("j")
    @kb.add("right")
    def _down(_event: Any) -> None:
        selected[0] = (selected[0] + 1) % len(items)

    @kb.add("enter")
    def _enter(event: Any) -> None:
        result[0] = selected[0]
        event.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event: Any) -> None:
        result[0] = None
        event.app.exit()

    for i in range(min(9, len(items))):

        @kb.add(str(i + 1))
        def _num(event: Any, idx: int = i) -> None:
            result[0] = idx
            event.app.exit()

    layout = Layout(Window(FormattedTextControl(_get_text, show_cursor=False)))
    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        style=_MENU_STYLE,
        full_screen=False,
        erase_when_done=True,
    )
    app.run()
    return result[0]


# ── Screen 1: select toolset ─────────────────────────────────────────


_MCP_SERVER_DOCS_URL = "https://holmesgpt.dev/latest/data-sources/remote-mcp-servers/"
_MCP_SELECTED_SENTINEL = object()


def select_toolset(toolsets: List[Toolset], console: Console) -> Optional[Toolset]:
    """Screen 1 – let the user pick a toolset to configure."""
    if not toolsets:
        console.print(
            f"[bold {ERROR_COLOR}]No configurable toolsets found.[/bold {ERROR_COLOR}]"
        )
        return None

    items: List[str] = []
    items.append(f"Add MCP Server - {_MCP_SERVER_DOCS_URL}")
    for t in toolsets:
        raw_status = t.status.value if t.status else "disabled"
        is_configured = getattr(t, "enabled", False)
        if raw_status == "enabled":
            merged_status = "enabled"
        elif raw_status == "failed" and not is_configured:
            merged_status = "unconfigured"
        elif raw_status == "failed" and is_configured:
            merged_status = "failed"
        else:
            merged_status = raw_status
        items.append(f"{t.name:<35} [{merged_status}]")

    idx = _run_selection_menu(
        items,
        title="Select a toolset to configure",
        hint="Up/Down to navigate, Enter to select, Esc to cancel",
    )
    if idx is None:
        return None
    if idx == 0:
        webbrowser.open(_MCP_SERVER_DOCS_URL)
        return _MCP_SELECTED_SENTINEL
    return toolsets[idx - 1]


# ── Screen 2: tree editor ─────────────────────────────────────────────

_BUTTON_LABELS = ["[ Test ]", "[ Reset ]", "[ Save ]", "[ Exit ]"]


def run_tree_editor(
    toolset: Toolset,
    initial_config: Dict[str, Any],
    config_file_path: Path,
) -> bool:
    """Screen 2 – full tree editor with inline editing and action buttons.

    Returns True if the configuration was saved at least once.
    """

    if not toolset.config_classes:
        raise ValueError(
            f"Toolset '{toolset.name}' has no config_classes; "
            "cannot open the tree editor for a non-configurable toolset."
        )
    config_class: Type[BaseModel] = toolset.config_classes[0]
    top_nodes = build_tree_from_schema(config_class, initial_config)
    flat_rows = _flatten_tree(top_nodes)

    # State
    cursor = [0]  # index into (flat_rows + buttons)
    editing = [False]
    editing_dict_key = [False]  # True when editing the key portion of a dict entry
    edit_buf = [Buffer()]
    status_lines: List[Tuple[str, str]] = []
    saved = [False]
    not_editing = Condition(lambda: not editing[0])

    total_items = lambda: len(flat_rows) + len(_BUTTON_LABELS)  # noqa: E731

    def _refresh_flat() -> None:
        nonlocal flat_rows
        flat_rows = _flatten_tree(top_nodes)

    # ── rendering ──

    def _compute_value_columns() -> Dict[Optional[int], int]:
        """For each sibling group, compute the max display-name length.

        Nodes are grouped by parent (id).  Dict children are excluded
        because they use their own ``=``-sign alignment.
        """
        groups: Dict[Optional[int], int] = {}
        for node in flat_rows:
            if node.dict_key is not None:
                continue
            parent_id = id(node.parent) if node.parent else None
            display_name = node.title if node.title else node.key
            name_len = len(display_name)
            if parent_id not in groups or name_len > groups[parent_id]:
                groups[parent_id] = name_len
        return groups

    def _value_pad(node: ConfigFieldNode, value_columns: Dict[Optional[int], int]) -> str:
        """Return the padding between the colon and the value for *node*."""
        display_name = node.title if node.title else node.key
        parent_id = id(node.parent) if node.parent else None
        max_name_len = value_columns.get(parent_id, len(display_name))
        return " " * (max_name_len - len(display_name))

    def _row_content_width(node: ConfigFieldNode, value_columns: Dict[Optional[int], int]) -> int:
        """Compute the visible width of a row's content (before comment/hints)."""
        indent = "  " * (node.depth + 1)
        prefix = "  "  # use non-selected width for alignment
        display_name = node.title if node.title else node.key
        pad = _value_pad(node, value_columns)

        if node.dict_key is not None:
            return 0  # no comments on these rows

        if node.is_header:
            count = len(node.children)
            type_bracket = "{}" if node.field_type == "dict" else "[]"
            return len(f"{indent}{prefix}{display_name}:{pad} {type_bracket[0]}{count} items{type_bracket[1]}")

        if node.field_type == "bool":
            val_display = str(node.value).lower() if node.value is not None else "null"
        elif node.value is None and not node.required:
            val_display = "<null>"
        elif node.value == "":
            val_display = "<empty>"
        else:
            val_display = str(node.value) if node.value is not None else ""

        return len(f"{indent}{prefix}{display_name}:{pad} {val_display}")

    def _compute_comment_column(value_columns: Dict[Optional[int], int]) -> int:
        """Find the column where all comments should start."""
        max_width = 0
        for node in flat_rows:
            if node.description or node.is_header:
                max_width = max(max_width, _row_content_width(node, value_columns))
        return max_width + 2 if max_width else 0  # 2 chars padding

    def _render_header_row(
        node: ConfigFieldNode,
        style: str,
        indent: str,
        prefix: str,
        display_name: str,
        pad: str,
        comment_col: int,
    ) -> List[Tuple[str, str]]:
        count = len(node.children)
        type_bracket = "{}" if node.field_type == "dict" else "[]"
        label = f"{indent}{prefix}{display_name}:{pad} {type_bracket[0]}{count} items{type_bracket[1]}"
        hint_text = "# Enter to add entry"
        if comment_col > 0:
            padding = max(2, comment_col - len(label))
            hints = " " * padding + hint_text
        else:
            hints = "  " + hint_text
        return [(style, label), ("class:dim", hints), ("", "\n")]

    def _render_dict_child_row(
        node: ConfigFieldNode,
        style: str,
        indent: str,
        prefix: str,
        is_editing_this: bool,
    ) -> List[Tuple[str, str]]:
        key_display = node.dict_key if node.dict_key else "<key>"
        val_display = str(node.value) if node.value else "<value>"

        # Find max key width among siblings to align = signs
        max_key_width = len(key_display)
        if node.parent:
            for sibling in node.parent.children:
                sib_key = sibling.dict_key if sibling.dict_key else "<key>"
                max_key_width = max(max_key_width, len(sib_key))

        row_parts: List[Tuple[str, str]] = [
            (style, f"{indent}{prefix}{node.key}: "),
        ]
        if is_editing_this and editing_dict_key[0]:
            row_parts.append(("class:selected", edit_buf[0].text))
            row_parts.append(("class:dim", "█"))
            row_parts.append(("", " " * max(0, max_key_width - len(edit_buf[0].text))))
        else:
            key_style = "class:dim" if not node.dict_key else style
            row_parts.append((key_style, key_display))
            row_parts.append(("", " " * (max_key_width - len(key_display))))
        row_parts.append((style, " = "))
        if is_editing_this and not editing_dict_key[0]:
            row_parts.append(("class:selected", edit_buf[0].text))
            row_parts.append(("class:dim", "█"))
        else:
            val_style = "class:dim" if not node.value else style
            row_parts.append((val_style, val_display))
        row_parts.append(("", "\n"))
        return row_parts

    def _render_leaf_row(
        node: ConfigFieldNode,
        style: str,
        indent: str,
        prefix: str,
        display_name: str,
        pad: str,
        comment_col: int,
        is_editing_this: bool,
    ) -> List[Tuple[str, str]]:
        is_list_entry = node.parent and node.parent.field_type == "list"

        if node.field_type == "bool":
            val_display = str(node.value).lower() if node.value is not None else "null"
            hints = "  (Enter to toggle)"
        elif is_list_entry:
            val_display = str(node.value) if node.value else "<value>"
            hints = ""
        else:
            if node.value is None and not node.required:
                val_display = "<null>"
            elif node.value is None:
                val_display = ""
            elif node.value == "":
                val_display = "<empty>"
            else:
                val_display = str(node.value)
            hints = ""

        label_prefix = f"{indent}{prefix}{display_name}:{pad} "

        row_parts: List[Tuple[str, str]] = [
            (style, label_prefix),
        ]

        # When editing this row, show the buffer contents with cursor
        if is_editing_this:
            buf = edit_buf[0]
            pos = buf.cursor_position
            text = buf.text
            row_parts.append(("class:selected", text[:pos]))
            row_parts.append(("class:dim", "█"))
            row_parts.append(("class:selected", text[pos:]))
            row_parts.append(("", "\n"))
            return row_parts

        is_placeholder = (is_list_entry and not node.value) or (node.value is None and not node.required) or node.value == ""
        val_style = "class:dim" if is_placeholder else style
        row_parts.append((val_style, val_display))

        if node.description and comment_col > 0:
            content_width = len(label_prefix) + len(val_display)
            padding = max(2, comment_col - content_width)
            row_parts.append(("class:dim", " " * padding + f"# {node.description}"))

        row_parts.append(("class:dim", hints))
        row_parts.append(("", "\n"))
        return row_parts

    def _render_row(
        node: ConfigFieldNode,
        selected: bool,
        comment_col: int,
        value_columns: Dict[Optional[int], int],
    ) -> List[Tuple[str, str]]:
        indent = "  " * (node.depth + 1)
        prefix = "> " if selected else "  "
        style = "class:selected" if selected else ""
        display_name = node.title if node.title else node.key
        pad = _value_pad(node, value_columns)

        if node.is_header:
            return _render_header_row(node, style, indent, prefix, display_name, pad, comment_col)

        row_idx = flat_rows.index(node) if node in flat_rows else -1
        is_editing_this = editing[0] and cursor[0] == row_idx

        if node.dict_key is not None:
            return _render_dict_child_row(node, style, indent, prefix, is_editing_this)

        return _render_leaf_row(node, style, indent, prefix, display_name, pad, comment_col, is_editing_this)

    def _get_display_text() -> List[Tuple[str, str]]:
        parts: List[Tuple[str, str]] = []
        parts.append(("class:header", f"  Configure: {toolset.name}\n"))
        parts.append(("class:dim", f"  Schema: {config_class.__name__}\n\n"))

        value_columns = _compute_value_columns()
        comment_col = _compute_comment_column(value_columns)
        for i, node in enumerate(flat_rows):
            parts.extend(_render_row(node, selected=(cursor[0] == i), comment_col=comment_col, value_columns=value_columns))

        # Separator
        parts.append(("", "\n"))

        # Buttons
        btn_start = len(flat_rows)
        btn_parts: List[Tuple[str, str]] = [("", "  ")]
        for bi, label in enumerate(_BUTTON_LABELS):
            idx = btn_start + bi
            if cursor[0] == idx:
                btn_parts.append(("class:button-selected", f" {label} "))
            else:
                btn_parts.append(("class:button", f" {label} "))
            btn_parts.append(("", "  "))
        parts.extend(btn_parts)
        parts.append(("", "\n"))

        # Status area
        if status_lines:
            parts.append(("", "\n"))
            parts.extend(status_lines)

        # Hint line
        parts.append(("class:hint", "\n  Up/Down: navigate | Enter: edit/select | Backspace/Del: delete entry or set null | Esc: cancel edit\n"))
        return parts

    # ── key bindings ──

    kb = KeyBindings()

    @kb.add("up", filter=not_editing)
    @kb.add("k", filter=not_editing)
    @kb.add("left", filter=not_editing)
    def _up(event: Any) -> None:
        cursor[0] = (cursor[0] - 1) % total_items()

    @kb.add("down", filter=not_editing)
    @kb.add("j", filter=not_editing)
    @kb.add("right", filter=not_editing)
    def _down(event: Any) -> None:
        cursor[0] = (cursor[0] + 1) % total_items()

    @kb.add("left", filter=~not_editing)
    def _edit_left(event: Any) -> None:
        buf = edit_buf[0]
        if buf.cursor_position > 0:
            buf.cursor_position -= 1

    @kb.add("right", filter=~not_editing)
    def _edit_right(event: Any) -> None:
        buf = edit_buf[0]
        if buf.cursor_position < len(buf.text):
            buf.cursor_position += 1

    @kb.add("up", filter=~not_editing)
    def _edit_up(event: Any) -> None:
        edit_buf[0].cursor_position = 0

    @kb.add("down", filter=~not_editing)
    def _edit_down(event: Any) -> None:
        buf = edit_buf[0]
        buf.cursor_position = len(buf.text)

    @kb.add("escape")
    def _escape(event: Any) -> None:
        if editing[0]:
            editing[0] = False
            editing_dict_key[0] = False
        # Don't exit the whole editor on Escape when not editing

    @kb.add("c-c")
    def _ctrl_c(event: Any) -> None:
        if editing[0]:
            editing[0] = False
            editing_dict_key[0] = False
        else:
            event.app.exit()

    @kb.add("c-d")
    @kb.add("delete")
    def _delete_entry(event: Any) -> None:
        idx = cursor[0]
        if idx >= len(flat_rows):
            return
        node = flat_rows[idx]

        if editing[0]:
            buf = edit_buf[0]
            is_collection_child = node.parent and node.parent.is_header and node.parent.field_type in ("dict", "list")
            if len(buf.text) == 0 and not node.required and not editing_dict_key[0] and not is_collection_child:
                # Empty buffer + deletion key → set to <null>
                node.value = None
                node.explicitly_set = True
                editing[0] = False
                status_lines.clear()
            else:
                # Forward-delete
                buf.delete()
            return

        if node.parent and node.parent.is_header and node.parent.field_type in ("dict", "list"):
            node.parent.children.remove(node)
            for i, child in enumerate(node.parent.children):
                child.key = str(i)
            _refresh_flat()
            if cursor[0] >= total_items():
                cursor[0] = max(0, total_items() - 1)
        elif not node.is_header and not node.required and node.value is not None:
            # Set optional leaf field to <null>
            node.value = None
            node.explicitly_set = True

    @kb.add("enter")
    def _enter(event: Any) -> None:
        nonlocal status_lines
        idx = cursor[0]

        # ── button press ──
        btn_start = len(flat_rows)
        if idx >= btn_start:
            btn_idx = idx - btn_start
            config_dict = tree_to_dict(top_nodes)

            if btn_idx == 0:  # Test
                ok, msg = run_config_test(toolset, config_dict)
                style_cls = "class:status-ok" if ok else "class:status-fail"
                status_lines = [(style_cls, f"  {line}\n") for line in msg.splitlines()]
            elif btn_idx == 1:  # Reset
                top_nodes.clear()
                top_nodes.extend(build_tree_from_schema(config_class, {}))
                _refresh_flat()
                cursor[0] = 0
                status_lines = [("class:status-ok", "  Configuration reset to defaults.\n")]
                return
            elif btn_idx == 2:  # Save
                config_path = Path(config_file_path) if config_file_path else Path(DEFAULT_CONFIG_LOCATION)
                ok, msg = save_config_to_file(config_path, toolset.name, config_dict)
                style_cls = "class:status-ok" if ok else "class:status-fail"
                status_lines = [(style_cls, f"  {line}\n") for line in msg.splitlines()]
                if ok:
                    saved[0] = True
            elif btn_idx == 3:  # Exit
                event.app.exit()
            return

        # ── tree node interaction ──
        node = flat_rows[idx]

        def _make_edit_buffer(text: str) -> Buffer:
            return Buffer(document=Document(text, len(text)))

        if editing[0]:
            raw = edit_buf[0].text

            # Dict child: confirm key, then move to editing value
            if node.dict_key is not None and editing_dict_key[0]:
                node.dict_key = raw
                editing_dict_key[0] = False
                initial_text = str(node.value) if node.value is not None else ""
                edit_buf[0] = _make_edit_buffer(initial_text)
                return

            # Confirm edit (value)
            if node.field_type == "int":
                try:
                    node.value = int(raw)
                except ValueError:
                    status_lines = [("class:status-fail", f"  Invalid integer: '{raw}'\n")]
                    editing[0] = False
                    editing_dict_key[0] = False
                    return
            elif node.field_type == "float":
                try:
                    node.value = float(raw)
                except ValueError:
                    status_lines = [("class:status-fail", f"  Invalid number: '{raw}'\n")]
                    editing[0] = False
                    editing_dict_key[0] = False
                    return
            else:
                node.value = raw
            node.explicitly_set = True
            editing[0] = False
            editing_dict_key[0] = False
            status_lines = []
            return

        # Bool toggle
        if node.field_type == "bool":
            node.value = not bool(node.value)
            return

        # Header: add entry
        if node.is_header:
            if node.field_type == "dict":
                _prompt_add_dict_entry(node, event)
                _refresh_flat()
                new_child = node.children[-1]
                cursor[0] = flat_rows.index(new_child)
                editing[0] = True
                editing_dict_key[0] = True
                edit_buf[0] = _make_edit_buffer(new_child.dict_key or "")
            elif node.field_type == "list":
                new_child = ConfigFieldNode(
                    key=str(len(node.children)),
                    field_type="str",
                    value="",
                    depth=node.depth + 1,
                    parent=node,
                )
                node.children.append(new_child)
                _refresh_flat()
                cursor[0] = flat_rows.index(new_child)
                editing[0] = True
                edit_buf[0] = _make_edit_buffer("")
            elif node.field_type == "model":
                pass  # Models are not directly "addable"
            return

        # Dict child: start editing key first
        if node.dict_key is not None:
            editing[0] = True
            editing_dict_key[0] = True
            edit_buf[0] = _make_edit_buffer(node.dict_key)
            return

        # Leaf: start inline editing
        editing[0] = True
        initial_text = str(node.value) if node.value is not None else ""
        edit_buf[0] = _make_edit_buffer(initial_text)

    # Handle typed characters when in editing mode
    @kb.add("<any>")
    def _char(event: Any) -> None:
        if not editing[0]:
            return
        char = event.data
        if len(char) != 1 or not char.isprintable():
            return

        idx = cursor[0]
        if idx >= len(flat_rows):
            return
        node = flat_rows[idx]

        # Numeric validation
        if node.field_type in ("int", "float"):
            allowed = set("0123456789")
            if node.field_type == "float":
                allowed.add(".")
            if char == "-" and edit_buf[0].cursor_position == 0:
                pass  # allow leading minus
            elif char not in allowed:
                return

        edit_buf[0].insert_text(char)

    @kb.add("backspace")
    def _backspace(event: Any) -> None:
        if editing[0]:
            buf = edit_buf[0]
            if len(buf.text) == 0:
                # Empty buffer + backspace → set to <null> if optional leaf field
                idx = cursor[0]
                if idx < len(flat_rows):
                    node = flat_rows[idx]
                    is_collection_child = node.parent and node.parent.is_header and node.parent.field_type in ("dict", "list")
                    if not node.required and not editing_dict_key[0] and not is_collection_child:
                        node.value = None
                        node.explicitly_set = True
                        editing[0] = False
                        status_lines.clear()
            else:
                buf.delete_before_cursor()
        else:
            _delete_entry(event)

    # ── run ──

    layout = Layout(
        Window(FormattedTextControl(_get_display_text, show_cursor=False), wrap_lines=True)
    )
    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        style=_MENU_STYLE,
        full_screen=False,
        erase_when_done=True,
    )
    app.run()
    return saved[0]


def _prompt_add_dict_entry(node: ConfigFieldNode, event: Any) -> None:
    """Add a new key-value child to a dict header node.

    Since we're inside a prompt_toolkit Application, we create an inline child
    with an index key and empty dict_key that the user can then edit.
    """
    idx = len(node.children)

    new_child = ConfigFieldNode(
        key=str(idx),
        field_type="str",
        value="",
        dict_key="",
        depth=node.depth + 1,
        parent=node,
    )
    node.children.append(new_child)


def _refresh_toolset_from_file(
    config_path: Path,
    toolset: Toolset,
    console: Console,
) -> None:
    """Re-read the saved config and refresh the toolset's status."""
    try:
        with open(config_path, "r") as f:
            file_data = yaml.safe_load(f) or {}
        saved_cfg = (
            file_data.get("toolsets", {}).get(toolset.name, {}).get("config", {})
        )
    except Exception as e:
        logger.warning("Could not re-read config file for refresh: %s", e)
        return

    toolset.config = saved_cfg
    toolset.enabled = True
    toolset.status = ToolsetStatusEnum.DISABLED
    toolset.error = None
    toolset.check_prerequisites(silent=True)

    if toolset.status == ToolsetStatusEnum.ENABLED:
        console.print(
            f"[bold green]Toolset '{toolset.name}' refreshed — enabled.[/bold green]"
        )
    else:
        console.print(
            f"[bold {ERROR_COLOR}]Toolset '{toolset.name}' refreshed — "
            f"{toolset.error or 'prerequisites not met'}.[/bold {ERROR_COLOR}]"
        )


# ── Main orchestrator ─────────────────────────────────────────────────


def run_toolset_config_tui(
    config: Config,
    config_file: Optional[Path],
    console: Console,
    preloaded_toolsets: Optional[List[Toolset]] = None,
) -> None:
    """Main entry point – runs the full 2-screen config flow."""
    if preloaded_toolsets is not None:
        toolsets = preloaded_toolsets
    else:
        toolsets = config.toolset_manager.list_console_toolsets()

    toolsets = [t for t in toolsets if t.config_classes and t.type != ToolsetType.MCP]
    selected = select_toolset(toolsets, console)
    if selected is _MCP_SELECTED_SENTINEL:
        console.print(
            f"[bold {STATUS_COLOR}]Opened MCP Documentation: "
            f"[link={_MCP_SERVER_DOCS_URL}]{_MCP_SERVER_DOCS_URL}[/link][/bold {STATUS_COLOR}]"
        )
        return
    if selected is None:
        console.print(f"[bold {STATUS_COLOR}]No toolset selected.[/bold {STATUS_COLOR}]")
        return

    initial = _get_existing_config(selected, config)

    config_path = Path(config_file) if config_file else Path(DEFAULT_CONFIG_LOCATION)
    saved = run_tree_editor(selected, initial, config_path)

    if saved:
        _refresh_toolset_from_file(config_path, selected, console)
        # Update in-memory config so subsequent edits see the saved values
        if config.toolsets is None:
            config.toolsets = {}
        set_toolset_config(config.toolsets, selected.name, selected.config)

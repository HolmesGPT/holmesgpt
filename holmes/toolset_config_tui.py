"""Interactive TUI for configuring toolsets.

Entry points:
  - CLI:         ``holmes toolset config``
  - Interactive:  ``/config`` slash command
"""

import copy
import io
import logging
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type, Union, get_args, get_origin

import yaml  # type: ignore
from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style as PTStyle
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel

from holmes.config import DEFAULT_CONFIG_LOCATION, Config
from holmes.core.tools import Toolset, ToolsetStatusEnum

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


def _extract_base_model_subclass(annotation: Any) -> Optional[Type[BaseModel]]:
    """Best-effort extraction of a BaseModel subclass from a type annotation."""
    if annotation is None:
        return None
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        if args:
            return _extract_base_model_subclass(args[0])
    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]  # noqa: E721
        if len(args) == 1:
            return _extract_base_model_subclass(args[0])
        return None
    try:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
    except Exception:
        return None
    return None


def _resolve_primitive_type(annotation: Any) -> str:
    """Map a Python type annotation to a simple type tag."""
    if annotation is None:
        return "str"

    origin = get_origin(annotation)

    # Unwrap Optional / Union[X, None]
    if origin is Union:
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

        # Current value
        cur = current_values.get(field_name)

        # Default fallback
        if cur is None:
            try:
                from pydantic_core import PydanticUndefined  # type: ignore
            except Exception:  # pragma: no cover
                PydanticUndefined = object()  # type: ignore
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
                for k, v in cur.items():
                    child = ConfigFieldNode(
                        key=k,
                        field_type="str",
                        value=v,
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
                result[node.key] = {c.key: c.value for c in node.children}
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
            if node.value is not None:
                result[node.key] = node.value
    return result


# ── Config file save / merge ─────────────────────────────────────────


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
    if toolset_name not in existing["toolsets"] or not isinstance(
        existing["toolsets"][toolset_name], dict
    ):
        existing["toolsets"][toolset_name] = {}

    existing["toolsets"][toolset_name]["enabled"] = True
    existing["toolsets"][toolset_name]["config"] = config_dict

    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w") as f:
            yaml.dump(existing, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        return False, f"Failed to write {config_file}: {e}"

    return True, f"Configuration saved to {config_file}"


def export_config_yaml(toolset_name: str, config_dict: Dict[str, Any]) -> str:
    """Return a prettified YAML snippet for the toolset config."""
    snippet = {
        "toolsets": {
            toolset_name: {
                "enabled": True,
                "config": config_dict,
            }
        }
    }
    return yaml.dump(snippet, default_flow_style=False, sort_keys=False)


def run_config_test(toolset: Toolset, config_dict: Dict[str, Any]) -> Tuple[bool, str]:
    """Run prerequisite checks against *config_dict* and return (ok, message).

    All stdout/stderr/logging output is captured so it doesn't leak into the TUI.
    The captured output is appended to the returned message.
    """
    test_toolset = copy.deepcopy(toolset)
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
    root_logger.addHandler(log_handler)

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            test_toolset.check_prerequisites(silent=True)
    finally:
        root_logger.removeHandler(log_handler)

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
    def _up(event: Any) -> None:
        selected[0] = (selected[0] - 1) % len(items)

    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None:
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
    )
    app.run()
    return result[0]


# ── Screen 1: select toolset ─────────────────────────────────────────


def _get_configurable_toolsets(config: Config) -> List[Toolset]:
    """Return toolsets that have at least one config_class."""
    all_toolsets = config.toolset_manager.list_console_toolsets()
    return [t for t in all_toolsets if t.config_classes]


def select_toolset(toolsets: List[Toolset], console: Console) -> Optional[Toolset]:
    """Screen 1 – let the user pick a toolset to configure."""
    if not toolsets:
        console.print(
            f"[bold {ERROR_COLOR}]No configurable toolsets found.[/bold {ERROR_COLOR}]"
        )
        return None

    items: List[str] = []
    for t in toolsets:
        status_tag = t.status.value if t.status else "disabled"
        has_config = "configured" if t.config else "unconfigured"
        items.append(f"{t.name:<35} [{status_tag}] ({has_config})")

    idx = _run_selection_menu(
        items,
        title="Select a toolset to configure",
        hint="Up/Down to navigate, Enter to select, Esc to cancel",
    )
    if idx is None:
        return None
    return toolsets[idx]


# ── Screen 2: add / edit decision ────────────────────────────────────


def ask_edit_or_fresh(
    toolset: Toolset,
    config: Config,
    console: Console,
) -> Optional[Dict[str, Any]]:
    """Screen 2 – decide whether to edit existing config or start fresh.

    Returns the initial values dict, or None to cancel.
    """
    # Check if there is existing config in the loaded Config object
    existing_config: Dict[str, Any] = {}
    if config.toolsets and toolset.name in config.toolsets:
        ts_entry = config.toolsets[toolset.name]
        if isinstance(ts_entry, dict) and ts_entry.get("config"):
            existing_config = ts_entry["config"]

    if not existing_config:
        # No existing config → straight to fresh
        return {}

    idx = _run_selection_menu(
        [
            "Edit current configuration",
            "Start with fresh configuration",
            "Cancel",
        ],
        title=f"Toolset '{toolset.name}' already has a configuration",
    )
    if idx is None or idx == 2:
        return None
    if idx == 0:
        return dict(existing_config)
    return {}


# ── Screen 3: tree editor ────────────────────────────────────────────

_BUTTON_LABELS = ["[ Test ]", "[ Export ]", "[ Save ]", "[ Exit ]"]


def run_tree_editor(
    toolset: Toolset,
    initial_config: Dict[str, Any],
    config_file_path: Path,
) -> None:
    """Screen 3 – full tree editor with inline editing and action buttons."""

    config_class: Type[BaseModel] = toolset.config_classes[0]
    top_nodes = build_tree_from_schema(config_class, initial_config)
    flat_rows = _flatten_tree(top_nodes)

    # State
    cursor = [0]  # index into (flat_rows + buttons)
    editing = [False]
    edit_buf = [Buffer()]
    status_lines: List[Tuple[str, str]] = []

    total_items = lambda: len(flat_rows) + len(_BUTTON_LABELS)  # noqa: E731

    def _refresh_flat() -> None:
        nonlocal flat_rows
        flat_rows = _flatten_tree(top_nodes)

    # ── rendering ──

    def _render_row(node: ConfigFieldNode, selected: bool) -> List[Tuple[str, str]]:
        indent = "  " * (node.depth + 1)
        prefix = "> " if selected else "  "
        style = "class:selected" if selected else ""

        if node.is_header:
            count = len(node.children)
            type_bracket = "{}" if node.field_type == "dict" else "[]"
            label = f"{indent}{prefix}{node.key}: {type_bracket[0]}{count} items{type_bracket[1]}"
            hints = "  (Enter to add entry)"
            return [(style, label), ("class:dim", hints), ("", "\n")]

        if node.field_type == "bool":
            val_display = str(node.value).lower() if node.value is not None else "null"
            hints = "  (Enter to toggle)"
        else:
            val_display = str(node.value) if node.value is not None else ""
            hints = ""

        desc = f"  # {node.description}" if node.description else ""

        row_parts: List[Tuple[str, str]] = [
            (style, f"{indent}{prefix}{node.key}: "),
        ]

        # When editing this row, show the buffer contents
        row_idx = flat_rows.index(node) if node in flat_rows else -1
        if editing[0] and cursor[0] == row_idx:
            row_parts.append(("class:selected", edit_buf[0].text))
            row_parts.append(("class:dim", "█"))
        else:
            row_parts.append((style, val_display))

        row_parts.append(("class:dim", desc))
        row_parts.append(("class:dim", hints))
        row_parts.append(("", "\n"))
        return row_parts

    def _get_display_text() -> List[Tuple[str, str]]:
        parts: List[Tuple[str, str]] = []
        parts.append(("class:header", f"  Configure: {toolset.name}\n"))
        parts.append(("class:dim", f"  Schema: {config_class.__name__}\n\n"))

        for i, node in enumerate(flat_rows):
            parts.extend(_render_row(node, selected=(cursor[0] == i)))

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
        parts.append(("class:hint", "\n  Up/Down: navigate | Enter: edit/select | Ctrl+D: delete entry | Esc: cancel edit\n"))
        return parts

    # ── key bindings ──

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None:
        if editing[0]:
            return
        cursor[0] = (cursor[0] - 1) % total_items()

    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None:
        if editing[0]:
            return
        cursor[0] = (cursor[0] + 1) % total_items()

    @kb.add("escape")
    def _escape(event: Any) -> None:
        if editing[0]:
            editing[0] = False
        # Don't exit the whole editor on Escape when not editing

    @kb.add("c-c")
    def _ctrl_c(event: Any) -> None:
        if editing[0]:
            editing[0] = False
        else:
            event.app.exit()

    @kb.add("c-d")
    @kb.add("delete")
    def _delete_entry(event: Any) -> None:
        if editing[0]:
            return
        idx = cursor[0]
        if idx >= len(flat_rows):
            return
        node = flat_rows[idx]
        if node.parent and node.parent.is_header and node.parent.field_type in ("dict", "list"):
            node.parent.children.remove(node)
            _refresh_flat()
            if cursor[0] >= total_items():
                cursor[0] = max(0, total_items() - 1)

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
            elif btn_idx == 1:  # Export
                yml = export_config_yaml(toolset.name, config_dict)
                status_lines = [("", f"  {line}\n") for line in yml.splitlines()]
            elif btn_idx == 2:  # Save
                config_path = Path(config_file_path) if config_file_path else Path(DEFAULT_CONFIG_LOCATION)
                ok, msg = save_config_to_file(config_path, toolset.name, config_dict)
                style_cls = "class:status-ok" if ok else "class:status-fail"
                status_lines = [(style_cls, f"  {line}\n") for line in msg.splitlines()]
            elif btn_idx == 3:  # Exit
                event.app.exit()
            return

        # ── tree node interaction ──
        node = flat_rows[idx]

        if editing[0]:
            # Confirm edit
            raw = edit_buf[0].text
            if node.field_type == "int":
                try:
                    node.value = int(raw)
                except ValueError:
                    status_lines = [("class:status-fail", f"  Invalid integer: '{raw}'\n")]
                    editing[0] = False
                    return
            elif node.field_type == "float":
                try:
                    node.value = float(raw)
                except ValueError:
                    status_lines = [("class:status-fail", f"  Invalid number: '{raw}'\n")]
                    editing[0] = False
                    return
            else:
                node.value = raw if raw else None
            editing[0] = False
            status_lines = []
            return

        # Bool toggle
        if node.field_type == "bool":
            node.value = not bool(node.value)
            return

        # Header: add entry
        if node.is_header:
            if node.field_type == "dict":
                # Use a simple prompt to get the key
                _prompt_add_dict_entry(node, event)
                _refresh_flat()
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
            elif node.field_type == "model":
                pass  # Models are not directly "addable"
            return

        # Leaf: start inline editing
        editing[0] = True
        initial_text = str(node.value) if node.value is not None else ""
        edit_buf[0] = Buffer(document=__import__("prompt_toolkit.document", fromlist=["Document"]).Document(initial_text, len(initial_text)))

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
        if not editing[0]:
            return
        edit_buf[0].delete_before_cursor()

    # ── run ──

    layout = Layout(
        Window(FormattedTextControl(_get_display_text, show_cursor=False), wrap_lines=True)
    )
    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        style=_MENU_STYLE,
        full_screen=False,
    )
    app.run()


def _prompt_add_dict_entry(node: ConfigFieldNode, event: Any) -> None:
    """Add a new key-value child to a dict header node.

    Since we're inside a prompt_toolkit Application, we create an inline child
    with a placeholder key that the user can then edit.
    """
    existing_keys = {c.key for c in node.children}
    key_num = len(node.children)
    new_key = f"key_{key_num}"
    while new_key in existing_keys:
        key_num += 1
        new_key = f"key_{key_num}"

    new_child = ConfigFieldNode(
        key=new_key,
        field_type="str",
        value="",
        depth=node.depth + 1,
        parent=node,
    )
    node.children.append(new_child)


# ── Main orchestrator ─────────────────────────────────────────────────


def run_toolset_config_tui(
    config: Config,
    config_file: Optional[Path],
    console: Console,
) -> None:
    """Main entry point – runs the full 3-screen config flow."""
    toolsets = _get_configurable_toolsets(config)

    selected = select_toolset(toolsets, console)
    if selected is None:
        console.print(f"[bold {STATUS_COLOR}]No toolset selected.[/bold {STATUS_COLOR}]")
        return

    initial = ask_edit_or_fresh(selected, config, console)
    if initial is None:
        console.print(f"[bold {STATUS_COLOR}]Cancelled.[/bold {STATUS_COLOR}]")
        return

    config_path = Path(config_file) if config_file else Path(DEFAULT_CONFIG_LOCATION)
    run_tree_editor(selected, initial, config_path)

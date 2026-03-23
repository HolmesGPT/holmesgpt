import contextvars
import logging
import math
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from enum import Enum
from io import StringIO
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Optional

try:
    import select as select_module
    import termios
    import tty

    _HAS_TERMINAL_CONTROL = True
except ImportError:
    _HAS_TERMINAL_CONTROL = False

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.completion import Completer, Completion, merge_completers
from prompt_toolkit.completion.filesystem import ExecutableCompleter, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.shortcuts.prompt import CompleteStyle
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from pygments.lexers import guess_lexer
from rich.console import Console, Group
from rich.control import Control
from rich.live import Live
from rich.markdown import Markdown, Panel
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from holmes.config import Config
from holmes.core.config import config_path_dir
from holmes.core.init_event import StatusEvent, StatusEventKind, ToolsetStatus
from holmes.core.feedback import (
    PRIVACY_NOTICE_BANNER,
    Feedback,
    FeedbackCallback,
    UserFeedback,
)
from holmes.core.prompt import PromptComponent, build_initial_ask_messages
from holmes.core.models import PendingToolApproval
from holmes.core.tool_calling_llm import (
    ApprovalCallback,
    LLMInterruptedError,
    LLMResult,
    ToolCallingLLM,
    ToolCallResult,
    extract_bash_session_prefixes,
)
from holmes.core.llm_usage import RequestStats
from holmes.core.models import ToolApprovalDecision
from holmes.utils.stream import StreamEvents, StreamMessage
from holmes.core.tools import pretty_print_toolset_status
from holmes.core.tracing import DummyTracer
from holmes.plugins.toolsets.bash.common.cli_prefixes import (
    enable_cli_mode,
)
from holmes.plugins.toolsets.bash.common.cli_prefixes import (
    save_cli_bash_tools_approved_prefixes as _save_approved_prefixes,
)
from holmes.utils.colors import (
    AI_COLOR,
    ERROR_COLOR,
    HELP_COLOR,
    STATUS_COLOR,
    TOOLS_COLOR,
    USER_COLOR,
)
from holmes.toolset_config_tui import run_toolset_config_tui
from holmes.utils.console.consts import agent_name
from holmes.utils.file_utils import write_json_file
from holmes.version import check_version_async

# Display loggers that are silenced in interactive mode.
# The interactive loop renders from stream events instead of these loggers.
DISPLAY_LOGGER_NAMES = [
    "holmes.display.tool_calling_llm",
    "holmes.display.tools",
    "holmes.display.toolset_manager",
    "holmes.display.core_investigation",
    "holmes.display.config",
    "holmes.display.llm",
    "holmes.display.toolset_utils",
    "holmes.display.bash_toolset",
    "holmes.display.mcp_toolset",
    "holmes.display.tool_executor",
]

_SENTINEL = object()  # marks end of stream on the queue


def silence_display_loggers():
    """Silence display loggers so interactive mode can render from stream events."""
    for name in DISPLAY_LOGGER_NAMES:
        logging.getLogger(name).setLevel(logging.WARNING + 1)


def restore_display_loggers():
    """Restore display loggers to default level."""
    for name in DISPLAY_LOGGER_NAMES:
        logging.getLogger(name).setLevel(logging.NOTSET)


_SLOW_THRESHOLD_SECS = 1.0  # Show a toolset by name if it takes longer than this


def _make_live(renderable: Any, **kwargs: Any) -> Any:
    """Create a Rich Live instance with a workaround for the ghost-frame bug.

    Rich 13.9.4 bug: ``Live.refresh()`` calls ``console.print(Control())``
    which uses the default ``end="\\n"``.  This trailing newline is not
    accounted for in ``LiveRender._shape``, so ``position_cursor()`` (which
    does ``height - 1`` cursor-ups) under-erases by one line when the
    terminal has room below the display.  Each frame leaks one ghost line.

    Fix: subclass ``Live`` and override ``refresh()`` to pass ``end=""``,
    eliminating the spurious trailing newline.  With ``end=""``, the cursor
    stays on the last content line and ``height - 1`` cursor-ups correctly
    reaches line 1.
    """

    class _FixedLive(Live):
        def refresh(self) -> None:
            with self._lock:
                self._live_render.set_renderable(self.renderable)
                if self.console.is_terminal and not self.console.is_dumb_terminal:
                    with self.console:
                        self.console.print(Control(), end="")
                elif not self._started and not self.transient:
                    with self.console:
                        self.console.print(Control(), end="")

    return _FixedLive(renderable, **kwargs)


class InitProgressRenderer:
    """Collects StatusEvents and renders live progress during initialization.

    Uses Rich Live(transient=True) so the detailed progress vanishes when done,
    leaving only a compact summary (plus any errors).

    Thread-safe: events arrive from the ThreadPoolExecutor in check_toolset_prerequisites.
    """

    def __init__(self, console: Console, model_name: str = ""):
        self._console = console
        self._lock = threading.Lock()
        self._toolsets_ok: List[str] = []
        self._toolsets_failed: List[tuple[str, str]] = []  # (name, error)
        self._in_flight: Dict[str, float] = {}  # name → start time
        self._model_message: str = ""
        self._phase: str = "Loading datasources"
        self._start_time: float = 0.0
        self._live: Optional[Any] = None  # Rich Live
        self._timer_stop = threading.Event()

    def _build_display(self) -> "Text":
        """Build the Rich renderable for the current state."""


        now = time.time()
        elapsed = now - self._start_time
        ok = len(self._toolsets_ok)
        failed = len(self._toolsets_failed)
        checked = ok + failed
        in_flight = len(self._in_flight)

        display = Text()

        frame = _SPINNER_FRAMES[int(now * 8) % len(_SPINNER_FRAMES)]
        display.append(f"  {frame} ", style="bold")
        display.append(f"{self._phase}", style="bold")
        display.append(f"  {checked} ready", style="dim")
        if in_flight:
            display.append(f", {in_flight} checking", style="dim")
        display.append(f"  ({elapsed:.1f}s)", style="dim")

        # Show recently completed toolset names (last 4, with "+N more" suffix)
        if self._toolsets_ok:
            max_show = 4
            recent = self._toolsets_ok[-max_show:]
            names = ", ".join(recent)
            remaining = ok - len(recent)
            if remaining > 0:
                names += f" and {remaining} more"
            display.append("\n  ")
            display.append(f"  ready: {names}", style="green")

        # Show in-flight toolsets that are taking more than 1 second
        slow: List[tuple[str, float]] = []
        for name, started_at in self._in_flight.items():
            duration = now - started_at
            if duration >= _SLOW_THRESHOLD_SECS:
                slow.append((name, duration))
        if slow:
            slow.sort(key=lambda x: -x[1])  # longest first
            parts = [f"{name} ({dur:.0f}s)" for name, dur in slow]
            display.append("\n  ")
            display.append(f"  checking: {', '.join(parts)}", style="yellow")

        if self._toolsets_failed:
            failed_names = ", ".join(name for name, _ in self._toolsets_failed[-4:])
            display.append("\n  ")
            display.append(f"  failed: {failed_names}", style="red dim")

        # Show model after datasources
        if self._model_message:
            paren = self._model_message.find("(")
            if paren > 0:
                display.append(f"\n  {self._model_message[:paren].rstrip()}", style="bold")
                display.append(f" {self._model_message[paren:]}", style="dim")
            else:
                display.append(f"\n  {self._model_message}", style="bold")

        return display

    def on_event(self, event: StatusEvent) -> None:
        """Callback passed as on_event to create_console_toolcalling_llm."""
        with self._lock:
            if event.kind == StatusEventKind.TOOLSET_CHECKING:
                self._in_flight[event.name] = time.time()
            elif event.kind == StatusEventKind.TOOLSET_READY:
                self._in_flight.pop(event.name, None)
                if event.status == ToolsetStatus.ENABLED:
                    self._toolsets_ok.append(event.name)
                else:
                    self._toolsets_failed.append((event.name, event.error))
            elif event.kind == StatusEventKind.TOOLSET_LAZY:
                if event.status == ToolsetStatus.ENABLED:
                    self._toolsets_ok.append(event.name)
                else:
                    self._toolsets_failed.append((event.name, event.error))
            elif event.kind == StatusEventKind.REFRESHING:
                self._phase = "Refreshing datasources"
            elif event.kind == StatusEventKind.MODEL_LOADED:
                self._model_message = event.message
            elif event.kind == StatusEventKind.DATASOURCE_COUNT:
                pass  # We compute our own count

            if self._live is not None:
                self._live.update(self._build_display())

    def _tick(self) -> None:
        """Background timer that updates the display for smooth spinner animation."""
        while not self._timer_stop.wait(0.15):
            with self._lock:
                if self._live is not None:
                    self._live.update(self._build_display())

    def start(self) -> None:
        """Start the live display. Call before create_console_toolcalling_llm."""
        self._start_time = time.time()
        self._live = _make_live(
            self._build_display(),
            console=self._console,
            transient=True,
            refresh_per_second=4,
        )
        self._live.start()
        # Background thread to update elapsed time every second
        self._timer_stop.clear()
        timer_thread = threading.Thread(target=self._tick, daemon=True)
        timer_thread.start()

    def stop(self) -> None:
        """Stop the live display and print a compact summary."""
        self._timer_stop.set()
        if self._live is not None:
            self._live.stop()
            self._live = None

        ok = len(self._toolsets_ok)
        failed = len(self._toolsets_failed)
        elapsed = time.time() - self._start_time

        parts = []
        parts.append(f"[bold green]{ok}[/bold green] datasources loaded")
        if failed:
            parts.append(f"[bold red]{failed} failed[/bold red]")
        parts.append(f"[dim]{elapsed:.1f}s[/dim]")
        self._console.print(" | ".join(parts))

        # Show failed toolsets with their error messages
        if self._toolsets_failed:
            for name, error in self._toolsets_failed:
                err_suffix = f": {error}" if error else ""
                self._console.print(f"  [red]✗ {name}{err_suffix}[/red]")

        # Model info after datasources
        if self._model_message:
            self._console.print(format_model_info_rich(self._model_message))
        self._console.rule(style="dim")


def format_model_info_rich(model_message: str) -> str:
    """Return a Rich-formatted model info string with dimmed source hint."""
    paren = model_message.find("(")
    if paren > 0:
        main = model_message[:paren].rstrip()
        hint = model_message[paren:]
        return f"[bold]{main}[/bold] [dim]{hint}[/dim]"
    return f"[bold]{model_message}[/bold]"


_TODO_WRITE_TOOL_NAME = "TodoWrite"

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _task_spinner_frame() -> str:
    """Return the current braille spinner frame based on wall-clock time."""
    return _SPINNER_FRAMES[int(time.time() * 8) % len(_SPINNER_FRAMES)]


def _chars_to_tokens(n: int) -> int:
    """Approximate token count from character count (~4 chars per token)."""
    return max(1, n // 4) if n > 0 else 0


def _format_size(n: int) -> str:
    """Format a char count as an approximate token count string."""
    tokens = _chars_to_tokens(n)
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M tokens"
    if tokens >= 10_000:
        return f"{tokens // 1000}K tokens"
    if tokens >= 1_000:
        return f"{tokens:,} tokens"
    return f"{tokens} tokens"


def _size_bar(output_len: int, max_width: int = 12) -> str:
    """Build a proportional bar representing data volume.

    Uses a log scale so small results still get a visible bar.
    Returns a string like '▰▰▰▰ 39K' — no empty blocks.
    """
    if not output_len or output_len <= 0:
        return ""
    # log scale: 100 tokens → 1 block, 100K → max blocks
    tokens = _chars_to_tokens(output_len)
    filled = min(max_width, max(1, int(math.log10(max(tokens, 1)) * (max_width / 5))))
    size_str = _format_size(output_len)
    return f"{'▰' * filled} {size_str}"


def _build_task_panel(tasks: list) -> Panel:
    """Build a Rich Panel showing the task list with checkbox-style icons."""
    completed = sum(1 for t in tasks if t.get("status") == "completed")
    total = len(tasks)

    content = Text()
    for i, task in enumerate(tasks):
        status = task.get("status", "pending")
        task_content = task.get("content", "")

        if status == "completed":
            content.append(" ☑ ", style="green")
            content.append(task_content, style="dim strike")
        elif status == "in_progress":
            content.append(" ☐ ", style="bold yellow")
            content.append(task_content, style="bold yellow")
        elif status == "failed":
            content.append(" ☒ ", style="bold red")
            content.append(task_content, style="red")
        else:
            content.append(" ☐ ", style="dim")
            content.append(task_content, style="dim")
        if i < len(tasks) - 1:
            content.append("\n")

    # Title with progress
    title = f"[bold]Tasks[/bold] [dim]{completed}/{total}[/dim]"

    return Panel(
        content,
        title=title,
        title_align="left",
        border_style="blue",
        padding=(0, 1),
    )


class _LiveLogFilter(logging.Filter):
    """Captures log records into a buffer instead of letting them through.

    During Rich Live display, log messages from separate Console instances
    break transient rendering — causing duplicate frames and garbled output.
    This filter intercepts records so they can be replayed after Live stops.
    """

    def __init__(self, buffer: List[logging.LogRecord]):
        super().__init__()
        self._buffer = buffer

    def filter(self, record: logging.LogRecord) -> bool:
        self._buffer.append(record)
        return False  # Suppress — will be replayed later


class AgenticProgressRenderer:
    """Renders tool-calling progress using Rich Live.

    Starts with a "Thinking..." spinner. When tools begin executing, transitions
    to show running tools with elapsed times. Completed tools are printed
    permanently with status icons. The Live display is transient — only in-flight
    state is shown live; completed state is printed and persists.

    TodoWrite results render as a bordered "Tasks" panel, visually separate from
    tool execution lines. The panel only reprints when task statuses change.

    AI messages are printed immediately (outside the Live region).
    """

    _DATA_PANE_LINES = 14  # Visible lines in the data pane
    _DATA_LINE_MAX = 200  # Max chars per line (dynamically clamped to pane width)
    _DATA_BUFFER_MAX = 2000  # Max raw lines kept in buffer
    _SCROLL_SPEED = 3  # Lines to advance per tick when idle-scrolling history

    def __init__(self, console: Console, tool_number_offset: int, escape_hint: str = ""):
        self._console = console
        self._tool_number_offset = tool_number_offset
        self._escape_hint = escape_hint
        self._lock = threading.Lock()

        # Pending completed tools to process
        self._completed: List[tuple] = []
        # In-flight tools: tool_number → (name, start_time)
        self._in_flight: Dict[int, tuple] = {}
        self._next_tool_number = tool_number_offset + 1

        self._thinking = True  # True until first tool starts or AI message arrives
        self._start_time = time.time()

        self._live: Optional[Any] = None  # Rich Live
        self._timer_stop = threading.Event()

        # Completed tool history for left pane: (name, elapsed, output_len, is_error)
        self._tool_history: List[tuple] = []

        # Data feed: all raw output lines for the scrolling right pane
        self._data_lines: List[str] = []
        self._scroll_offset = 0  # Current scroll position
        self._scroll_pause = 0  # Ticks to pause before advancing
        self._follow_tail = True  # When True, snap to end of data
        self._total_bytes = 0  # Total bytes processed
        self._total_queries = 0  # Total tool calls completed

        # Latest tasks for live display
        self._live_tasks: Optional[list] = None
        self._summary_printed = False

        # Approval state: when True, everything dims and scrolling stops
        self._approval_pending = False
        self._pending_approval_descriptions: List[str] = []

        # Log buffering: capture log messages during Live display to prevent
        # them from breaking the transient rendering (duplicate frames, garbled output)
        self._log_buffer: List[logging.LogRecord] = []
        self._log_filter = _LiveLogFilter(self._log_buffer)

    # Sentinel prefix for tool header lines in the data buffer
    _TOOL_HEADER_PREFIX = "\x00TOOL:"

    def _data_line_max(self) -> int:
        """Dynamic max chars per line based on actual data pane width."""
        try:
            tw = self._console.width
            if not isinstance(tw, (int, float)) or tw <= 0:
                tw = 120
        except (TypeError, ValueError, AttributeError):
            tw = 120
        # 50/50 split; data pane gets half minus borders/padding (~7 chars)
        left_width = (int(tw) - 3) // 2
        data_width = int(tw) - left_width - 7
        return max(40, min(data_width, self._DATA_LINE_MAX))

    def _ingest_output(self, tool_name: str, output: str, description: str = "") -> None:
        """Ingest raw tool output into the scrolling data buffer."""
        # Insert a header line so the data pane shows which tool produced this output
        header = description if description else tool_name
        self._data_lines.append(f"{self._TOOL_HEADER_PREFIX}{header}")

        if not output:
            self._data_lines.append("\x00EMPTY")
            self._total_queries += 1
            self._follow_tail = True
            return
        self._total_bytes += len(output)
        self._total_queries += 1

        line_max = self._data_line_max()
        lines = output.splitlines()
        for line in lines:
            line = line.rstrip()
            if not line:
                continue
            if len(line) > line_max:
                line = line[: line_max - 1] + "…"
            self._data_lines.append(line)

        # Jump to tail so new data is immediately visible
        self._follow_tail = True

        # Trim buffer if too large
        if len(self._data_lines) > self._DATA_BUFFER_MAX:
            overflow = len(self._data_lines) - self._DATA_BUFFER_MAX
            self._data_lines = self._data_lines[overflow:]
            self._scroll_offset = max(0, self._scroll_offset - overflow)

    def _build_data_pane(self) -> "Text":
        """Build the scrolling data feed pane."""


        pane = Text(no_wrap=True, overflow="ellipsis")

        if not self._data_lines:
            pane.append("  Waiting for data…", style="dim italic")
            return pane

        total = len(self._data_lines)
        visible = self._DATA_PANE_LINES

        # Clamp scroll to valid range — no wrapping, stops at the end
        max_start = max(0, total - visible)
        start = min(self._scroll_offset, max_start)
        end = min(start + visible, total)

        # Width of line number gutter based on total lines
        gutter_w = len(str(total))

        # Find the most recent tool header at or before the scroll position
        # and pin it at the top so the user always knows which tool's output they're viewing
        pinned_header = None
        pinned_header_idx = -1
        for scan_idx in range(start, -1, -1):
            if self._data_lines[scan_idx].startswith(self._TOOL_HEADER_PREFIX):
                pinned_header = self._data_lines[scan_idx][len(self._TOOL_HEADER_PREFIX):]
                pinned_header_idx = scan_idx
                break

        rows_rendered = 0
        if pinned_header:
            pane.append(f" {'':>{gutter_w}} ", style="dim")
            pane.append(pinned_header, style=f"bold {TOOLS_COLOR}")
            pane.append("\n")
            rows_rendered += 1

        for i, idx in enumerate(range(start, end)):
            if rows_rendered >= visible:
                break
            line = self._data_lines[idx]

            # Skip header lines that duplicate the pinned header
            if line.startswith(self._TOOL_HEADER_PREFIX):
                if idx == pinned_header_idx:
                    # This is the same header we already pinned — skip it
                    continue
                # New tool section header
                pinned_header = line[len(self._TOOL_HEADER_PREFIX):]
                pinned_header_idx = idx
                pane.append(f" {'':>{gutter_w}} ", style="dim")
                pane.append(pinned_header, style=f"bold {TOOLS_COLOR}")
                pane.append("\n")
                rows_rendered += 1
                continue

            # Render empty marker with visible style
            if line == "\x00EMPTY":
                pane.append(f" {'':>{gutter_w}} ", style="dim")
                pane.append("  ✗ no output", style="italic red")
                rows_rendered += 1
                if rows_rendered < visible:
                    pane.append("\n")
                continue

            # Edge fade: dim bottom 2 lines to hint at more content below
            remaining = visible - rows_rendered
            if remaining <= 2:
                style = "dim"
            else:
                style = ""

            pane.append(f" {idx + 1:>{gutter_w}} ", style="dim")
            pane.append(line, style=style)
            rows_rendered += 1
            if rows_rendered < visible:
                pane.append("\n")

        return pane

    def _build_stats_line(self) -> str:
        """Build the stats string for the data pane title."""
        if self._total_bytes == 0:
            return ""
        tokens = _chars_to_tokens(self._total_bytes)
        if tokens >= 1_000_000:
            size = f"{tokens / 1_000_000:.1f}M tokens"
        elif tokens >= 1_000:
            size = f"{tokens / 1_000:.1f}K tokens"
        else:
            size = f"{tokens} tokens"
        return f" [dim]{size} across {self._total_queries} queries[/dim]"

    def _build_left_pane(self, show_analyzing: bool = False) -> Any:
        """Build the left-side status pane with separate tasks and tools sections."""


        now = time.time()
        sections = []

        # --- Tasks section ---
        if self._live_tasks:
            tasks_text = Text()
            completed = sum(1 for t in self._live_tasks if t.get("status") == "completed")
            total = len(self._live_tasks)
            for task in self._live_tasks:
                status = task.get("status", "pending")
                tc = task.get("content", "")
                if self._approval_pending:
                    # All tasks dim when waiting for approval
                    icon = " ☑ " if status == "completed" else " ☒ " if status == "failed" else " ☐ "
                    tasks_text.append(icon, style="dim")
                    tasks_text.append(tc, style="dim")
                elif status == "completed":
                    tasks_text.append(" ☑ ", style="green")
                    tasks_text.append(tc, style="dim strike")
                elif status == "in_progress":
                    tasks_text.append(" ☐ ", style="bold yellow")
                    tasks_text.append(tc, style="bold yellow")
                elif status == "failed":
                    tasks_text.append(" ☒ ", style="bold red")
                    tasks_text.append(tc, style="red")
                else:
                    tasks_text.append(" ☐ ", style="dim")
                    tasks_text.append(tc, style="dim")
                tasks_text.append("\n")
            # Remove trailing newline
            if tasks_text.plain.endswith("\n"):
                tasks_text.right_crop(1)
            task_border = "dim" if self._approval_pending else "blue"
            task_title = f"[dim]Tasks {completed}/{total}[/dim]" if self._approval_pending else f"[bold]Tasks[/bold] [dim]{completed}/{total}[/dim]"
            sections.append(
                Panel(tasks_text, title=task_title,
                      title_align="left", border_style=task_border, padding=(0, 1))
            )

        # --- Tools section ---
        has_tools = self._tool_history or self._in_flight
        if has_tools:
            tools_text = Text(no_wrap=True, overflow="ellipsis")
            # Compute available width for tool labels.
            # Left pane is fixed ~52 chars, minus border (2) + padding (2) + prefix (4).
            try:
                term_width = self._console.width or 120
                if not isinstance(term_width, (int, float)):
                    term_width = 120
            except (TypeError, ValueError, AttributeError):
                term_width = 120
            pane_width = min(52, int(term_width) // 2)
            label_budget = max(pane_width - 2 - 2 - 4, 30)

            for name, desc, toolset, elapsed, output_len, is_error in self._tool_history:
                tools_text.append("  → ", style="dim")
                # Build suffix first so we know how much space the label gets
                suffix = ""
                if toolset:
                    suffix += f" [{toolset}]"
                if elapsed is not None:
                    suffix += f" {elapsed:.1f}s"
                if output_len > 0:
                    suffix += f" {_format_size(output_len)}"
                if is_error:
                    suffix += " (error)"
                max_label = label_budget - len(suffix)
                label = desc if desc else name
                if max_label > 6 and len(label) > max_label:
                    label = label[: max_label - 1] + "…"
                tools_text.append(label, style="dim" if is_error else "")
                if toolset:
                    tools_text.append(f" [{toolset}]", style="dim")
                if elapsed is not None:
                    tools_text.append(f" {elapsed:.1f}s", style="dim")
                if output_len > 0:
                    tools_text.append(f" {_format_size(output_len)}", style="dim cyan")
                if is_error:
                    tools_text.append(" (error)", style="dim red")
                tools_text.append("\n")

            frame = _SPINNER_FRAMES[int(now * 8) % len(_SPINNER_FRAMES)]
            for _num, (name, started) in sorted(self._in_flight.items()):
                elapsed = now - started
                tools_text.append(f"  {frame} ", style="bold magenta")
                tools_text.append(f"{name}", style="bold")
                if elapsed >= 1.0:
                    tools_text.append(f" ({elapsed:.0f}s)", style="dim")
                tools_text.append("\n")

            if tools_text.plain.endswith("\n"):
                tools_text.right_crop(1)

            tool_count = len(self._tool_history) + len(self._in_flight)
            tool_title = f"[dim]Tools {tool_count}[/dim]" if self._approval_pending else f"[bold]Tools[/bold] [dim]{tool_count}[/dim]"
            sections.append(
                Panel(tools_text, title=tool_title,
                      title_align="left", border_style="dim", padding=(0, 1))
            )

        # Status line: static when approval pending, animated otherwise
        if self._approval_pending:
            status_text = Text()
            status_text.append("  ⏸ ", style="bold yellow")
            status_text.append("Approval required", style="bold yellow")
            if self._escape_hint:
                status_text.append(f"  {self._escape_hint}", style="dim")
            sections.append(status_text)
        elif show_analyzing or self._in_flight:
            status_text = Text()
            elapsed = now - self._start_time
            frame = _SPINNER_FRAMES[int(now * 8) % len(_SPINNER_FRAMES)]
            status_text.append(f"  {frame} ", style=f"bold {AI_COLOR}")
            if show_analyzing:
                status_text.append("Analyzing", style=f"bold {AI_COLOR}")
                dots = "." * (int(elapsed * 2) % 4)
                status_text.append(f"{dots:<4}", style=f"bold {AI_COLOR}")
            else:
                status_text.append("Gathering data", style=f"bold {AI_COLOR}")
                status_text.append("    ", style=f"bold {AI_COLOR}")
            if self._escape_hint:
                status_text.append(f"  {self._escape_hint}", style="dim")
            sections.append(status_text)

        if not sections:
            return Text("  Waiting…", style="dim italic")

        return Group(*sections)

    def _build_approval_data_pane(self) -> Any:
        """Build a data pane showing the command awaiting approval."""


        pane = Text()
        pane.append("\n")
        if self._pending_approval_descriptions:
            for desc in self._pending_approval_descriptions:
                pane.append(f"  {desc}\n", style="bold")
        else:
            pane.append("  (unknown command)\n", style="dim")
        pane.append("\n")
        pane.append("  Respond below…\n", style="dim italic")
        return pane

    def _build_display(self) -> Any:
        show_analyzing = self._thinking and not self._in_flight and not self._approval_pending
        left = self._build_left_pane(show_analyzing=show_analyzing)

        # Before any tool output arrives, just show the left pane content
        if not self._data_lines:
            return left

        if self._approval_pending:
            right = self._build_approval_data_pane()
        else:
            right = self._build_data_pane()

        # Side-by-side layout: 50/50 split between left and right panes.
        try:
            tw = self._console.width or 120
            if not isinstance(tw, (int, float)):
                tw = 120
        except (TypeError, ValueError, AttributeError):
            tw = 120
        half = (tw - 3) // 2  # -3 for padding/borders between columns
        left_width = max(half, 30)
        right_width = max(tw - left_width - 3, 30)
        table = Table.grid(padding=(0, 1))
        table.add_column("status", min_width=left_width)
        table.add_column("data", min_width=right_width)

        stats = self._build_stats_line()
        if self._approval_pending:
            data_border = "bold yellow"
            data_title = "[bold yellow]Approve bash command?[/bold yellow]"
        else:
            data_border = "dim"
            data_title = f"[bold]Data[/bold]{stats}"
        table.add_row(
            left,
            Panel(right, title=data_title, title_align="left", border_style=data_border, padding=(0, 0)),
        )

        return table

    def _tick(self) -> None:
        while not self._timer_stop.wait(0.15):
            with self._lock:
                if self._live is not None:
                    # Freeze scrolling when waiting for user approval
                    if self._approval_pending:
                        self._live.update(self._build_display())
                        continue
                    # Scroll logic: modulo-forward through buffer.
                    # When new data arrives (_follow_tail), jump to end.
                    # Otherwise scroll forward from 0, wrapping at end.
                    if self._data_lines and len(self._data_lines) > self._DATA_PANE_LINES:
                        max_start = len(self._data_lines) - self._DATA_PANE_LINES
                        if self._follow_tail:
                            # New data: snap to the end
                            self._scroll_offset = max_start
                            self._follow_tail = False
                            self._scroll_pause = 20  # ~3s pause at end before scrolling
                        elif self._scroll_pause > 0:
                            self._scroll_pause -= 1
                        else:
                            # Idle: scroll forward, wrap to 0 at the end
                            self._scroll_offset += self._SCROLL_SPEED
                            if self._scroll_offset >= max_start:
                                self._scroll_offset = 0
                                self._scroll_pause = 6  # ~1s pause at wrap
                    self._live.update(self._build_display())

    def start(self) -> None:
        """Start the Live display with the initial 'Thinking...' spinner."""
        # Buffer log messages to prevent them from breaking Live's transient rendering.
        # Filters must be installed on handlers (not the root logger) because
        # Python's logging propagation skips logger-level filters on ancestors.
        for handler in logging.getLogger().handlers:
            handler.addFilter(self._log_filter)

        try:
            self._live = _make_live(
                self._build_display(),
                console=self._console,
                transient=True,
                refresh_per_second=8,
            )
            self._live.start()
        except (TypeError, AttributeError):
            # Console may be a mock in tests — skip live display
            self._live = None
            for handler in logging.getLogger().handlers:
                handler.removeFilter(self._log_filter)
            return
        self._timer_stop.clear()
        timer_thread = threading.Thread(target=self._tick, daemon=True)
        timer_thread.start()

    def _stop_live(self) -> None:
        """Stop the Live display and replay buffered log messages."""
        self._timer_stop.set()
        if self._live is not None:
            try:
                self._live.stop()
            except (TypeError, AttributeError):
                pass
            self._live = None
        # Remove filter from all handlers and replay buffered log records
        for handler in logging.getLogger().handlers:
            handler.removeFilter(self._log_filter)
        if self._log_buffer:
            root = logging.getLogger()
            for record in self._log_buffer:
                for handler in root.handlers:
                    if record.levelno >= handler.level:
                        handler.emit(record)
            self._log_buffer.clear()

    def pause_for_approval(self) -> None:
        """Stop Live so the main thread can show an interactive approval prompt.

        Unlike ``flush()``, this does NOT replay buffered logs or print the
        investigation summary.  Call ``resume_after_approval()`` to restart.
        """
        with self._lock:
            self._timer_stop.set()
            if self._live is not None:
                try:
                    self._live.stop()
                except (TypeError, AttributeError):
                    pass
                self._live = None
            # Remove log filter so approval prompt logs go through normally
            for handler in logging.getLogger().handlers:
                handler.removeFilter(self._log_filter)

    def resume_after_approval(self) -> None:
        """Restart Live after the approval prompt has finished."""
        with self._lock:
            # Clear approval state so the display shows normal UI
            self._approval_pending = False
            self._pending_approval_descriptions = []
            if self._live is not None:
                return  # already running
            # Re-install log filter
            for handler in logging.getLogger().handlers:
                handler.addFilter(self._log_filter)
            try:
                self._live = _make_live(
                    self._build_display(),
                    console=self._console,
                    transient=True,
                    refresh_per_second=8,
                )
                self._live.start()
            except (TypeError, AttributeError):
                self._live = None
                return
            self._timer_stop.clear()
            timer_thread = threading.Thread(target=self._tick, daemon=True)
            timer_thread.start()

    def _process_completed(self) -> None:
        """Absorb completed tools into live state (tool history + tasks)."""
        if not self._completed:
            return

        for item in self._completed:
            _num, name, desc, toolset, elapsed, output_len, is_error, extra = item
            if name == _TODO_WRITE_TOOL_NAME and extra:
                self._live_tasks = extra
            else:
                self._tool_history.append((name, desc, toolset, elapsed, output_len or 0, is_error))

        self._completed.clear()

    def _print_investigation_summary(self) -> None:
        """Print full task list + tools as a permanent record before the answer."""


        if self._summary_printed:
            return
        if not self._live_tasks and not self._tool_history:
            return
        self._summary_printed = True

        # Print the task list
        if self._live_tasks:
            self._console.print(_build_task_panel(self._live_tasks))

        # Print the tools list
        if self._tool_history:
            tools_text = Text()
            try:
                term_width = int(self._console.width or 120)
            except (TypeError, ValueError):
                term_width = 120
            for idx, (name, desc, toolset, elapsed, output_len, is_error) in enumerate(self._tool_history):
                tool_num = self._tool_number_offset + idx + 1
                tools_text.append(f"  {tool_num}. ", style="dim")
                # Build suffix first so we know how much space the label gets
                suffix = ""
                if toolset:
                    suffix += f" [{toolset}]"
                if elapsed is not None:
                    suffix += f" {elapsed:.1f}s"
                if output_len > 0:
                    suffix += f" {_format_size(output_len)}"
                if is_error:
                    suffix += " (error)"
                # panel border(2) + padding(2) + number prefix(~6)
                prefix_len = 2 + 2 + len(f"  {tool_num}. ")
                label_budget = max(term_width - prefix_len - len(suffix), 20)
                label = desc if desc else name
                if len(label) > label_budget:
                    label = label[: label_budget - 1] + "…"
                tools_text.append(label, style="dim" if is_error else "")
                if toolset:
                    tools_text.append(f" [{toolset}]", style="dim")
                if elapsed is not None:
                    tools_text.append(f" {elapsed:.1f}s", style="dim")
                if output_len > 0:
                    tools_text.append(f" {_format_size(output_len)}", style="dim cyan")
                if is_error:
                    tools_text.append(" (error)", style="dim red")
                tools_text.append("\n")
            if tools_text.plain.endswith("\n"):
                tools_text.right_crop(1)
            tools_text.append("\n")
            tools_text.append("  /show <number> to view full output", style="dim italic")
            tool_count = len(self._tool_history)
            self._console.print(
                Panel(tools_text, title=f"[bold]Tools[/bold] [dim]{tool_count}[/dim]",
                      title_align="left", border_style="dim", padding=(0, 1))
            )

        # Print stats line
        if self._total_bytes > 0:
            stats = _format_size(self._total_bytes)
            self._console.print(
                f"  [dim]Analyzed {stats} across {self._total_queries} queries[/dim]"
            )

    def handle_event(
        self,
        event: StreamMessage,
        all_tool_calls: list,
        all_tool_calls_history: list,
    ) -> None:
        """Process one stream event. Called from the main render thread."""
        with self._lock:
            if event.event == StreamEvents.START_TOOL:
                self._thinking = False
                self._approval_pending = False
                self._pending_approval_descriptions = []
                num = self._next_tool_number
                self._next_tool_number += 1
                tool_name = event.data.get("tool_name", "...")
                self._in_flight[num] = (tool_name, time.time())
                if self._live is not None:
                    self._live.update(self._build_display())

            elif event.event == StreamEvents.TOOL_RESULT:
                all_tool_calls.append(event.data)

                description = event.data.get("description", "")
                tool_name = event.data.get("tool_name", event.data.get("name", ""))
                toolset_name = event.data.get("toolset_name", "")
                result_data = event.data.get("result", {})
                output_str = result_data.get("data", "")
                elapsed = result_data.get("elapsed_seconds")
                output_len = len(output_str) if output_str else 0
                is_error = output_len == 0 or result_data.get("error", False)
                tool_number = self._tool_number_offset + len(all_tool_calls)

                # Extract TodoWrite tasks for rich rendering
                extra = None
                if tool_name == _TODO_WRITE_TOOL_NAME:
                    params = result_data.get("params") or {}
                    todos = params.get("todos")
                    if isinstance(todos, list):
                        extra = todos
                        self._live_tasks = todos

                # Ingest raw output into scrolling data buffer (skip TodoWrite)
                if tool_name != _TODO_WRITE_TOOL_NAME:
                    self._ingest_output(tool_name, output_str, description=description)

                # Remove from in-flight
                removed = self._in_flight.pop(tool_number, None)
                # Also try to match by order only if number didn't match
                if removed is None:
                    for k in sorted(self._in_flight.keys()):
                        self._in_flight.pop(k, None)
                        break

                self._completed.append(
                    (tool_number, tool_name, description, toolset_name, elapsed, output_len, is_error, extra)
                )

                self._process_completed()

                if not self._in_flight:
                    self._thinking = True
                if self._live is not None:
                    self._live.update(self._build_display())

            elif event.event == StreamEvents.APPROVAL_REQUIRED:
                self._approval_pending = True
                self._thinking = False
                # Store pending approval descriptions for display
                pending = event.data.get("pending_approvals", [])
                self._pending_approval_descriptions = [
                    a.get("description", a.get("tool_name", "unknown")) for a in pending
                ]
                if self._live is not None:
                    self._live.update(self._build_display())

            elif event.event == StreamEvents.AI_MESSAGE:
                self._thinking = False
                if self._completed:
                    self._process_completed()

                reasoning = event.data.get("reasoning")
                content = event.data.get("content")
                if reasoning:
                    self._console.print(
                        f"  [italic dim]{reasoning}[/italic dim]"
                    )
                if content and content.strip():
                    self._console.print(
                        f"  [dim]{content}[/dim]"
                    )

                # Ensure live display is running for subsequent tool events
                if self._live is None:
                    self.start()

    def flush(self) -> None:
        """Ensure any remaining in-flight display is cleaned up."""
        with self._lock:
            if self._completed:
                self._process_completed()
            self._stop_live()
            self._print_investigation_summary()
            # Reset all state for next invocation
            self._data_lines.clear()
            self._tool_history.clear()
            self._scroll_offset = 0
            self._scroll_pause = 0
            self._total_bytes = 0
            self._total_queries = 0
            self._live_tasks = None


class SlashCommands(Enum):
    CONFIG = ("/config", "Open interactive toolset configuration editor")
    EXIT = ("/exit", "Exit interactive mode")
    HELP = ("/help", "Show help message with all commands")
    CLEAR = ("/clear", "Clear screen and reset conversation context")
    TOOLS_CONFIG = ("/tools", "Show available toolsets and their status")
    TOGGLE_TOOL_OUTPUT = (
        "/auto",
        "Toggle auto-display of tool outputs after responses",
    )
    LAST_OUTPUT = ("/last", "Show all tool outputs from last response")
    RUN = ("/run", "Run a bash command and optionally share with LLM")
    SHELL = (
        "/shell",
        "Drop into interactive shell, then optionally share session with LLM",
    )
    CONTEXT = ("/context", "Show conversation context size and token count")
    SHOW = ("/show", "Show specific tool output in scrollable view")
    FEEDBACK = ("/feedback", "Provide feedback on the agent's response")

    def __init__(self, command, description):
        self.command = command
        self.description = description


class SlashCommandCompleter(Completer):
    def __init__(self, unsupported_commands: Optional[List[str]] = None):
        # Build commands dictionary, excluding unsupported commands
        all_commands = {cmd.command: cmd.description for cmd in SlashCommands}
        if unsupported_commands:
            self.commands = {
                cmd: desc
                for cmd, desc in all_commands.items()
                if cmd not in unsupported_commands
            }
        else:
            self.commands = all_commands

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            word = text
            for cmd, description in self.commands.items():
                if cmd.startswith(word):
                    yield Completion(
                        cmd, start_position=-len(word), display=f"{cmd} - {description}"
                    )


class SmartPathCompleter(Completer):
    """Path completer that works for relative paths starting with ./ or ../"""

    def __init__(self):
        self.path_completer = PathCompleter()

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()
        if not words:
            return

        last_word = words[-1]
        # Only complete if the last word looks like a relative path (not absolute paths starting with /)
        if last_word.startswith("./") or last_word.startswith("../"):
            # Create a temporary document with just the path part
            path_doc = Document(last_word, len(last_word))

            for completion in self.path_completer.get_completions(
                path_doc, complete_event
            ):
                yield Completion(
                    completion.text,
                    start_position=completion.start_position - len(last_word),
                    display=completion.display,
                    display_meta=completion.display_meta,
                )


class ConditionalExecutableCompleter(Completer):
    """Executable completer that only works after /run commands"""

    def __init__(self):
        self.executable_completer = ExecutableCompleter()

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # Only provide executable completion if the line starts with /run
        if text.startswith("/run "):
            # Extract the command part after "/run "
            command_part = text[5:]  # Remove "/run "

            # Only complete the first word (the executable name)
            words = command_part.split()
            if len(words) <= 1:  # Only when typing the first word
                # Create a temporary document with just the command part
                cmd_doc = Document(command_part, len(command_part))

                seen_completions = set()
                for completion in self.executable_completer.get_completions(
                    cmd_doc, complete_event
                ):
                    # Remove duplicates based on text only (display can be FormattedText which is unhashable)
                    if completion.text not in seen_completions:
                        seen_completions.add(completion.text)
                        yield Completion(
                            completion.text,
                            start_position=completion.start_position
                            - len(command_part),
                            display=completion.display,
                            display_meta=completion.display_meta,
                        )


class ShowCommandCompleter(Completer):
    """Completer that provides suggestions for /show command based on tool call history"""

    def __init__(self):
        self.tool_calls_history = []

    def update_history(self, tool_calls_history: List[ToolCallResult]):
        """Update the tool calls history for completion suggestions"""
        self.tool_calls_history = tool_calls_history

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # Only provide completion if the line starts with /show
        if text.startswith("/show "):
            # Extract the argument part after "/show "
            show_part = text[6:]  # Remove "/show "

            # Don't complete if there are already multiple words
            words = show_part.split()
            if len(words) > 1:
                return

            # Provide completions based on available tool calls
            if self.tool_calls_history:
                for i, tool_call in enumerate(self.tool_calls_history):
                    tool_index = str(i + 1)  # 1-based index
                    tool_description = tool_call.description

                    # Complete tool index numbers (show all if empty, or filter by what user typed)
                    if (
                        not show_part
                        or tool_index.startswith(show_part)
                        or show_part.lower() in tool_description.lower()
                    ):
                        yield Completion(
                            tool_index,
                            start_position=-len(show_part),
                            display=f"{tool_index} - {tool_description}",
                        )


WELCOME_BANNER = f"[dim]Type [bold]{SlashCommands.HELP.command}[/bold] for commands, [bold]{SlashCommands.CONFIG.command}[/bold] to configure, [bold]{SlashCommands.EXIT.command}[/bold] to quit[/dim]"


def format_tool_call_output(
    tool_call: ToolCallResult, tool_index: Optional[int] = None
) -> str:
    """
    Format a single tool call result for display in a rich panel.

    Args:
        tool_call: ToolCallResult object containing the tool execution result
        tool_index: Optional 1-based index of the tool for /show command

    Returns:
        Formatted string for display in a rich panel
    """
    result = tool_call.result
    output_str = result.get_stringified_data()

    color = result.status.to_color()
    MAX_CHARS = 500
    if len(output_str) == 0:
        content = f"[{color}]<empty>[/{color}]"
    elif len(output_str) > MAX_CHARS:
        truncated = output_str[:MAX_CHARS].strip()
        remaining_chars = len(output_str) - MAX_CHARS
        show_hint = f"/show {tool_index}" if tool_index else "/show"
        content = f"[{color}]{truncated}[/{color}]\n\n[dim]... truncated ({remaining_chars:,} more chars) - {show_hint} to view full output[/dim]"
    else:
        content = f"[{color}]{output_str}[/{color}]"

    return content


def build_modal_title(tool_call: ToolCallResult, wrap_status: str) -> str:
    """Build modal title with navigation instructions."""
    return f"{tool_call.description} (exit: q, nav: ↑↓/j/k/g/G/d/u/f/b/space, wrap: w [{wrap_status}])"


def strip_ansi_codes(text: str) -> str:
    ansi_escape_pattern = re.compile(
        r"\x1b\[[0-9;]*[a-zA-Z]|\033\[[0-9;]*[a-zA-Z]|\^\[\[[0-9;]*[a-zA-Z]"
    )
    return ansi_escape_pattern.sub("", text)


def detect_lexer(content: str) -> Optional[PygmentsLexer]:
    """
    Detect appropriate lexer for content using Pygments' built-in detection.

    Args:
        content: String content to analyze

    Returns:
        PygmentsLexer instance if content type is detected, None otherwise
    """
    if not content.strip():
        return None

    try:
        # Use Pygments' built-in lexer guessing
        lexer = guess_lexer(content)
        return PygmentsLexer(lexer.__class__)
    except Exception:
        # If detection fails, return None for no syntax highlighting
        return None


def handle_show_command(
    show_arg: str, all_tool_calls_history: List[ToolCallResult], console: Console
) -> None:
    """Handle the /show command to display tool outputs."""
    if not all_tool_calls_history:
        console.print(
            f"[bold {ERROR_COLOR}]No tool calls available in the conversation.[/bold {ERROR_COLOR}]"
        )
        return

    if not show_arg:
        # Show list of available tools
        console.print(
            f"[bold {STATUS_COLOR}]Available tool outputs:[/bold {STATUS_COLOR}]"
        )
        for i, tool_call in enumerate(all_tool_calls_history):
            console.print(f"  {i+1}. {tool_call.description}")
        console.print("[dim]Usage: /show <number> or /show <tool_name>[/dim]")
        return

    # Find tool by number or name
    tool_to_show = None
    try:
        tool_index = int(show_arg) - 1  # Convert to 0-based index
        if 0 <= tool_index < len(all_tool_calls_history):
            tool_to_show = all_tool_calls_history[tool_index]
        else:
            console.print(
                f"[bold {ERROR_COLOR}]Invalid tool index. Use 1-{len(all_tool_calls_history)}[/bold {ERROR_COLOR}]"
            )
            return
    except ValueError:
        # Try to find by tool name/description
        for tool_call in all_tool_calls_history:
            if show_arg.lower() in tool_call.description.lower():
                tool_to_show = tool_call
                break

        if not tool_to_show:
            console.print(
                f"[bold {ERROR_COLOR}]Tool not found: {show_arg}[/bold {ERROR_COLOR}]"
            )
            return

    # Show the tool output in modal
    show_tool_output_modal(tool_to_show, console)


def show_tool_output_modal(tool_call: ToolCallResult, console: Console) -> None:
    """
    Display a tool output in a scrollable modal window.

    Args:
        tool_call: ToolCallResult object to display
        console: Rich console (for fallback display)
    """
    try:
        # Get the full output
        output = tool_call.result.get_stringified_data()
        output = strip_ansi_codes(output)
        title = build_modal_title(tool_call, "off")  # Word wrap starts disabled

        # Detect appropriate syntax highlighting
        lexer = detect_lexer(output)

        # Create text area with the output
        text_area = TextArea(
            text=output,
            read_only=True,
            scrollbar=True,
            line_numbers=False,
            wrap_lines=False,  # Disable word wrap by default
            lexer=lexer,
        )

        # Create header
        header = Window(
            FormattedTextControl(title),
            height=1,
            style="reverse",
        )

        # Create layout
        layout = Layout(
            HSplit(
                [
                    header,
                    text_area,
                ]
            )
        )

        # Create key bindings
        bindings = KeyBindings()

        # Track exit state to prevent double exits
        exited = False

        # Exit commands (q, escape, or ctrl+c to exit)
        @bindings.add("q")
        @bindings.add("escape")
        @bindings.add("c-c")
        def _(event):
            nonlocal exited
            if not exited:
                exited = True
                event.app.exit()

        # Vim/less-like navigation
        @bindings.add("j")
        @bindings.add("down")
        def _(event):
            event.app.layout.focus(text_area)
            text_area.buffer.cursor_down()

        @bindings.add("k")
        @bindings.add("up")
        def _(event):
            event.app.layout.focus(text_area)
            text_area.buffer.cursor_up()

        @bindings.add("g")
        @bindings.add("home")
        def _(event):
            event.app.layout.focus(text_area)
            text_area.buffer.cursor_position = 0

        @bindings.add("G")
        @bindings.add("end")
        def _(event):
            event.app.layout.focus(text_area)
            # Go to last line, then to beginning of that line
            text_area.buffer.cursor_position = len(text_area.buffer.text)
            text_area.buffer.cursor_left(
                count=text_area.buffer.document.cursor_position_col
            )

        @bindings.add("d")
        @bindings.add("c-d")
        @bindings.add("pagedown")
        def _(event):
            event.app.layout.focus(text_area)
            # Get current window height and scroll by half
            window_height = event.app.output.get_size().rows - 1  # -1 for header
            scroll_amount = max(1, window_height // 2)
            for _ in range(scroll_amount):
                text_area.buffer.cursor_down()

        @bindings.add("u")
        @bindings.add("c-u")
        @bindings.add("pageup")
        def _(event):
            event.app.layout.focus(text_area)
            # Get current window height and scroll by half
            window_height = event.app.output.get_size().rows - 1  # -1 for header
            scroll_amount = max(1, window_height // 2)
            for _ in range(scroll_amount):
                text_area.buffer.cursor_up()

        @bindings.add("f")
        @bindings.add("c-f")
        @bindings.add("space")
        def _(event):
            event.app.layout.focus(text_area)
            # Get current window height and scroll by full page
            window_height = event.app.output.get_size().rows - 1  # -1 for header
            scroll_amount = max(1, window_height)
            for _ in range(scroll_amount):
                text_area.buffer.cursor_down()

        @bindings.add("b")
        @bindings.add("c-b")
        def _(event):
            event.app.layout.focus(text_area)
            # Get current window height and scroll by full page
            window_height = event.app.output.get_size().rows - 1  # -1 for header
            scroll_amount = max(1, window_height)
            for _ in range(scroll_amount):
                text_area.buffer.cursor_up()

        @bindings.add("w")
        def _(event):
            # Toggle word wrap
            text_area.wrap_lines = not text_area.wrap_lines
            # Update the header to show current wrap state
            wrap_status = "on" if text_area.wrap_lines else "off"
            new_title = build_modal_title(tool_call, wrap_status)
            header.content = FormattedTextControl(new_title)

        # Create and run application
        app: Application = Application(
            layout=layout,
            key_bindings=bindings,
            full_screen=True,
        )

        app.run()

    except Exception as e:
        # Fallback to regular display
        console.print(f"[bold red]Error showing modal: {e}[/bold red]")
        console.print(format_tool_call_output(tool_call))


def handle_context_command(messages, ai: ToolCallingLLM, console: Console) -> None:
    """Handle the /context command to show conversation context statistics."""
    if messages is None:
        console.print(
            f"[bold {STATUS_COLOR}]No conversation context yet.[/bold {STATUS_COLOR}]"
        )
        return

    # Calculate context statistics
    tokens_metadata = ai.llm.count_tokens(
        messages
    )  # TODO: pass tools to also count tokens used by input tools
    max_context_size = ai.llm.get_context_window_size()
    max_output_tokens = ai.llm.get_maximum_output_token()
    available_tokens = (
        max_context_size - tokens_metadata.total_tokens - max_output_tokens
    )

    # Analyze token distribution by role and tool calls
    role_token_usage: DefaultDict[str, int] = defaultdict(int)
    tool_token_usage: DefaultDict[str, int] = defaultdict(int)
    tool_call_counts: DefaultDict[str, int] = defaultdict(int)

    for msg in messages:
        role = msg.get("role", "unknown")
        message_tokens = ai.llm.count_tokens(
            [msg]
        )  # TODO: pass tools to also count tokens used by input tools
        role_token_usage[role] += message_tokens.total_tokens

        # Track individual tool usage
        if role == "tool":
            tool_name = msg.get("name", "unknown_tool")
            tool_token_usage[tool_name] += message_tokens.total_tokens
            tool_call_counts[tool_name] += 1

    # Display context information
    console.print(f"[bold {STATUS_COLOR}]Conversation Context:[/bold {STATUS_COLOR}]")
    console.print(
        f"  Context used: {tokens_metadata.total_tokens:,} / {max_context_size:,} tokens ({(tokens_metadata.total_tokens / max_context_size) * 100:.1f}%)"
    )
    console.print(
        f"  Space remaining: {available_tokens:,} for input ({(available_tokens / max_context_size) * 100:.1f}%) + {max_output_tokens:,} reserved for output ({(max_output_tokens / max_context_size) * 100:.1f}%)"
    )

    # Show token breakdown by role
    console.print("  Token breakdown:")
    for role in ["system", "user", "assistant", "tool"]:
        if role in role_token_usage:
            tokens = role_token_usage[role]
            percentage = (
                (tokens / tokens_metadata.total_tokens) * 100
                if tokens_metadata.total_tokens > 0
                else 0
            )
            role_name = {
                "system": "system prompt",
                "user": "user messages",
                "assistant": "assistant replies",
                "tool": "tool responses",
            }.get(role, role)
            console.print(f"    {role_name}: {tokens:,} tokens ({percentage:.1f}%)")

            # Show top 4 tools breakdown under tool responses
            if role == "tool" and tool_token_usage:
                sorted_tools = sorted(
                    tool_token_usage.items(), key=lambda x: x[1], reverse=True
                )

                # Show top 4 tools
                for tool_name, tool_tokens in sorted_tools[:4]:
                    tool_percentage = (tool_tokens / tokens) * 100 if tokens > 0 else 0
                    call_count = tool_call_counts[tool_name]
                    console.print(
                        f"      {tool_name}: {tool_tokens:,} tokens ({tool_percentage:.1f}%) from {call_count} tool calls"
                    )

                # Show "other" category if there are more than 4 tools
                if len(sorted_tools) > 4:
                    other_tokens = sum(
                        tool_tokens for _, tool_tokens in sorted_tools[4:]
                    )
                    other_calls = sum(
                        tool_call_counts[tool_name] for tool_name, _ in sorted_tools[4:]
                    )
                    other_percentage = (
                        (other_tokens / tokens) * 100 if tokens > 0 else 0
                    )
                    other_count = len(sorted_tools) - 4
                    console.print(
                        f"      other ({other_count} tools): {other_tokens:,} tokens ({other_percentage:.1f}%) from {other_calls} tool calls"
                    )

    if available_tokens < 0:
        console.print(
            f"[bold {ERROR_COLOR}]⚠️  Context will be truncated on next LLM call[/bold {ERROR_COLOR}]"
        )


def prompt_for_llm_sharing(
    session: PromptSession, style: Style, content: str, content_type: str
) -> Optional[str]:
    """
    Prompt user to share content with LLM and return formatted user input.

    Args:
        session: PromptSession for user input
        style: Style for prompts
        content: The content to potentially share (command output, shell session, etc.)
        content_type: Description of content type (e.g., "command", "shell session")

    Returns:
        Formatted user input string if user chooses to share, None otherwise
    """
    # Create a temporary session without history for y/n prompts
    temp_session = PromptSession(history=InMemoryHistory())  # type: ignore

    share_prompt = temp_session.prompt(
        [("class:prompt", f"Share {content_type} with LLM? (Y/n): ")], style=style
    )

    if not share_prompt.lower().startswith("n"):
        comment_prompt = temp_session.prompt(
            [("class:prompt", "Optional comment/question (press Enter to skip): ")],
            style=style,
        )

        user_input = f"I {content_type}:\\n\\n```\\n{content}\\n```\\n\\n"

        if comment_prompt.strip():
            user_input += f"Comment/Question: {comment_prompt.strip()}"

        return user_input

    return None


def _run_inline_menu(
    options: list[str], console: Console, header: Any = None
) -> Optional[int]:
    """Run an inline menu with arrow-key navigation using prompt_toolkit.

    Uses prompt_toolkit ``Application`` with ``erase_when_done=True`` so the
    entire UI (header panel + options) is erased when the user makes a
    selection.  Arrow keys, j/k, number keys, Enter, and Escape all work
    correctly across terminals and SSH.

    Args:
        options: List of option strings to display.
        console: Rich console for output.
        header: Optional Rich renderable printed above the menu (erased
                via ANSI codes after the menu exits).

    Returns:
        Index of selected option (0-based), or ``None`` if cancelled.
    """
    selected = [0]
    result: List[Optional[int]] = [None]

    def get_menu_text():
        lines = []
        for i, option in enumerate(options):
            if i == selected[0]:
                lines.append(("bold", f"  > {i + 1}. {option}\n"))
            else:
                lines.append(("", f"    {i + 1}. {option}\n"))
        lines.append(("class:hint", "\n  Esc to cancel"))
        return lines

    bindings = KeyBindings()

    @bindings.add("up")
    @bindings.add("k")
    def _up(event: Any) -> None:
        selected[0] = (selected[0] - 1) % len(options)

    @bindings.add("down")
    @bindings.add("j")
    def _down(event: Any) -> None:
        selected[0] = (selected[0] + 1) % len(options)

    @bindings.add("enter")
    def _enter(event: Any) -> None:
        result[0] = selected[0]
        event.app.exit()

    @bindings.add("escape")
    @bindings.add("c-c")
    def _cancel(event: Any) -> None:
        result[0] = None
        event.app.exit()

    for i in range(min(9, len(options))):

        @bindings.add(str(i + 1))
        def _select_num(event: Any, idx: int = i) -> None:
            result[0] = idx
            event.app.exit()

    menu_style = Style.from_dict({"hint": "#666666"})
    layout = Layout(Window(FormattedTextControl(get_menu_text, show_cursor=False)))

    # Measure header height so we can erase it after the menu exits
    header_lines = 0
    if header is not None:
        buf = StringIO()
        measure = Console(file=buf, width=console.width or 120, force_terminal=True)
        measure.print(header)
        header_lines = buf.getvalue().count("\n")
        console.print(header)

    app: Application = Application(
        layout=layout,
        key_bindings=bindings,
        style=menu_style,
        full_screen=False,
        erase_when_done=True,
    )
    app.run()

    # erase_when_done clears the menu; also erase the Rich header above it
    if header_lines > 0:
        sys.stdout.write(f"\x1b[{header_lines}A\x1b[0J")
        sys.stdout.flush()

    return result[0]


def handle_tool_approval(
    pending_approval: PendingToolApproval,
    style: Style,
    console: Console,
) -> tuple[bool, Optional[str]]:
    """
    Handle user approval for potentially sensitive commands.

    Shows an interactive menu per the bash toolset spec:
    1. Yes - one-time approval
    2. Yes, and don't ask again for <prefix> commands - saves to allow list
    3. Type feedback to tell Holmes what to do differently

    Args:
        pending_approval: The PendingToolApproval describing what needs approval
        style: Style for prompts
        console: Rich console for output

    Returns:
        Tuple of (approved: bool, feedback: Optional[str])
        - approved: True if user approves, False if denied
        - feedback: User's optional feedback message when denying
    """
    command = pending_approval.description
    prefixes = pending_approval.params.get("suggested_prefixes", [])

    # Format prefixes for display
    if prefixes:
        prefixes_display = ", ".join(f"{p}" for p in prefixes)
    else:
        prefixes_display = "<command>"

    # Build command panel — passed as header to the menu so everything
    # lives inside a single transient Rich Live (auto-erased on exit).
    panel = Panel(
        f"  {command or 'unknown'}",
        title="[bold]Approve bash command?[/bold]",
        title_align="left",
        border_style="bold yellow",
        padding=(1, 1),
    )

    options = [
        "Yes",
        f"Yes, and automatically approve '{prefixes_display}' in the future",
        "No, and tell Holmes what to do differently",
    ]

    result = _run_inline_menu(options, console, header=panel)

    if result == 0:  # Yes
        return True, None
    elif result == 1:  # Yes, save
        if prefixes:
            _save_approved_prefixes(prefixes)
            console.print(f"[green]✓ Saved `{prefixes_display}` to allow list[/green]")
        return True, None
    else:  # No (option 3) or Cancelled (Esc) - prompt for optional feedback
        temp_session = PromptSession(history=InMemoryHistory())  # type: ignore
        feedback_prompt = temp_session.prompt(
            [("class:prompt", "Optional feedback for the AI (press Enter to skip): ")],
            style=style,
        )
        feedback = feedback_prompt.strip() if feedback_prompt.strip() else None
        return False, feedback


def handle_run_command(
    bash_command: str, session: PromptSession, style: Style, console: Console
) -> Optional[str]:
    """
    Handle the /run command to execute a bash command.

    Args:
        bash_command: The bash command to execute
        session: PromptSession for user input
        style: Style for prompts
        console: Rich console for output

    Returns:
        Formatted user input string if user chooses to share, None otherwise
    """
    if not bash_command:
        console.print(
            f"[bold {ERROR_COLOR}]Usage: /run <bash_command>[/bold {ERROR_COLOR}]"
        )
        return None

    result = None
    output = ""
    error_message = ""

    try:
        console.print(
            f"[bold {STATUS_COLOR}]Running: {bash_command}[/bold {STATUS_COLOR}]"
        )
        result = subprocess.run(
            bash_command, shell=True, capture_output=True, text=True
        )

        output = result.stdout + result.stderr
        if result.returncode == 0:
            console.print(
                f"[bold green]✓ Command succeeded (exit code: {result.returncode})[/bold green]"
            )
        else:
            console.print(
                f"[bold {ERROR_COLOR}]✗ Command failed (exit code: {result.returncode})[/bold {ERROR_COLOR}]"
            )

        if output.strip():
            console.print(
                Panel(
                    output,
                    padding=(1, 2),
                    border_style="white",
                    title="Command Output",
                    title_align="left",
                )
            )

    except KeyboardInterrupt:
        error_message = "Command interrupted by user"
        console.print(f"[bold {ERROR_COLOR}]{error_message}[/bold {ERROR_COLOR}]")
    except Exception as e:
        error_message = f"Error running command: {e}"
        console.print(f"[bold {ERROR_COLOR}]{error_message}[/bold {ERROR_COLOR}]")

    # Build command output for sharing
    command_output = f"ran the command: `{bash_command}`\n\n"
    if result is not None:
        command_output += f"Exit code: {result.returncode}\n\n"
        if output.strip():
            command_output += f"Output:\n{output}"
    elif error_message:
        command_output += f"Error: {error_message}"

    return prompt_for_llm_sharing(session, style, command_output, "ran a command")


def handle_shell_command(
    session: PromptSession, style: Style, console: Console
) -> Optional[str]:
    """
    Handle the /shell command to start an interactive shell session.

    Args:
        session: PromptSession for user input
        style: Style for prompts
        console: Rich console for output

    Returns:
        Formatted user input string if user chooses to share, None otherwise
    """
    console.print(
        f"[bold {STATUS_COLOR}]Starting interactive shell. Type 'exit' to return to {agent_name}.[/bold {STATUS_COLOR}]"
    )
    console.print(
        "[dim]Shell session will be recorded and can be shared with LLM when you exit.[/dim]"
    )

    # Create a temporary file to capture shell session
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".log") as session_file:
        session_log_path = session_file.name

        try:
            # Start shell with script command to capture session
            shell_env = os.environ.copy()
            shell_env["PS1"] = "\\u@\\h:\\w$ "  # Set a clean prompt

            subprocess.run(f"script -q {session_log_path}", shell=True, env=shell_env)

            # Read the session log
            session_output = ""
            try:
                with open(session_log_path, "r") as f:
                    session_output = f.read()
            except Exception as e:
                console.print(
                    f"[bold {ERROR_COLOR}]Error reading session log: {e}[/bold {ERROR_COLOR}]"
                )
                return None

            if session_output.strip():
                console.print(
                    f"[bold {STATUS_COLOR}]Shell session ended.[/bold {STATUS_COLOR}]"
                )
                return prompt_for_llm_sharing(
                    session, style, session_output, "had an interactive shell session"
                )
            else:
                console.print(
                    f"[bold {STATUS_COLOR}]Shell session ended with no output.[/bold {STATUS_COLOR}]"
                )
                return None

        except KeyboardInterrupt:
            console.print(
                f"[bold {STATUS_COLOR}]Shell session interrupted.[/bold {STATUS_COLOR}]"
            )
            return None
        except Exception as e:
            console.print(
                f"[bold {ERROR_COLOR}]Error starting shell: {e}[/bold {ERROR_COLOR}]"
            )
            return None


def find_tool_index_in_history(
    tool_call: ToolCallResult, all_tool_calls_history: List[ToolCallResult]
) -> Optional[int]:
    """Find the 1-based index of a tool call in the complete history."""
    for i, historical_tool in enumerate(all_tool_calls_history):
        if historical_tool.tool_call_id == tool_call.tool_call_id:
            return i + 1  # 1-based index
    return None


def handle_last_command(
    last_response, console: Console, all_tool_calls_history: List[ToolCallResult]
) -> None:
    """Handle the /last command to show recent tool outputs."""
    if last_response is None or not last_response.tool_calls:
        console.print(
            f"[bold {ERROR_COLOR}]No tool calls available from the last response.[/bold {ERROR_COLOR}]"
        )
        return

    console.print(
        f"[bold {TOOLS_COLOR}]Used {len(last_response.tool_calls)} tools[/bold {TOOLS_COLOR}]"
    )
    for tool_call in last_response.tool_calls:
        tool_index = find_tool_index_in_history(tool_call, all_tool_calls_history)
        preview_output = format_tool_call_output(tool_call, tool_index)
        title = f"{tool_call.result.status.to_emoji()} {tool_call.description} -> returned {tool_call.result.return_code}"

        console.print(
            Panel(
                preview_output,
                padding=(1, 2),
                border_style=TOOLS_COLOR,
                title=title,
            )
        )


def handle_feedback_command(
    style: Style,
    console: Console,
    feedback: Feedback,
    feedback_callback: FeedbackCallback,
) -> None:
    """Handle the /feedback command to collect user feedback."""
    try:
        # Create a temporary session without history for feedback prompts
        temp_session = PromptSession(history=InMemoryHistory())  # type: ignore
        # Prominent privacy notice to users
        console.print(
            f"[bold {HELP_COLOR}]Privacy Notice:[/bold {HELP_COLOR}] {PRIVACY_NOTICE_BANNER}"
        )
        # A "Cancel" button of equal discoverability to "Sent" or "Submit" buttons must be made available
        console.print(
            "[bold yellow]💡 Tip: Press Ctrl+C at any time to cancel feedback[/bold yellow]"
        )

        # Ask for thumbs up/down rating with validation
        while True:
            rating_prompt = temp_session.prompt(
                [("class:prompt", "Was this response useful to you? 👍(y)/👎(n): ")],
                style=style,
            )

            rating_lower = rating_prompt.lower().strip()
            if rating_lower in ["y", "n"]:
                break
            else:
                console.print(
                    "[bold red]Please enter only 'y' for yes or 'n' for no.[/bold red]"
                )

        # Determine rating
        is_positive = rating_lower == "y"

        # Ask for additional comments
        comment_prompt = temp_session.prompt(
            [
                (
                    "class:prompt",
                    "Do you want to provide any additional comments for feedback? (press Enter to skip):\n",
                )
            ],
            style=style,
        )

        comment = comment_prompt.strip() if comment_prompt.strip() else None

        # Create UserFeedback object
        user_feedback = UserFeedback(is_positive, comment)

        if comment:
            console.print(
                f'[bold green]✓ Feedback recorded (rating={user_feedback.rating_emoji}, "{escape(comment)}")[/bold green]'
            )
        else:
            console.print(
                f"[bold green]✓ Feedback recorded (rating={user_feedback.rating_emoji}, no comment)[/bold green]"
            )

        # Final confirmation before submitting
        final_confirmation = temp_session.prompt(
            [("class:prompt", "\nDo you want to submit this feedback? (Y/n): ")],
            style=style,
        )

        # If user says no, cancel the feedback
        if final_confirmation.lower().strip().startswith("n"):
            console.print("[dim]Feedback cancelled.[/dim]")
            return

        feedback.user_feedback = user_feedback
        feedback_callback(feedback)
        console.print("[bold green]Thank you for your feedback! 🙏[/bold green]")

    except KeyboardInterrupt:
        console.print("[dim]Feedback cancelled.[/dim]")
        return


def display_recent_tool_outputs(
    tool_calls: List[ToolCallResult],
    console: Console,
    all_tool_calls_history: List[ToolCallResult],
) -> None:
    """Display recent tool outputs in rich panels (for auto-display after responses)."""
    console.print(
        f"[bold {TOOLS_COLOR}]Used {len(tool_calls)} tools[/bold {TOOLS_COLOR}]"
    )
    for tool_call in tool_calls:
        tool_index = find_tool_index_in_history(tool_call, all_tool_calls_history)
        preview_output = format_tool_call_output(tool_call, tool_index)
        title = (
            f"{tool_call.result.status.to_emoji()} {tool_call.description} -> "
            f"returned {tool_call.result.return_code}"
        )

        console.print(
            Panel(
                preview_output,
                padding=(1, 2),
                border_style=TOOLS_COLOR,
                title=title,
            )
        )


def save_conversation_to_file(
    json_output_file: str,
    messages: List,
    all_tool_calls_history: List[ToolCallResult],
    console: Console,
) -> None:
    """Save the current conversation to a JSON file."""
    try:
        # Create LLMResult-like structure for consistency with non-interactive mode
        conversation_result = LLMResult(
            messages=messages,
            tool_calls=all_tool_calls_history,
            result=None,  # No single result in interactive mode
            total_cost=0.0,  # TODO: Could aggregate costs from all responses if needed
            total_tokens=0,
            prompt_tokens=0,
            completion_tokens=0,
            metadata={
                "session_type": "interactive",
                "total_turns": len([m for m in messages if m.get("role") == "user"]),
            },
        )
        write_json_file(json_output_file, conversation_result.model_dump())
        console.print(
            f"[bold {STATUS_COLOR}]Conversation saved to {json_output_file}[/bold {STATUS_COLOR}]"
        )
    except Exception as e:
        logging.error(f"Failed to save conversation: {e}", exc_info=e)
        console.print(
            f"[bold {ERROR_COLOR}]Failed to save conversation: {e}[/bold {ERROR_COLOR}]"
        )


def _wait_for_completion_or_escape(
    thread: threading.Thread,
    cancel_event: threading.Event,
    stop_event: threading.Event,
    poll_interval: float = 0.1,
) -> bool:
    """Monitor stdin for Escape while thread runs. Returns True if interrupted.

    The ``stop_event`` can be set by the main thread to cleanly stop this
    listener (e.g. before showing an approval prompt or at the end of a turn).
    """
    if not _HAS_TERMINAL_CONTROL or not sys.stdin.isatty():
        thread.join()
        return False

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while thread.is_alive() and not stop_event.is_set():
            ready, _, _ = select_module.select([sys.stdin], [], [], poll_interval)
            if ready:
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    # Disambiguate standalone Escape from escape sequences (arrow keys etc.)
                    ready2, _, _ = select_module.select(
                        [sys.stdin], [], [], 0.1
                    )
                    if ready2:
                        # Part of an escape sequence — consume all remaining bytes
                        # (e.g. \x1b[A is 2 more bytes, \x1b[1;5A is more)
                        ch2 = sys.stdin.read(1)
                        if ch2 == "[":
                            # CSI sequence — read until final alpha byte
                            while True:
                                ch3 = sys.stdin.read(1)
                                if not ch3 or ch3.isalpha() or ch3 == "~":
                                    break
                        elif ch2 == "O":
                            sys.stdin.read(1)  # SS3: one more byte
                        continue
                    # Standalone Escape key pressed
                    cancel_event.set()
                    thread.join(timeout=2.0)
                    return True
        return False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def run_interactive_loop(
    ai: ToolCallingLLM,
    console: Console,
    initial_user_input: Optional[str],
    include_files: Optional[List[Path]],
    show_tool_output: bool,
    tracer=None,
    runbooks=None,
    system_prompt_additions: Optional[str] = None,
    check_version: bool = True,
    feedback_callback: Optional[FeedbackCallback] = None,
    json_output_file: Optional[str] = None,
    bash_always_deny: bool = False,
    bash_always_allow: bool = False,
    prompt_component_overrides: Optional[Dict[PromptComponent, bool]] = None,
    config: Optional[Config] = None,
    config_file_path: Optional[Path] = None,
) -> None:
    # Enable CLI mode for bash prefix loading (server mode doesn't call this)
    enable_cli_mode()

    # Silence display loggers — the interactive loop renders from stream events instead
    silence_display_loggers()

    # Initialize tracer - use DummyTracer if no tracer provided
    if tracer is None:
        tracer = DummyTracer()

    style = Style.from_dict(
        {
            "prompt": USER_COLOR,
            "bottom-toolbar": "#000000 bg:#ff0000",
            "bottom-toolbar.text": "#aaaa44 bg:#aa4444",
        }
    )

    # Set up approval callback based on CLI flags
    # --bash-always-deny: None (default behavior denies)
    # --bash-always-allow: callback that always approves
    # default: interactive approval handler
    approval_callback: Optional[ApprovalCallback] = None
    if bash_always_allow:
        approval_callback = lambda _: (True, None)
    elif not bash_always_deny:
        def approval_handler(
            pending_approval: PendingToolApproval,
        ) -> tuple[bool, Optional[str]]:
            return handle_tool_approval(
                pending_approval=pending_approval,
                style=style,
                console=console,
            )

        approval_callback = approval_handler

    # Create merged completer with slash commands, conditional executables, show command, and smart paths
    # TODO: remove unsupported_commands support once we implement feedback callback
    unsupported_commands = []
    if feedback_callback is None:
        unsupported_commands.append(SlashCommands.FEEDBACK.command)
    slash_completer = SlashCommandCompleter(unsupported_commands)
    executable_completer = ConditionalExecutableCompleter()
    show_completer = ShowCommandCompleter()
    path_completer = SmartPathCompleter()

    command_completer = merge_completers(
        [slash_completer, executable_completer, show_completer, path_completer]
    )

    # Use file-based history
    history_file = os.path.join(config_path_dir, "history")

    os.makedirs(os.path.dirname(history_file), exist_ok=True)
    history = FileHistory(history_file)
    if initial_user_input:
        history.append_string(initial_user_input)

    feedback = Feedback()
    feedback.metadata.update_llm(ai.llm)

    # Create custom key bindings for Ctrl+C behavior
    bindings = KeyBindings()
    status_message = ""
    version_message = ""

    def clear_version_message():
        nonlocal version_message
        version_message = ""
        session.app.invalidate()

    def on_version_check_complete(result):
        """Callback when background version check completes"""
        nonlocal version_message
        if not result.is_latest and result.update_message:
            version_message = result.update_message
            session.app.invalidate()

            # Auto-clear after 10 seconds
            timer = threading.Timer(10, clear_version_message)
            timer.start()

    @bindings.add("c-c")
    def _(event):
        """Handle Ctrl+C: clear input if text exists, otherwise quit."""
        buffer = event.app.current_buffer
        if buffer.text:
            nonlocal status_message
            status_message = f"Input cleared. Use {SlashCommands.EXIT.command} or Ctrl+C again to quit."
            buffer.reset()

            # call timer to clear status message after 3 seconds
            def clear_status():
                nonlocal status_message
                status_message = ""
                event.app.invalidate()

            timer = threading.Timer(3, clear_status)
            timer.start()
        else:
            # Quit if no text
            raise KeyboardInterrupt()

    def get_bottom_toolbar():
        messages = []

        # Ctrl-c status message (red background)
        if status_message:
            messages.append(("bg:#ff0000 fg:#000000", status_message))

        # Version message (yellow background)
        if version_message:
            if messages:
                messages.append(("", " | "))
            messages.append(("bg:#ffff00 fg:#000000", version_message))

        return messages if messages else None

    session = PromptSession(
        completer=command_completer,
        history=history,
        complete_style=CompleteStyle.COLUMN,
        reserve_space_for_menu=12,
        key_bindings=bindings,
        bottom_toolbar=get_bottom_toolbar,
    )  # type: ignore

    # Start background version check
    if check_version:
        check_version_async(on_version_check_complete)

    input_prompt = [("class:prompt", "User: ")]

    welcome_banner = WELCOME_BANNER
    if feedback_callback:
        welcome_banner += f", [bold]{SlashCommands.FEEDBACK.command}[/bold] for feedback"
    console.print(welcome_banner)

    if initial_user_input:
        console.print(
            f"\n[bold {USER_COLOR}]User:[/bold {USER_COLOR}] {initial_user_input}"
        )
    messages = None
    last_response = None
    all_tool_calls_history: List[
        ToolCallResult
    ] = []  # Track all tool calls throughout conversation

    while True:
        try:
            if initial_user_input:
                user_input = initial_user_input
                initial_user_input = None
            else:
                user_input = session.prompt(input_prompt, style=style)  # type: ignore

            if user_input.startswith("/"):
                original_input = user_input.strip()
                command = original_input.lower()
                # Handle prefix matching for slash commands
                matches = [
                    cmd
                    for cmd in slash_completer.commands.keys()
                    if cmd.startswith(command)
                ]
                if len(matches) == 1:
                    command = matches[0]
                elif len(matches) > 1:
                    console.print(
                        f"[bold {ERROR_COLOR}]Ambiguous command '{command}'. "
                        f"Matches: {', '.join(matches)}[/bold {ERROR_COLOR}]"
                    )
                    continue

                if command == SlashCommands.EXIT.command:
                    console.print(
                        f"[bold {STATUS_COLOR}]Exiting interactive mode.[/bold {STATUS_COLOR}]"
                    )
                    break
                elif command == SlashCommands.HELP.command:
                    console.print(
                        f"[bold {HELP_COLOR}]Available commands:[/bold {HELP_COLOR}]"
                    )
                    for cmd, description in slash_completer.commands.items():
                        # Only show feedback command if callback is available
                        if (
                            cmd == SlashCommands.FEEDBACK.command
                            and feedback_callback is None
                        ):
                            continue
                        console.print(f"  [bold]{cmd}[/bold] - {description}")
                    continue
                elif command == SlashCommands.CLEAR.command:
                    console.clear()
                    console.print(
                        f"[bold {STATUS_COLOR}]Screen cleared and context reset. "
                        f"You can now ask a new question.[/bold {STATUS_COLOR}]"
                    )
                    messages = None
                    last_response = None
                    all_tool_calls_history.clear()
                    # Reset the show completer history
                    show_completer.update_history([])
                    ai.reset_interaction_state()
                    continue
                elif command == SlashCommands.TOOLS_CONFIG.command:
                    pretty_print_toolset_status(ai.tool_executor.toolsets, console)
                    continue
                elif command == SlashCommands.TOGGLE_TOOL_OUTPUT.command:
                    show_tool_output = not show_tool_output
                    status = "enabled" if show_tool_output else "disabled"
                    console.print(
                        f"[bold yellow]Auto-display of tool outputs {status}.[/bold yellow]"
                    )
                    continue
                elif command == SlashCommands.LAST_OUTPUT.command:
                    handle_last_command(last_response, console, all_tool_calls_history)
                    continue
                elif command == SlashCommands.CONTEXT.command:
                    handle_context_command(messages, ai, console)
                    continue
                elif command.startswith(SlashCommands.SHOW.command):
                    # Parse the command to extract tool index or name
                    show_arg = original_input[len(SlashCommands.SHOW.command) :].strip()
                    handle_show_command(show_arg, all_tool_calls_history, console)
                    continue
                elif command.startswith(SlashCommands.RUN.command):
                    bash_command = original_input[
                        len(SlashCommands.RUN.command) :
                    ].strip()
                    shared_input = handle_run_command(
                        bash_command, session, style, console
                    )
                    if shared_input is None:
                        continue  # User chose not to share, continue to next input
                    user_input = shared_input
                elif command == SlashCommands.SHELL.command:
                    shared_input = handle_shell_command(session, style, console)
                    if shared_input is None:
                        continue  # User chose not to share or no output, continue to next input
                    user_input = shared_input
                elif command == SlashCommands.CONFIG.command:
                    if config is not None:
                        run_toolset_config_tui(
                            config,
                            config_file_path,
                            console,
                            preloaded_toolsets=ai.tool_executor.toolsets,
                        )
                    else:
                        console.print(
                            "[bold red]Config not available in this session. "
                            "Use 'holmes toolset config' from the CLI instead.[/bold red]"
                        )
                    continue
                elif (
                    command == SlashCommands.FEEDBACK.command
                    and feedback_callback is not None
                ):
                    handle_feedback_command(style, console, feedback, feedback_callback)
                    continue
                else:
                    console.print(f"Unknown command: {command}")
                    continue
            elif not user_input.strip():
                continue

            ai.reset_interaction_state()

            if messages is None:
                if include_files:
                    for file_path in include_files:
                        console.print(
                            f"[bold yellow]Adding file {file_path} to context[/bold yellow]"
                        )
                messages = build_initial_ask_messages(
                    user_input,
                    include_files,
                    ai.tool_executor,
                    runbooks,
                    system_prompt_additions,
                    prompt_component_overrides=prompt_component_overrides,
                )
            else:
                messages.append({"role": "user", "content": user_input})

            escape_hint = (
                "(press escape to interrupt)"
                if _HAS_TERMINAL_CONTROL and sys.stdin.isatty()
                else ""
            )
            console.print()  # blank line before progress

            # Snapshot messages before the call so we can rollback on interrupt
            messages_snapshot = list(messages)

            cancel_event = threading.Event()

            # Approval coordination: background thread stores pending approvals
            # and blocks; main thread runs the interactive prompt and sends back
            # the decisions.  This avoids terminal conflicts between Rich Live
            # (main thread) and prompt_toolkit (also needs the main thread).
            approval_pending_event = threading.Event()  # bg → main: "I need approval"
            approval_done_event = threading.Event()     # main → bg: "decisions ready"
            approval_data: List[Optional[List[dict]]] = [None]  # pending_approvals list
            approval_decisions: List[Optional[List["ToolApprovalDecision"]]] = [None]

            # --- Stream-based AI call ---
            # The background thread drains call_stream() and pushes events to a queue.
            # The main thread renders events and handles escape-to-interrupt.
            event_queue: queue.Queue = queue.Queue()
            call_error: List[Optional[Exception]] = [None]

            with tracer.start_trace(user_input) as trace_span:
                trace_span.log(
                    input=user_input,
                    metadata={"type": "user_question"},
                )

                tool_number_offset = len(all_tool_calls_history)

                def _run_ai_stream(
                    _event_queue=event_queue,
                    _call_error=call_error,
                    _messages=messages,
                    _trace_span=trace_span,
                    _cancel_event=cancel_event,
                    _has_approval=approval_callback is not None,
                    _tool_number_offset=tool_number_offset,
                    _iteration_offset=0,
                ) -> None:
                    try:
                        # Replicate the approval loop from call()
                        tool_decisions: Optional[List[ToolApprovalDecision]] = None
                        while True:
                            stream = ai.call_stream(
                                msgs=_messages,
                                enable_tool_approval=_has_approval,
                                tool_decisions=tool_decisions,
                                trace_span=_trace_span,
                                cancel_event=_cancel_event,
                                tool_number_offset=_tool_number_offset,
                                iteration_offset=_iteration_offset,
                            )
                            tool_decisions = None
                            last_event = None
                            for event in stream:
                                last_event = event
                                _event_queue.put(event)
                                if event.event == StreamEvents.TOOL_RESULT:
                                    _tool_number_offset += 1
                                if event.event in (StreamEvents.ANSWER_END, StreamEvents.APPROVAL_REQUIRED):
                                    break

                            if last_event is None:
                                raise Exception("Stream ended without yielding any events")

                            # Check if we got an approval-required event
                            if last_event.event == StreamEvents.APPROVAL_REQUIRED:
                                td = last_event.data
                                _messages[:] = td["messages"]
                                # call_stream returns absolute iteration count;
                                # carry it forward so the next call_stream
                                # enforces the global max_steps limit.
                                _iteration_offset = td.get("num_llm_calls", _iteration_offset)
                                # Hand off to main thread for interactive prompt
                                approval_data[0] = td["pending_approvals"]
                                approval_done_event.clear()
                                approval_pending_event.set()
                                # Block until main thread fills in decisions
                                approval_done_event.wait()
                                tool_decisions = approval_decisions[0]
                                approval_decisions[0] = None
                                approval_data[0] = None
                                continue
                            else:
                                # ANSWER_END — done
                                break
                    except Exception as exc:  # noqa: BLE001
                        _call_error[0] = exc
                    finally:
                        _event_queue.put(_SENTINEL)

                # Copy context so Braintrust's current_span ContextVar propagates to the thread,
                # otherwise ChatCompletionWrapper spans won't nest under the trace span.
                ctx = contextvars.copy_context()
                ai_thread = threading.Thread(target=ctx.run, args=(_run_ai_stream,), daemon=True)
                ai_thread.start()

                # Start escape listener in a background thread so the main
                # thread can render events from the queue.
                escape_stop = threading.Event()
                escape_thread = threading.Thread(
                    target=_wait_for_completion_or_escape,
                    args=(ai_thread, cancel_event, escape_stop),
                    daemon=True,
                )
                escape_thread.start()

                # --- Main thread: render stream events while monitoring for escape ---
                progress = AgenticProgressRenderer(console, tool_number_offset, escape_hint)
                progress.start()
                all_tool_calls_this_turn: list[dict] = []
                terminal_data = None
                interrupted = False
                accumulated_stats = RequestStats()
                total_num_llm_calls = 0

                while True:
                    try:
                        item = event_queue.get(timeout=0.1)
                    except queue.Empty:
                        if cancel_event.is_set():
                            interrupted = True
                            break
                        continue

                    if item is _SENTINEL:
                        break

                    event = item

                    # Render the event
                    progress.handle_event(
                        event, all_tool_calls_this_turn, all_tool_calls_history,
                    )

                    if event.event == StreamEvents.ANSWER_END:
                        terminal_data = event.data
                        total_num_llm_calls = terminal_data.get("num_llm_calls", 0)
                        accumulated_stats += RequestStats(**terminal_data.get("costs", {}))
                    elif event.event == StreamEvents.APPROVAL_REQUIRED:
                        # Accumulate stats from the pre-approval segment
                        # (num_llm_calls is absolute, so assign not accumulate)
                        total_num_llm_calls = event.data.get("num_llm_calls", 0)
                        accumulated_stats += RequestStats(**event.data.get("costs", {}))
                        # Wait for bg thread to signal it's ready
                        approval_pending_event.wait(timeout=5.0)
                        approval_pending_event.clear()

                        # 1. Pause Live display
                        progress.pause_for_approval()

                        # 2. Stop escape listener (it holds terminal in cbreak)
                        escape_stop.set()
                        escape_thread.join(timeout=2.0)

                        # 3. Run approval prompt on main thread
                        pending = approval_data[0] or []
                        decisions = ai._prompt_for_approval_decisions(
                            pending, approval_callback
                        )
                        approval_decisions[0] = decisions
                        approval_done_event.set()

                        # 4. Resume progress renderer and escape listener
                        progress.resume_after_approval()

                        escape_stop = threading.Event()
                        escape_thread = threading.Thread(
                            target=_wait_for_completion_or_escape,
                            args=(ai_thread, cancel_event, escape_stop),
                            daemon=True,
                        )
                        escape_thread.start()

                # Clean up live display and wait for threads
                escape_stop.set()
                progress.flush()
                ai_thread.join(timeout=5.0)
                escape_thread.join(timeout=2.0)

                if interrupted or isinstance(call_error[0], LLMInterruptedError):
                    messages = messages_snapshot
                    console.print(
                        f"[bold {STATUS_COLOR}]Interrupted.[/bold {STATUS_COLOR}]\n"
                    )
                    continue
                elif call_error[0] is not None:
                    raise call_error[0]

                if not terminal_data:
                    raise Exception("Stream ended without ANSWER_END")

                # Build LLMResult from stream data
                deduped: dict[str, dict] = {}
                for tc in all_tool_calls_this_turn:
                    deduped[tc.get("tool_call_id", id(tc))] = tc
                response = LLMResult(
                    result=terminal_data["content"],
                    tool_calls=list(deduped.values()),
                    num_llm_calls=total_num_llm_calls,
                    messages=terminal_data["messages"],
                    metadata=terminal_data.get("metadata"),
                    **accumulated_stats.model_dump(),
                )

                trace_span.log(
                    output=response.result,
                )
                trace_url = tracer.get_trace_url()

            messages = response.messages
            last_response = response
            feedback.metadata.add_llm_response(user_input, response.result)

            if response.tool_calls:
                all_tool_calls_history.extend(response.tool_calls)
                # Update the show completer with the latest tool call history
                show_completer.update_history(all_tool_calls_history)

            if show_tool_output and response.tool_calls:
                display_recent_tool_outputs(
                    response.tool_calls, console, all_tool_calls_history
                )
            console.print(
                Panel(
                    Markdown(f"{response.result}"),
                    padding=(1, 2),
                    border_style=AI_COLOR,
                    title=f"[bold {AI_COLOR}]AI Response[/bold {AI_COLOR}]",
                    title_align="left",
                )
            )

            console.print("")

            # Save conversation after each AI response
            if json_output_file and messages:
                save_conversation_to_file(
                    json_output_file, messages, all_tool_calls_history, console
                )
        except typer.Abort:
            console.print(
                f"[bold {STATUS_COLOR}]Exiting interactive mode.[/bold {STATUS_COLOR}]"
            )
            break
        except EOFError:  # Handle Ctrl+D
            console.print(
                f"[bold {STATUS_COLOR}]Exiting interactive mode.[/bold {STATUS_COLOR}]"
            )
            break
        except Exception as e:
            logging.error("An error occurred during interactive mode:", exc_info=e)
            console.print(f"[bold {ERROR_COLOR}]Error: {e}[/bold {ERROR_COLOR}]")
        finally:
            # Print trace URL for debugging (works for both success and error cases)
            trace_url = tracer.get_trace_url()
            if trace_url:
                console.print(f"🔍 View trace: {trace_url}")

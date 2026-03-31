from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class StatusEventKind(str, Enum):
    """Kinds of status events emitted during HolmesGPT initialization."""

    TOOLSET_CHECKING = "toolset_checking"
    TOOLSET_READY = "toolset_ready"
    TOOLSET_LAZY = "toolset_lazy"
    DATASOURCE_COUNT = "datasource_count"
    MODEL_LOADED = "model_loaded"
    TOOL_OVERRIDE = "tool_override"
    REFRESHING = "refreshing"


class ToolsetStatus(str, Enum):
    """Status of a toolset after prerequisite checking."""

    ENABLED = "enabled"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass
class StatusEvent:
    """Structured event emitted during HolmesGPT initialization.

    Used by interactive/UI callers to render real-time progress.
    Non-interactive callers simply don't pass an ``on_event`` callback
    and the existing ``display_logger`` messages remain unchanged.
    """

    kind: StatusEventKind
    name: str = ""
    status: ToolsetStatus = ToolsetStatus.ENABLED
    message: str = ""
    error: str = ""
    count: int = 0


EventCallback = Optional[Callable[[StatusEvent], None]]
"""Signature for the ``on_event`` callback threaded through initialization."""

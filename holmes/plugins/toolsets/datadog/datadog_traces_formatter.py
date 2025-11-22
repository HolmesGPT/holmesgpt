"""Formatting utilities for Datadog traces output."""

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Tuple

from holmes.plugins.toolsets.utils import unix_nano_to_rfc3339


def parse_datadog_span_timestamp(attrs: Dict[str, Any]) -> Tuple[int, int]:
    """
    Parse timestamp and duration from Datadog span attributes.

    Returns:
        Tuple of (start_ns, duration_ns)
    """
    custom = attrs.get("custom", {})

    # Get timestamp and convert to nanoseconds
    start_timestamp = attrs.get("start_timestamp", "")
    # Check for duration in both custom and direct attributes
    duration_ns = custom.get("duration", 0) or attrs.get("duration", 0)

    # Check for start time in nanoseconds directly first
    start_ns = attrs.get("start", 0)

    # If not found, try to parse from timestamp string
    if not start_ns and start_timestamp:
        try:
            dt = datetime.fromisoformat(start_timestamp.replace("Z", "+00:00"))
            start_ns = int(dt.timestamp() * 1_000_000_000)
        except (ValueError, TypeError):
            start_ns = 0

    return start_ns, duration_ns


def format_traces_list(spans: List[Dict[str, Any]], limit: int = 50) -> str:
    """
    Format a list of spans grouped by trace ID into a readable output.
    """
    if not spans:
        return ""

    # Group spans by trace_id
    traces = defaultdict(list)
    for span in spans:
        trace_id = span.get("attributes", {}).get("trace_id")
        if trace_id:
            traces[trace_id].append(span)

    # Format output
    output_lines = []
    output_lines.append(f"Found {len(traces)} traces with matching spans")
    output_lines.append("")

    for trace_id, trace_spans in list(traces.items())[:limit]:
        # Find root span and calculate trace duration
        root_span = None
        min_start = float("inf")
        max_end = 0

        for span in trace_spans:
            attrs = span.get("attributes", {})
            start_ns, duration_ns = parse_datadog_span_timestamp(attrs)
            end_ns = start_ns + duration_ns

            if start_ns > 0 and start_ns < min_start:
                min_start = start_ns

            if end_ns > max_end:
                max_end = end_ns

            # Check if this is a root span (no parent_id)
            if not attrs.get("parent_id"):
                root_span = span

        # If no root span found, use the first span
        if not root_span and trace_spans:
            root_span = trace_spans[0]

        # Calculate duration, handling edge cases
        if min_start == float("inf") or max_end == 0:
            trace_duration_ms = 0.0
        else:
            trace_duration_ms = (max_end - min_start) / 1_000_000

        if root_span:
            attrs = root_span.get("attributes", {})
            service_name = attrs.get("service", "unknown")
            operation_name = attrs.get("operation_name", "unknown")
            start_time_str = (
                unix_nano_to_rfc3339(min_start)
                if min_start != float("inf")
                else "unknown"
            )

            output_lines.append(
                f"Trace (traceID={trace_id}) (durationMs={trace_duration_ms:.2f})"
            )
            output_lines.append(
                f"\tstartTime={start_time_str} rootServiceName={service_name} rootTraceName={operation_name}"
            )

    return "\n".join(output_lines)


def build_span_hierarchy(
    spans: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict], List[Dict]]:
    """
    Build a hierarchy of spans from a flat list.

    Returns:
        Tuple of (span_map, root_spans)
    """
    span_map = {}
    root_spans = []

    # First pass: create span objects
    for span_data in spans:
        attrs = span_data.get("attributes", {})
        span_id = attrs.get("span_id", "")
        parent_id = attrs.get("parent_id", "")

        start_ns, duration_ns = parse_datadog_span_timestamp(attrs)

        span_obj = {
            "span_id": span_id,
            "parent_id": parent_id,
            "name": attrs.get("operation_name", "unknown"),
            "service": attrs.get("service", "unknown"),
            "resource": attrs.get("resource_name", ""),
            "start_ns": start_ns,
            "duration_ns": duration_ns,
            "status": attrs.get("status", ""),
            "tags": attrs.get("tags", []),
            "children": [],
            "attributes": attrs,
        }

        span_map[span_id] = span_obj

        if not parent_id:
            root_spans.append(span_obj)

    # Second pass: build hierarchy
    for span_obj in span_map.values():
        parent_id = span_obj["parent_id"]
        if parent_id and parent_id in span_map:
            span_map[parent_id]["children"].append(span_obj)
        elif parent_id and parent_id not in span_map:
            # This is an orphaned span (parent not in trace)
            root_spans.append(span_obj)

    return span_map, root_spans


def format_trace_hierarchy(trace_id: str, spans: List[Dict[str, Any]]) -> str:
    """
    Format a trace with its full span hierarchy.
    """
    if not spans:
        return ""

    span_map, root_spans = build_span_hierarchy(spans)

    # Format output
    output_lines = []
    output_lines.append(f"Trace ID: {trace_id}")
    output_lines.append("")

    def format_span_tree(span: Dict[str, Any], level: int = 0) -> None:
        indent = "  " * level
        duration_ms = span["duration_ns"] / 1_000_000

        output_lines.append(
            f"{indent}├─ {span['name']} ({span['service']}) - {duration_ms:.2f}ms (span_id={span['span_id']})"
        )

        start_time_str = unix_nano_to_rfc3339(span["start_ns"])
        end_time_ns = span["start_ns"] + span["duration_ns"]
        end_time_str = unix_nano_to_rfc3339(end_time_ns)

        output_lines.append(
            f"{indent}│  Datetime: start={start_time_str} end={end_time_str}"
        )

        if span["resource"]:
            output_lines.append(f"{indent}│  Resource: {span['resource']}")

        if span["status"]:
            output_lines.append(f"{indent}│  Status: {span['status']}")

        # Show important tags
        important_tags = [
            "env",
            "version",
            "http.method",
            "http.status_code",
            "error.type",
            "error.message",
        ]
        tags_to_show = {}

        for tag in span["tags"]:
            if isinstance(tag, str) and ":" in tag:
                key, value = tag.split(":", 1)
                if key in important_tags:
                    tags_to_show[key] = value

        if tags_to_show:
            output_lines.append(f"{indent}│  Tags:")
            for key, value in tags_to_show.items():
                output_lines.append(f"{indent}│    {key}: {value}")

        # Sort children by start time
        sorted_children = sorted(span["children"], key=lambda s: s["start_ns"])
        for child in sorted_children:
            format_span_tree(child, level + 1)

    # Format all root spans
    for root_span in sorted(root_spans, key=lambda s: s["start_ns"]):
        format_span_tree(root_span)

    return "\n".join(output_lines)


def format_spans_search(
    spans: List[Dict[str, Any]], max_traces: int = 50, max_spans_per_trace: int = 10
) -> str:
    """
    Format spans search results grouped by trace.
    """
    if not spans:
        return ""

    # Format output
    output_lines = []
    output_lines.append(f"Found {len(spans)} matching spans")
    output_lines.append("")

    # Group spans by trace for better readability
    spans_by_trace = defaultdict(list)
    for span in spans:
        trace_id = span.get("attributes", {}).get("trace_id", "unknown")
        spans_by_trace[trace_id].append(span)

    output_lines.append(f"Spans grouped by {len(spans_by_trace)} traces:")
    output_lines.append("")

    for trace_id, trace_spans in list(spans_by_trace.items())[:max_traces]:
        output_lines.append(f"Trace ID: {trace_id}")

        # Sort spans by timestamp within each trace
        sorted_spans = sorted(
            trace_spans,
            key=lambda s: parse_datadog_span_timestamp(s.get("attributes", {}))[0],
        )

        for span in sorted_spans[:max_spans_per_trace]:
            attrs = span.get("attributes", {})

            span_id = attrs.get("span_id", "unknown")
            service = attrs.get("service", "unknown")
            operation = attrs.get("operation_name", "unknown")
            resource = attrs.get("resource_name", "")

            start_ns, duration_ns = parse_datadog_span_timestamp(attrs)
            duration_ms = duration_ns / 1_000_000
            start_time_str = unix_nano_to_rfc3339(start_ns)

            output_lines.append(f"  ├─ {operation} ({service}) - {duration_ms:.2f}ms")
            output_lines.append(f"  │  span_id: {span_id}")
            output_lines.append(f"  │  time: {start_time_str}")

            if resource:
                output_lines.append(f"  │  resource: {resource}")

            # Show status if error
            status = attrs.get("status", "")
            if status and status != "ok":
                output_lines.append(f"  │  status: {status}")

            # Check for custom error information
            custom = attrs.get("custom") or {}
            if not isinstance(custom, dict):
                custom = {}
            error_info = custom.get("error")
            if isinstance(error_info, dict) and error_info:
                output_lines.append("  │  error:")

                # Extract error fields
                error_id = error_info.get("id")
                error_file = error_info.get("file")
                error_stack = error_info.get("stack")
                error_message = error_info.get("message")
                error_type = error_info.get("type")

                if error_id:
                    output_lines.append(f"  │    id: {error_id}")
                if error_file:
                    output_lines.append(f"  │    file: {error_file}")
                if error_message:
                    output_lines.append(f"  │    message: {error_message}")
                if error_type:
                    output_lines.append(f"  │    type: {error_type}")
                if error_stack:
                    # Truncate stack trace for readability and support list inputs
                    if isinstance(error_stack, list):
                        lines = [str(x) for x in error_stack]
                    else:
                        lines = str(error_stack).splitlines()
                    stack_lines = lines[:5]
                    output_lines.append(f"  │    stack: {stack_lines[0]}")
                    for stack_line in stack_lines[1:]:
                        output_lines.append(f"  │           {stack_line}")
                    if len(lines) > 5:
                        output_lines.append("  │           ... (truncated)")

            # Show important tags
            tags = attrs.get("tags", [])
            important_tags = {}
            for tag in tags:
                if isinstance(tag, str) and ":" in tag:
                    key, value = tag.split(":", 1)
                    if key in ["env", "version", "http.status_code", "error.type"]:
                        important_tags[key] = value

            if important_tags:
                tags_str = ", ".join([f"{k}={v}" for k, v in important_tags.items()])
                output_lines.append(f"  │  tags: {tags_str}")

            output_lines.append("  │")

        if len(trace_spans) > max_spans_per_trace:
            output_lines.append(
                f"  └─ ... and {len(trace_spans) - max_spans_per_trace} more spans in this trace"
            )

        output_lines.append("")

    if len(spans_by_trace) > max_traces:
        output_lines.append(f"... and {len(spans_by_trace) - max_traces} more traces")

    return "\n".join(output_lines)

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import base64
from holmes.plugins.toolsets.utils import unix_nano_to_rfc3339


@dataclass
class Span:
    span_id: str
    parent_span_id: Optional[str]
    name: str
    service_name: str
    start_time: int
    end_time: int
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    children: List["Span"] = field(default_factory=list)
    resource_attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        """Calculate duration in milliseconds"""
        return (
            self.end_time - self.start_time
        ) / 1_000_000  # Convert nanoseconds to milliseconds


def decode_id(encoded_id: str) -> str:
    """Decode base64 IDs to a hex string for easier reading"""
    return base64.b64decode(encoded_id).hex()


def build_span_hierarchy(trace_data: Dict) -> List[Span]:
    # Step 1: Extract all spans and create span objects
    all_spans = {}

    for batch in trace_data["batches"]:
        # Extract service name and other resource attributes
        service_name = "unknown"
        resource_attributes = {}

        for attr in batch["resource"]["attributes"]:
            key = attr["key"]
            value = list(attr["value"].values())[0]
            resource_attributes[key] = value

            if key == "service.name":
                service_name = value

        for scope_spans in batch["scopeSpans"]:
            for span_data in scope_spans["spans"]:
                span_id = decode_id(span_data["spanId"])

                # Get parent span ID if it exists
                parent_span_id = None
                if "parentSpanId" in span_data:
                    parent_span_id = decode_id(span_data["parentSpanId"])

                # Create a Span object
                span = Span(
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    name=span_data["name"],
                    service_name=service_name,
                    start_time=int(span_data["startTimeUnixNano"]),
                    end_time=int(span_data["endTimeUnixNano"]),
                    attributes={
                        attr["key"]: list(attr["value"].values())[0]
                        for attr in span_data.get("attributes", [])
                    },
                    events=span_data.get("events", []),
                    resource_attributes=resource_attributes,
                )

                all_spans[span_id] = span

    # Step 2: Build the hierarchy by connecting parents and children
    root_spans = []

    for span in all_spans.values():
        if span.parent_span_id is None:
            # This is a root span
            root_spans.append(span)
        else:
            # Add this span as a child to its parent
            if span.parent_span_id in all_spans:
                all_spans[span.parent_span_id].children.append(span)

    return root_spans


def format_labels(attributes, key_labels):
    """Format the specified labels from attributes into a string"""
    result = []
    for key in key_labels:
        if key in attributes:
            result.append(f"{key}='{attributes[key]}'")

    return " ".join(result) if result else None


def format_span_tree(span: Span, level: int = 0, key_labels: List[str] = []) -> str:
    """Format the span hierarchy in a tree format with timestamps and labels"""
    span_tree_text = ""
    indent = "  " * level
    duration = span.duration_ms

    span_tree_text += f"{indent}├─ {span.name} ({span.service_name}) - {duration:.2f}ms (span_id={span.span_id})\n"

    start_time_str = unix_nano_to_rfc3339(span.start_time)
    end_time_str = unix_nano_to_rfc3339(span.end_time)
    span_tree_text += (
        f"{indent}│  Datetime: start={start_time_str} end={end_time_str}\n"
    )

    if key_labels and span.resource_attributes:
        resource_labels = format_labels(span.resource_attributes, key_labels)
        if resource_labels:
            span_tree_text += f"{indent}│  Resource labels: {resource_labels}\n"

    if span.attributes:
        span_tree_text += f"{indent}│  Attributes:\n"
        for key, value in span.attributes.items():
            span_tree_text += f"{indent}│    {key}: {value}\n"

        if key_labels:
            span_labels = format_labels(span.attributes, key_labels)
            if span_labels:
                span_tree_text += f"{indent}│  Span labels: {span_labels}\n"

    if span.events:
        span_tree_text += f"{indent}│  Events:\n"
        for event in span.events:
            event_name = event["name"]
            event_time = int(event["timeUnixNano"])
            event_time_str = unix_nano_to_rfc3339(event_time)
            relative_time = (event_time - span.start_time) / 1_000_000
            span_tree_text += f"{indent}│    {event_name} (+{relative_time:.2f}ms) at {event_time_str}\n"

            if "attributes" in event and event["attributes"]:
                for attr in event["attributes"]:
                    attr_key = attr["key"]
                    values = list(attr["value"].values())
                    if len(values) == 1:
                        span_tree_text += (
                            f"{indent}│      {attr_key}: {str(values[0])}\n"
                        )
                    elif values:
                        span_tree_text += f"{indent}│      {attr_key}: {str(values)}\n"

    for child in sorted(span.children, key=lambda s: s.start_time):
        span_tree_text += format_span_tree(child, level + 1, key_labels)

    return span_tree_text


def process_trace(
    trace_data: Dict,
    key_labels: List[str] = [
        "service.name",
        "service.version",
        "k8s.deployment.name",
        "k8s.node.name",
        "k8s.pod.name",
        "k8s.namespace.name",
    ],
) -> str:
    root_spans = build_span_hierarchy(trace_data)

    span_trees = []
    for root_span in sorted(root_spans, key=lambda s: s.start_time):
        span_trees.append(format_span_tree(root_span, key_labels=key_labels))

    return "\n\n".join(span_trees)


def format_traces_list(trace_data: Dict) -> str:
    traces = trace_data.get("traces", [])

    if len(traces) > 0:
        traces_str = []
        for trace in traces:
            trace_str = f"Trace (traceID={trace.get('traceID')})"
            trace_str += (
                f" (durationMs={trace.get('durationMs')})\n"
                if trace.get("durationMs") is not None
                else "\n"
            )
            trace_str += f"\tstartTime={unix_nano_to_rfc3339(int(trace.get('startTimeUnixNano')))}"
            trace_str += f" rootServiceName={trace.get('rootServiceName')}"
            trace_str += f" rootTraceName={trace.get('rootTraceName')}"
            traces_str.append(trace_str)
        return "\n".join(traces_str)
    else:
        return "No matching trace could be found"

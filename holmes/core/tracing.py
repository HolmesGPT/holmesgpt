import getpass
import logging
import os
import platform
import socket
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from opentelemetry import trace as otel_trace

BRAINTRUST_API_KEY = os.environ.get("BRAINTRUST_API_KEY")
BRAINTRUST_ORG = os.environ.get("BRAINTRUST_ORG", "robustadev")
BRAINTRUST_PROJECT = os.environ.get(
    "BRAINTRUST_PROJECT", "HolmesGPT"
)  # only for evals - for CLI it's set differently

try:
    import braintrust
    from braintrust import Span, SpanTypeAttribute

    logging.info("Braintrust package imported successfully")
    BRAINTRUST_AVAILABLE = True
except ImportError:
    BRAINTRUST_AVAILABLE = False
    # Type aliases for when braintrust is not available
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from braintrust import Span, SpanTypeAttribute
    else:
        Span = Any
        SpanTypeAttribute = Any


session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")


def readable_timestamp():
    return session_timestamp


def get_active_branch_name():
    try:
        # First check GitHub Actions environment variables (CI)
        github_head_ref = os.environ.get("GITHUB_HEAD_REF")  # Set for PRs
        if github_head_ref:
            return github_head_ref

        github_ref = os.environ.get(
            "GITHUB_REF", ""
        )  # Set for pushes: refs/heads/branch-name
        if github_ref.startswith("refs/heads/"):
            return github_ref.replace("refs/heads/", "")

        # Check if .git is a file (worktree case)
        git_path = Path(".git")
        if git_path.is_file():
            # Read the worktree git directory path
            with git_path.open("r") as f:
                content = f.read().strip()
                if content.startswith("gitdir:"):
                    worktree_git_dir = Path(content.split("gitdir:", 1)[1].strip())
                    head_file = worktree_git_dir / "HEAD"
                else:
                    return "Unknown"
        else:
            # Regular .git directory
            head_file = git_path / "HEAD"

        with head_file.open("r") as f:
            content = f.read().splitlines()
            for line in content:
                if line[0:4] == "ref:":
                    return line.partition("refs/heads/")[2]
    except Exception:
        pass

    return "Unknown"


def get_machine_state_tags() -> Dict[str, str]:
    return {
        "username": getpass.getuser(),
        "branch": get_active_branch_name(),
        "platform": platform.platform(),
        "hostname": socket.gethostname(),
    }


def get_experiment_name():
    if os.environ.get("EXPERIMENT_ID"):
        return os.environ.get("EXPERIMENT_ID")
    return readable_timestamp()  # should never happen in evals (we set EXPERIMENT_ID in conftest.py), but can happen with holmesgpt cli


def _is_noop_span(span) -> bool:
    """Check if a span is a Braintrust NoopSpan (inactive span)."""
    return span is None or str(type(span)).endswith("_NoopSpan'>")


class SpanType(Enum):
    """Standard span types for tracing categorization."""

    LLM = "llm"
    SCORE = "score"
    FUNCTION = "function"
    EVAL = "eval"
    TASK = "task"
    TOOL = "tool"


class DummySpan:
    """A no-op span implementation for when tracing is disabled."""

    def start_span(self, name: Optional[str] = None, span_type=None, **kwargs):
        return DummySpan()

    def log(self, *args, **kwargs):
        pass

    def end(self):
        pass

    def set_attributes(
        self, name: Optional[str] = None, type=None, span_attributes=None
    ) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class DummyTracer:
    """A no-op tracer implementation for when tracing is disabled."""

    def start_experiment(self, experiment_name=None, additional_metadata=None):
        """No-op experiment creation."""
        return None

    def start_trace(self, name: str, span_type=None):
        """No-op trace creation."""
        return DummySpan()

    def get_trace_url(self):
        return None

    def wrap_llm(self, llm_module):
        """No-op LLM wrapping for dummy tracer."""
        return llm_module


class BraintrustTracer:
    """Braintrust implementation of tracing."""

    def __init__(self, project: str):
        if not BRAINTRUST_AVAILABLE:
            raise ImportError("braintrust package is required for BraintrustTracer")

        self.project = project

    def start_experiment(
        self,
        experiment_name: Optional[str] = None,
        additional_metadata: Optional[dict] = None,
    ):
        """Create and start a new Braintrust experiment.

        Args:
            experiment_name: Name for the experiment, auto-generated if None
            metadata: Metadata to attach to experiment

        Returns:
            Braintrust experiment object
        """
        if not os.environ.get("BRAINTRUST_API_KEY"):
            return None

        if experiment_name is None:
            experiment_name = get_experiment_name()

        metadata = get_machine_state_tags()
        if additional_metadata is not None:
            metadata.update(additional_metadata)

        return braintrust.init(
            project=self.project,
            experiment=experiment_name,
            metadata=metadata,
            update=True,
        )

    def start_trace(
        self, name: str, span_type: Optional[SpanType] = None
    ) -> Union[Span, DummySpan]:
        """Start a trace span in current Braintrust context.

        Args:
            name: Span name
            span_type: Type of span for categorization

        Returns:
            Span that can be used as context manager
        """
        if not os.environ.get("BRAINTRUST_API_KEY"):
            return DummySpan()

        # Add span type to kwargs if provided
        kwargs = {}
        if span_type:
            kwargs["type"] = span_type.value

        # Use current Braintrust context (experiment or parent span)
        current_span = braintrust.current_span()
        if not _is_noop_span(current_span):
            return current_span.start_span(name=name, **kwargs)  # type: ignore

        # Fallback to current experiment
        current_experiment = braintrust.current_experiment()
        if current_experiment:
            return current_experiment.start_span(name=name, **kwargs)  # type: ignore

        return DummySpan()

    def get_trace_url(self) -> Optional[str]:
        """Get URL to view the trace in Braintrust."""
        logging.info("Getting trace URL for Braintrust")
        if not os.environ.get("BRAINTRUST_API_KEY"):
            logging.warning("BRAINTRUST_API_KEY not set, cannot get trace URL")
            return None

        current_experiment = braintrust.current_experiment()
        if not current_experiment:
            logging.warning("No current experiment found in Braintrust context")
            return None

        experiment_name = getattr(current_experiment, "name", None)
        if not experiment_name:
            logging.warning("No experiment name found in current Braintrust context")
            return None

        current_span = braintrust.current_span()
        if not _is_noop_span(current_span):
            current_span.link()
        else:
            logging.warning("No active span found in Braintrust context")

        return f"https://www.braintrust.dev/app/{BRAINTRUST_ORG}/p/{self.project}/experiments/{experiment_name}"

    def wrap_llm(self, llm_module):
        """Wrap LiteLLM with Braintrust tracing if in active context, otherwise return unwrapped."""
        if not BRAINTRUST_AVAILABLE or not os.environ.get("BRAINTRUST_API_KEY"):
            return llm_module

        from braintrust.oai import ChatCompletionWrapper

        class WrappedLiteLLM:
            def __init__(self, original_module):
                self._original_module = original_module
                self._chat_wrapper = ChatCompletionWrapper(
                    create_fn=original_module.completion,
                    acreate_fn=None,
                )

            def completion(self, **kwargs):
                return self._chat_wrapper.create(**kwargs)

            def __getattr__(self, name):
                return getattr(self._original_module, name)

        return WrappedLiteLLM(llm_module)


# Mapping from SpanType to OTEL Gen AI semantic convention span names
SPAN_TYPE_TO_OTEL = {
    SpanType.LLM: "chat",
    SpanType.TOOL: "execute_tool",
    SpanType.TASK: "invoke_agent",
    SpanType.FUNCTION: "execute_tool",
    SpanType.EVAL: "invoke_agent",
    SpanType.SCORE: "score",
}


class OTELSpan:
    """Wrapper around OTEL Span that implements Braintrust-compatible interface."""

    def __init__(self, span: otel_trace.Span, tracer: "OTELTracer"):
        self._span = span
        self._tracer = tracer
        self._context = otel_trace.set_span_in_context(span)

    def start_span(
        self, name: Optional[str] = None, span_type: Optional[SpanType] = None, **kwargs
    ) -> "OTELSpan":
        """Create a child span."""
        otel_name = self._tracer._get_otel_span_name(name or "", span_type)
        child_span = self._tracer._native_tracer.start_span(
            otel_name, context=self._context
        )
        return OTELSpan(child_span, self._tracer)

    def log(
        self,
        input: Optional[Any] = None,
        output: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> None:
        """Log data to span as attributes (Braintrust compatibility)."""
        from experimental.otel.attributes import truncate

        if input is not None:
            self._span.set_attribute("gen_ai.prompt", truncate(str(input)))
        if output is not None:
            self._span.set_attribute("gen_ai.completion", truncate(str(output)))
        if metadata:
            for key, value in metadata.items():
                self._span.set_attribute(f"metadata.{key}", str(value))

    def set_attributes(
        self,
        name: Optional[str] = None,
        type: Optional[str] = None,
        span_attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Set span attributes."""
        if name:
            self._span.set_attribute("span.name", name)
        if span_attributes:
            for key, value in span_attributes.items():
                self._span.set_attribute(key, str(value) if value is not None else "")

    def end(self) -> None:
        """End the span."""
        self._span.end()

    def __enter__(self) -> "OTELSpan":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_val:
            from experimental.otel.tracing import set_span_error

            set_span_error(self._span, exc_val)
        self.end()


class OTELTracer:
    """OpenTelemetry implementation of tracing."""

    def __init__(self, service_name: str = "holmesgpt"):
        self._service_name = service_name
        self._initialized = False
        self._native_tracer: Optional[otel_trace.Tracer] = None

    def _ensure_initialized(self) -> None:
        """Lazy initialization of OTEL tracer."""
        if not self._initialized:
            from experimental.otel.tracing import get_tracer, init_otel_tracer

            init_otel_tracer()
            self._native_tracer = get_tracer(self._service_name)
            self._initialized = True

    def _get_otel_span_name(self, name: str, span_type: Optional[SpanType]) -> str:
        """Convert span name to Gen AI semantic convention format."""
        if span_type:
            prefix = SPAN_TYPE_TO_OTEL.get(span_type, "")
            if prefix:
                return f"{prefix} {name}" if name else prefix
        return name

    def start_experiment(
        self,
        experiment_name: Optional[str] = None,
        additional_metadata: Optional[dict] = None,
    ):
        """No-op for OTEL - experiments are a Braintrust concept.

        OTEL uses traces, not experiments. This method exists for API compatibility.
        Returns None to indicate no experiment context.
        """
        self._ensure_initialized()
        return None

    def start_trace(
        self, name: str, span_type: Optional[SpanType] = None
    ) -> Union["OTELSpan", DummySpan]:
        """Start a root trace span.

        Args:
            name: Human-readable span name
            span_type: Type of span for Gen AI semantic conventions

        Returns:
            OTELSpan that can be used as context manager
        """
        self._ensure_initialized()

        if not self._native_tracer:
            return DummySpan()

        otel_name = self._get_otel_span_name(name, span_type)
        span = self._native_tracer.start_span(otel_name)

        # Set Gen AI attributes based on span type
        if span_type:
            operation_name = SPAN_TYPE_TO_OTEL.get(span_type, "unknown")
            span.set_attribute("gen_ai.operation.name", operation_name)

        return OTELSpan(span, self)

    def get_trace_url(self) -> Optional[str]:
        """OTEL doesn't have a direct trace URL - depends on backend.

        Returns None. Users should check their observability backend
        (OpenSearch, Jaeger, etc.) for trace visualization.
        """
        return None

    def wrap_llm(self, llm_module):
        """No automatic LLM wrapping for OTEL.

        OTEL instrumentation happens via manual spans, not automatic wrapping.
        For automatic LiteLLM instrumentation, use OpenTelemetry's litellm
        instrumentation separately.
        """
        return llm_module


class CompositeSpan:
    """Span that delegates to multiple underlying spans."""

    def __init__(self, spans: List[Union[OTELSpan, DummySpan, Any]]):
        self._spans = spans

    def start_span(
        self, name: Optional[str] = None, span_type: Optional[SpanType] = None, **kwargs
    ) -> "CompositeSpan":
        """Create child spans on all underlying spans."""
        child_spans = [s.start_span(name, span_type, **kwargs) for s in self._spans]
        return CompositeSpan(child_spans)

    def log(self, *args, **kwargs) -> None:
        """Log to all underlying spans."""
        for span in self._spans:
            span.log(*args, **kwargs)

    def set_attributes(self, **kwargs) -> None:
        """Set attributes on all underlying spans."""
        for span in self._spans:
            span.set_attributes(**kwargs)

    def end(self) -> None:
        """End all underlying spans."""
        for span in self._spans:
            span.end()

    def __enter__(self) -> "CompositeSpan":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        for span in self._spans:
            span.__exit__(exc_type, exc_val, exc_tb)


class CompositeTracer:
    """Tracer that delegates to multiple tracers for dual tracing."""

    def __init__(self, tracers: List[Union[OTELTracer, "BraintrustTracer", DummyTracer]]):
        self._tracers = tracers

    def start_experiment(
        self,
        experiment_name: Optional[str] = None,
        additional_metadata: Optional[dict] = None,
    ):
        """Start experiment on all tracers that support it."""
        for tracer in self._tracers:
            tracer.start_experiment(experiment_name, additional_metadata)

    def start_trace(
        self, name: str, span_type: Optional[SpanType] = None
    ) -> CompositeSpan:
        """Start traces on all underlying tracers."""
        spans = [tracer.start_trace(name, span_type) for tracer in self._tracers]
        return CompositeSpan(spans)

    def get_trace_url(self) -> Optional[str]:
        """Get trace URL from the first tracer that has one."""
        for tracer in self._tracers:
            url = tracer.get_trace_url()
            if url:
                return url
        return None

    def wrap_llm(self, llm_module):
        """Wrap LLM with all tracers that support it."""
        result = llm_module
        for tracer in self._tracers:
            result = tracer.wrap_llm(result)
        return result


class TracingFactory:
    """Factory for creating tracer instances."""

    _otel_initialized = False

    @staticmethod
    def init_otel() -> bool:
        """Early OTEL initialization for servers.

        Call at server startup for optimal performance.
        Returns True if initialization succeeded.
        """
        if TracingFactory._otel_initialized:
            return True
        try:
            from experimental.otel.tracing import init_otel_tracer

            result = init_otel_tracer()
            TracingFactory._otel_initialized = result
            return result
        except Exception as e:
            logging.warning(f"Failed to initialize OTEL: {e}")
            return False

    @staticmethod
    def _create_single_tracer(
        trace_type: str, project: str = BRAINTRUST_PROJECT
    ) -> Union[OTELTracer, "BraintrustTracer", DummyTracer]:
        """Create a single tracer instance based on the trace type."""
        trace_type_lower = trace_type.lower().strip()

        if trace_type_lower == "braintrust":
            if not BRAINTRUST_AVAILABLE:
                logging.warning(
                    "Braintrust tracing requested but braintrust package not available"
                )
                return DummyTracer()

            if not os.environ.get("BRAINTRUST_API_KEY"):
                logging.warning(
                    "Braintrust tracing requested but BRAINTRUST_API_KEY not set"
                )
                return DummyTracer()

            return BraintrustTracer(project=project)

        elif trace_type_lower == "otel":
            if not os.environ.get("OTEL_ENABLED", "").lower() == "true":
                logging.warning(
                    "OTEL tracing requested but OTEL_ENABLED not set to 'true'"
                )
                return DummyTracer()
            return OTELTracer()

        logging.warning(f"Unknown trace type: {trace_type}")
        return DummyTracer()

    @staticmethod
    def create_tracer(trace_type: Optional[str], project: str = BRAINTRUST_PROJECT):
        """Create a tracer instance based on the trace type.

        Args:
            trace_type: Type of tracing. Can be:
                - 'braintrust': For evaluations and experiments
                - 'otel': For production observability (requires OTEL_ENABLED=true)
                - 'braintrust,otel': For dual tracing (both systems)
                - None: Returns DummyTracer
            project: Project name for Braintrust tracing

        Returns:
            Tracer instance if tracing enabled, DummyTracer if disabled
        """
        if not trace_type:
            return DummyTracer()

        # Support multiple tracers: "braintrust,otel"
        if "," in trace_type:
            tracers = [
                TracingFactory._create_single_tracer(t.strip(), project)
                for t in trace_type.split(",")
            ]
            # Filter out DummyTracers
            active_tracers = [t for t in tracers if not isinstance(t, DummyTracer)]
            if not active_tracers:
                return DummyTracer()
            if len(active_tracers) == 1:
                return active_tracers[0]
            return CompositeTracer(active_tracers)

        return TracingFactory._create_single_tracer(trace_type, project)

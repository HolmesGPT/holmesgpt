"""
Label-routed Prometheus toolset.

For multi-tenant Prometheus-compatible backends (e.g. Grafana Mimir) where a
single base URL serves many tenants and the tenant is selected per request by
an HTTP header, and the tenant to query is the value of a Kubernetes resource
label (e.g. a `productline` label). Every API call is sent as:

    {prometheus_url}api/v1/...    with header    {routing_header}: {label_value}

e.g. `X-Scope-OrgID: corporate` for Mimir.

This is NOT a general-purpose Prometheus toolset. For a single, static
Prometheus instance, use `prometheus/metrics` instead. This toolset requires
the LLM to determine the routing label's value (e.g. from a kubernetes tool)
and pass it explicitly as `label_value` on every call. There is no default
tenant to fall back to.

Implementation note: this subclasses the real `prometheus/metrics` tools
(query building, timeouts, token-based truncation, SSL error handling all
stay inherited and unmodified) and only overrides the request headers, via
the `BasePrometheusTool._get_headers()` seam in `prometheus.py`. Bug
fixes/improvements to the underlying tools apply here automatically.
"""

import os
from typing import Any, ClassVar, Dict, Optional, Tuple, Type

from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.prometheus.prometheus import (
    ExecuteInstantQuery,
    ExecuteRangeQuery,
    GetAllLabels,
    GetLabelValues,
    GetMetricMetadata,
    GetMetricNames,
    GetSeries,
    ListPrometheusRules,
    PrometheusConfig,
    PrometheusToolset,
)
from holmes.plugins.prompts import load_and_render_prompt


class LabelRoutedPrometheusConfig(PrometheusConfig):
    """Configuration for a multi-tenant Prometheus-compatible backend whose
    tenant is selected per request by a header, from a Kubernetes resource
    label value."""

    _name: ClassVar[Optional[str]] = "Prometheus (label-routed)"
    _description: ClassVar[Optional[str]] = (
        "Connect to a multi-tenant Prometheus-compatible backend (e.g. Mimir) that "
        "selects the tenant per request from a Kubernetes resource label value."
    )
    _docs_anchor: ClassVar[Optional[str]] = (
        "label-routed-prometheus-multi-tenant-gateway"
    )
    # Not a `prometheus/metrics` variant: don't inherit its "prometheus" subtype.
    _subtype: ClassVar[Optional[str]] = None

    prometheus_url: str = Field(  # type: ignore[assignment]
        title="URL",
        description=(
            "Base URL of the Prometheus-compatible API, the same for every tenant. "
            "The tenant is sent in the routing header, not in the URL."
        ),
        examples=["http://mimir.monitoring.svc.cluster.local/prometheus"],
    )
    label_key: str = Field(
        default="productline",
        title="Routing Label",
        description=(
            "Name of the Kubernetes label whose value selects the tenant. "
            "Used only to generate LLM-facing instructions telling it which label to look up "
            "before calling this toolset's tools. It does not change any tool parameter name "
            "(the tool parameter is always `label_value`)."
        ),
        examples=["productline"],
    )
    routing_header: str = Field(
        default="X-Scope-OrgID",
        title="Routing Header",
        description=(
            "HTTP header that carries `label_value` on every request. Overrides a header "
            "of the same name in `additional_headers`."
        ),
        examples=["X-Scope-OrgID"],
    )


def _label_value_param() -> ToolParameter:
    return ToolParameter(
        description=(
            "Value of the Kubernetes routing label for the resource being investigated "
            "(see this toolset's instructions for which label to look up first, e.g. via a "
            "kubernetes tool). Selects which tenant's metrics this call reads. "
            "Do not guess or fabricate this value."
        ),
        type="string",
        required=True,
    )


def _missing_label_value_error(config: LabelRoutedPrometheusConfig) -> str:
    return (
        "'label_value' parameter is required and was missing or empty. Before calling "
        f"this tool, determine the value of the Kubernetes label '{config.label_key}' on the "
        "resource being investigated (check the resource itself, e.g. Pod/Deployment, "
        "falling back to its Namespace if not present there) using a kubernetes tool, "
        "then retry this call passing that value as 'label_value'. If the resource has "
        f"no '{config.label_key}' label at all, report to the user that this query cannot be "
        "routed rather than guessing a value."
    )


class LabelRoutedHeaderMixin:
    """Adds a required `label_value` parameter to a `prometheus/metrics` tool
    and sends it in the configured routing header on every request.
    Everything else (URL, query building, response formatting, error
    handling) stays inherited."""

    def __init__(self, toolset):
        super().__init__(toolset)  # type: ignore[call-arg]
        self.parameters["label_value"] = _label_value_param()  # type: ignore[attr-defined]
        # Prefix the name so this can never collide with the stock
        # prometheus/metrics tools if both toolsets were accidentally enabled
        # in the same deployment.
        self.name = f"label_routed_{self.name}"  # type: ignore[attr-defined]

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        config = self.toolset.config  # type: ignore[attr-defined]
        label_value = str(params.get("label_value") or "").strip()
        if config is not None and not label_value:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=_missing_label_value_error(config),
                params=params,
            )
        return super()._invoke({**params, "label_value": label_value}, context)  # type: ignore[misc]

    def _get_headers(self, params: dict) -> Dict[str, str]:
        config: LabelRoutedPrometheusConfig = self.toolset.config  # type: ignore[attr-defined]
        return {
            **config.additional_headers,
            config.routing_header: str(params["label_value"]),
        }


class LabelRoutedListPrometheusRules(LabelRoutedHeaderMixin, ListPrometheusRules):
    pass


class LabelRoutedGetMetricNames(LabelRoutedHeaderMixin, GetMetricNames):
    pass


class LabelRoutedGetLabelValues(LabelRoutedHeaderMixin, GetLabelValues):
    pass


class LabelRoutedGetAllLabels(LabelRoutedHeaderMixin, GetAllLabels):
    pass


class LabelRoutedGetSeries(LabelRoutedHeaderMixin, GetSeries):
    pass


class LabelRoutedGetMetricMetadata(LabelRoutedHeaderMixin, GetMetricMetadata):
    pass


class LabelRoutedExecuteInstantQuery(LabelRoutedHeaderMixin, ExecuteInstantQuery):
    pass


class LabelRoutedExecuteRangeQuery(LabelRoutedHeaderMixin, ExecuteRangeQuery):
    pass


# `BasePrometheusTool.toolset: "PrometheusToolset"` is a forward reference that
# pydantic resolves lazily. Cross-module subclassing needs an explicit rebuild
# with that name in scope, or instantiating any of the classes above raises
# "class not fully defined".
for _cls in (
    LabelRoutedListPrometheusRules,
    LabelRoutedGetMetricNames,
    LabelRoutedGetLabelValues,
    LabelRoutedGetAllLabels,
    LabelRoutedGetSeries,
    LabelRoutedGetMetricMetadata,
    LabelRoutedExecuteInstantQuery,
    LabelRoutedExecuteRangeQuery,
):
    _cls.model_rebuild(_types_namespace={"PrometheusToolset": PrometheusToolset})


class LabelRoutedPrometheusToolset(PrometheusToolset):
    """Subclasses `PrometheusToolset` only so this toolset type-checks against
    the `toolset: "PrometheusToolset"` field declared on `BasePrometheusTool`
    (pydantic requires an actual isinstance match there, not just a
    structurally similar sibling class). `__init__` intentionally skips
    `PrometheusToolset.__init__` — its tool list, name, and subtype-detection
    logic don't apply here — and calls `Toolset.__init__` directly instead."""

    config_classes: ClassVar[list[Type[LabelRoutedPrometheusConfig]]] = [
        LabelRoutedPrometheusConfig
    ]
    config: Optional[LabelRoutedPrometheusConfig] = None
    _llm_tool_prefix: ClassVar[str] = "label_routed_"

    def __init__(self):
        Toolset.__init__(
            self,
            name="prometheus/label-routed-metrics",
            description=(
                "Prometheus integration for multi-tenant backends that selects the tenant "
                "per request from a Kubernetes resource label value"
            ),
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/prometheus/",
            icon_url="https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/prometheus.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                LabelRoutedListPrometheusRules(toolset=self),
                LabelRoutedGetMetricNames(toolset=self),
                LabelRoutedGetLabelValues(toolset=self),
                LabelRoutedGetAllLabels(toolset=self),
                LabelRoutedGetSeries(toolset=self),
                LabelRoutedGetMetricMetadata(toolset=self),
                LabelRoutedExecuteInstantQuery(toolset=self),
                LabelRoutedExecuteRangeQuery(toolset=self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
        )
        self._reload_llm_instructions()

    def _reload_llm_instructions(self):
        # Tenant-routing rules first, then the stock Prometheus instructions
        # rendered with this toolset's `label_routed_` tool names.
        super()._reload_llm_instructions()
        routing = load_and_render_prompt(
            prompt=f"file://{os.path.join(os.path.dirname(os.path.abspath(__file__)), 'label_routed_prometheus_instructions.jinja2')}",
            context={"config": self.config},
        )
        self.llm_instructions = f"{routing}\n{self.llm_instructions}"

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        config = config or {}
        try:
            self.config = LabelRoutedPrometheusConfig(**config)
            self._reload_llm_instructions()
        except Exception as e:
            return False, f"Invalid label-routed Prometheus configuration: {e}"
        # No connectivity check: there is no tenant at startup, and Mimir
        # rejects tenant-less queries.
        return True, ""

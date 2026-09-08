"""
Label-routed Prometheus toolset.

For deployments where a gateway fronts a *separate* Prometheus instance per
client, keyed by a URL path segment derived from a Kubernetes resource label
(e.g. a `productline` label). Every API call is routed as:

    {prometheus_url}/prometheus/{label_value}/api/v1/...

This is NOT a general-purpose Prometheus toolset. For a single, static
Prometheus instance, use `prometheus/metrics` instead. This toolset requires
the LLM to determine the routing label's value (e.g. from a kubernetes tool)
and pass it explicitly as `label_value` on every call — there is no default
backend to fall back to.

Implementation note: this subclasses the real `prometheus/metrics` tools
(query building, timeouts, token-based truncation, SSL error handling all
stay inherited and unmodified) and only overrides how the request's base URL
is computed, via the `BasePrometheusTool._get_base_url()` seam in
`prometheus.py`. Bug fixes/improvements to the underlying tools apply here
automatically.
"""

import os
from typing import Any, ClassVar, Optional, Tuple, Type
from urllib.parse import quote, urljoin

from pydantic import Field

from holmes.core.tools import CallablePrerequisite, ToolParameter, Toolset, ToolsetTag
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


class LabelRoutedPrometheusConfig(PrometheusConfig):
    """Configuration for a Prometheus gateway that routes each request to a
    per-client backend based on a Kubernetes resource label value."""

    _name: ClassVar[Optional[str]] = "Prometheus (label-routed)"
    _description: ClassVar[Optional[str]] = (
        "Connect to a Prometheus gateway that routes each request to a "
        "per-client backend selected by a Kubernetes resource label value."
    )
    _docs_anchor: ClassVar[Optional[str]] = (
        "label-routed-prometheus-multi-tenant-gateway"
    )

    prometheus_url: str = Field(  # type: ignore[assignment]
        title="URL",
        description=(
            "Base URL of the Prometheus gateway, WITHOUT the per-client path segment "
            "(that segment is built dynamically from `label_value` on every call)."
        ),
        examples=["http://prometheus-gateway.monitoring.svc.cluster.local:80"],
    )
    label_key: str = Field(
        default="productline",
        title="Routing Label",
        description=(
            "Name of the Kubernetes label whose value selects the per-client Prometheus path. "
            "Used only to generate LLM-facing instructions telling it which label to look up "
            "before calling this toolset's tools — it does not change any tool parameter name "
            "(the tool parameter is always `label_value`)."
        ),
        examples=["productline"],
    )


def _label_value_param() -> ToolParameter:
    return ToolParameter(
        description=(
            "Value of the Kubernetes routing label for the resource being investigated "
            "(see this toolset's instructions for which label to look up first, e.g. via a "
            "kubernetes tool). Selects which per-client Prometheus backend this call is routed to. "
            "Do not guess or fabricate this value."
        ),
        type="string",
        required=True,
    )


def _resolve_label_routed_url(config: LabelRoutedPrometheusConfig, params: dict) -> str:
    label_value = params.get("label_value")
    if not label_value:
        raise ValueError(
            "'label_value' parameter is required and was missing or empty. Before calling "
            f"this tool, determine the value of the Kubernetes label '{config.label_key}' on the "
            "resource being investigated (check the resource itself, e.g. Pod/Deployment, "
            "falling back to its Namespace if not present there) using a kubernetes tool, "
            "then retry this call passing that value as 'label_value'. If the resource has "
            f"no '{config.label_key}' label at all, report to the user that this query cannot be "
            "routed rather than guessing a value."
        )
    return urljoin(
        config.prometheus_url, f"prometheus/{quote(str(label_value), safe='')}/"
    )


class LabelRoutedURLMixin:
    """Adds a required `label_value` parameter to a `prometheus/metrics` tool
    and routes its base URL through `{prometheus_url}/prometheus/{label_value}/`
    instead of the toolset's static `prometheus_url`. Everything else (query
    building, response formatting, error handling) stays inherited."""

    def __init__(self, toolset):
        super().__init__(toolset)  # type: ignore[call-arg]
        self.parameters["label_value"] = _label_value_param()  # type: ignore[attr-defined]
        # Prefix the name so this can never collide with the stock
        # prometheus/metrics tools if both toolsets were accidentally enabled
        # in the same deployment.
        self.name = f"label_routed_{self.name}"  # type: ignore[attr-defined]

    def _get_base_url(self, params: dict) -> str:
        return _resolve_label_routed_url(self.toolset.config, params)  # type: ignore[attr-defined]


class LabelRoutedListPrometheusRules(LabelRoutedURLMixin, ListPrometheusRules):
    pass


class LabelRoutedGetMetricNames(LabelRoutedURLMixin, GetMetricNames):
    pass


class LabelRoutedGetLabelValues(LabelRoutedURLMixin, GetLabelValues):
    pass


class LabelRoutedGetAllLabels(LabelRoutedURLMixin, GetAllLabels):
    pass


class LabelRoutedGetSeries(LabelRoutedURLMixin, GetSeries):
    pass


class LabelRoutedGetMetricMetadata(LabelRoutedURLMixin, GetMetricMetadata):
    pass


class LabelRoutedExecuteInstantQuery(LabelRoutedURLMixin, ExecuteInstantQuery):
    pass


class LabelRoutedExecuteRangeQuery(LabelRoutedURLMixin, ExecuteRangeQuery):
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

    def __init__(self):
        Toolset.__init__(
            self,
            name="prometheus/label-routed-metrics",
            description=(
                "Prometheus integration that routes each request to a per-client backend "
                "selected by a Kubernetes resource label value"
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
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "label_routed_prometheus_instructions.jinja2"
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        config = config or {}
        try:
            self.config = LabelRoutedPrometheusConfig(**config)
            self._reload_llm_instructions()
            return True, ""
        except Exception as e:
            return False, f"Invalid label-routed Prometheus configuration: {e}"

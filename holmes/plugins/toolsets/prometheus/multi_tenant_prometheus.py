"""
Multi-tenant Prometheus toolset.

For multi-tenant Prometheus-compatible backends (e.g. Grafana Mimir) where a
single base URL serves many tenants and the tenant is selected per request by
an HTTP header, and the tenant to query is the value of a Kubernetes resource
label (e.g. a `productline` label). Every API call is sent as:

    {prometheus_url}api/v1/...    with header    {tenant_header}: {tenant}

e.g. `X-Scope-OrgID: corporate` for Mimir.

This is NOT a general-purpose Prometheus toolset. For a single, static
Prometheus instance, use `prometheus/metrics` instead. This toolset requires
the LLM to determine the tenant label's value (e.g. from a kubernetes tool)
and pass it explicitly as `tenant` on every call. There is no default
tenant to fall back to.

Implementation note: this subclasses the real `prometheus/metrics` tools
(query building, timeouts, token-based truncation, SSL error handling all
stay inherited and unmodified), keeping their names so UI/Slack graph
rendering still recognises them, and only adds the tenant header to each
request. Bug fixes/improvements to the underlying tools apply here
automatically. Don't enable it alongside `prometheus/metrics`: the tool names
collide.
"""

import os
from typing import Any, ClassVar, Optional, Tuple, Type

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


class MultiTenantPrometheusConfig(PrometheusConfig):
    """Configuration for a multi-tenant Prometheus-compatible backend whose
    tenant is selected per request by a header, from a Kubernetes resource
    label value."""

    _name: ClassVar[Optional[str]] = "Prometheus (multi-tenant)"
    _description: ClassVar[Optional[str]] = (
        "Connect to a multi-tenant Prometheus-compatible backend (e.g. Mimir) that "
        "selects the tenant per request from a Kubernetes resource label value."
    )
    _docs_anchor: ClassVar[Optional[str]] = "multi-tenant-prometheus"
    # Not a `prometheus/metrics` variant: don't inherit its "prometheus" subtype.
    _subtype: ClassVar[Optional[str]] = None

    prometheus_url: str = Field(  # type: ignore[assignment]
        title="URL",
        description=(
            "Base URL of the Prometheus-compatible API, the same for every tenant. "
            "The tenant is sent in the tenant header, not in the URL."
        ),
        examples=["http://mimir.monitoring.svc.cluster.local/prometheus"],
    )
    tenant_label: str = Field(
        default="productline",
        min_length=1,
        title="Tenant Label",
        description=(
            "Name of the Kubernetes label whose value selects the tenant. "
            "Used only to generate LLM-facing instructions telling it which label to look up "
            "before calling this toolset's tools. It does not change any tool parameter name "
            "(the tool parameter is always `tenant`)."
        ),
        examples=["productline"],
    )
    tenant_header: str = Field(
        default="X-Scope-OrgID",
        min_length=1,
        title="Tenant Header",
        description=(
            "HTTP header that carries `tenant` on every request. Overrides a header "
            "of the same name in `additional_headers`."
        ),
        examples=["X-Scope-OrgID"],
    )


def _tenant_param() -> ToolParameter:
    return ToolParameter(
        description=(
            "Value of the Kubernetes tenant label for the resource being investigated "
            "(see this toolset's instructions for which label to look up first, e.g. via a "
            "kubernetes tool). Selects which tenant's metrics this call reads. "
            "Do not guess or fabricate this value."
        ),
        type="string",
        required=True,
    )


def _missing_tenant_error(config: MultiTenantPrometheusConfig) -> str:
    return (
        "'tenant' parameter is required and was missing or empty. Before calling "
        f"this tool, determine the value of the Kubernetes label '{config.tenant_label}' on the "
        "resource being investigated (check the resource itself, e.g. Pod/Deployment, "
        "falling back to its Namespace if not present there) using a kubernetes tool, "
        "then retry this call passing that value as 'tenant'. If the resource has "
        f"no '{config.tenant_label}' label at all, report to the user that this query cannot be "
        "routed rather than guessing a value."
    )


class MultiTenantHeaderMixin:
    """Adds a required `tenant` parameter to a `prometheus/metrics` tool
    and sends it in the configured tenant header on every request.
    Everything else (URL, query building, response formatting, error
    handling) stays inherited."""

    def __init__(self, toolset):
        super().__init__(toolset)  # type: ignore[call-arg]
        self.parameters["tenant"] = _tenant_param()  # type: ignore[attr-defined]

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        config: Optional[MultiTenantPrometheusConfig] = self.toolset.config  # type: ignore[attr-defined]
        if config is None:
            return super()._invoke(params, context)  # type: ignore[misc]
        tenant = str(params.get("tenant") or "").strip()
        if not tenant:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=_missing_tenant_error(config),
                params=params,
            )
        if "|" in tenant:
            # Mimir reads `a|b` as a federated query across tenants a and b.
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"'tenant' must be a single tenant ID, got '{tenant}'. '|' would query "
                    "several tenants at once; call the tool once per tenant instead."
                ),
                params=params,
            )
        # The stock tools send `config.additional_headers`, so run them against a
        # per-call copy of the config that carries the tenant header. Copies,
        # not mutation: one toolset instance serves concurrent calls for
        # different tenants.
        routed_config = config.model_copy(
            update={
                "additional_headers": {
                    **config.additional_headers,
                    config.tenant_header: tenant,
                }
            }
        )
        routed_tool = self.model_copy(  # type: ignore[attr-defined]
            update={"toolset": self.toolset.model_copy(update={"config": routed_config})}  # type: ignore[attr-defined]
        )
        return super(MultiTenantHeaderMixin, routed_tool)._invoke(  # type: ignore[misc]
            {**params, "tenant": tenant}, context
        )


class MultiTenantListPrometheusRules(MultiTenantHeaderMixin, ListPrometheusRules):
    pass


class MultiTenantGetMetricNames(MultiTenantHeaderMixin, GetMetricNames):
    pass


class MultiTenantGetLabelValues(MultiTenantHeaderMixin, GetLabelValues):
    pass


class MultiTenantGetAllLabels(MultiTenantHeaderMixin, GetAllLabels):
    pass


class MultiTenantGetSeries(MultiTenantHeaderMixin, GetSeries):
    pass


class MultiTenantGetMetricMetadata(MultiTenantHeaderMixin, GetMetricMetadata):
    pass


class MultiTenantExecuteInstantQuery(MultiTenantHeaderMixin, ExecuteInstantQuery):
    pass


class MultiTenantExecuteRangeQuery(MultiTenantHeaderMixin, ExecuteRangeQuery):
    pass


class MultiTenantPrometheusToolset(PrometheusToolset):
    """Subclasses `PrometheusToolset` only so this toolset type-checks against
    the `toolset: "PrometheusToolset"` field declared on `BasePrometheusTool`
    (pydantic requires an actual isinstance match there, not just a
    structurally similar sibling class). `__init__` intentionally skips
    `PrometheusToolset.__init__` — its tool list, name, and subtype-detection
    logic don't apply here — and calls `Toolset.__init__` directly instead."""

    config_classes: ClassVar[list[Type[MultiTenantPrometheusConfig]]] = [
        MultiTenantPrometheusConfig
    ]
    config: Optional[MultiTenantPrometheusConfig] = None

    def __init__(self):
        Toolset.__init__(
            self,
            name="prometheus/multi-tenant-metrics",
            description=(
                "Prometheus integration for multi-tenant backends that selects the tenant "
                "per request from a Kubernetes resource label value"
            ),
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/prometheus/",
            icon_url="https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/prometheus.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                MultiTenantListPrometheusRules(toolset=self),
                MultiTenantGetMetricNames(toolset=self),
                MultiTenantGetLabelValues(toolset=self),
                MultiTenantGetAllLabels(toolset=self),
                MultiTenantGetSeries(toolset=self),
                MultiTenantGetMetricMetadata(toolset=self),
                MultiTenantExecuteInstantQuery(toolset=self),
                MultiTenantExecuteRangeQuery(toolset=self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
        )
        self._reload_llm_instructions()

    def _reload_llm_instructions(self):
        # Render the stock prometheus_instructions.jinja2 unchanged, then embed it
        # in this toolset's template (jinja `include` can't reach it: the prompt
        # loader is rooted at holmes/plugins/prompts).
        super()._reload_llm_instructions()
        self.llm_instructions = load_and_render_prompt(
            prompt=f"file://{os.path.join(os.path.dirname(os.path.abspath(__file__)), 'multi_tenant_prometheus_instructions.jinja2')}",
            context={
                "config": self.config,
                "prometheus_instructions": self.llm_instructions,
            },
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        config = config or {}
        try:
            self.config = MultiTenantPrometheusConfig(**config)
            self._reload_llm_instructions()
        except Exception as e:
            return False, f"Invalid multi-tenant Prometheus configuration: {e}"
        # No connectivity check: there is no tenant at startup, and Mimir
        # rejects tenant-less queries.
        return True, ""

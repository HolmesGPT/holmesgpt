"""Generic multi-instance support via composition (delegation), not inheritance.

A toolset stays a plain *single-instance* toolset — it knows how to talk to ONE
endpoint and nothing about "instances". To let a single HolmesGPT deployment talk
to several endpoints of the same kind (prod-eu vs prod-us, a Grafana per cluster, …)
you wrap the toolset:

    from holmes.plugins.toolsets.multi_instance import multi_instance
    ...
    multi_instance(ServiceNowTablesToolset),   # <- the entire conversion

`multi_instance(cls)` returns a `MultiInstanceToolset` that:

- mirrors the child's `name`, `description`, `icon_url`, `docs_url`, `tags`, etc., so
  it registers transparently under the same toolset name;
- accepts either the child's normal flat config **or** `{<globals>, instances: [...]}`;
- builds one child toolset per configured instance, running each child's own
  `prerequisites_callable` (its real validation + health check) against a per-instance
  flat config (top-level globals merged in);
- exposes the union of the children's tools as routing proxies that strip the generic
  `instance` parameter and delegate to the chosen child's identically-named tool;
- adds a `<name>_list_instances` discovery tool **only** when >1 instance is configured;
- aggregates health tolerantly (loads if any instance is reachable).

Design rule (where new functionality goes): **single-endpoint concern → the toolset;
choosing/combining endpoints → here, in the wrapper.**

Backwards compatibility: a flat config (no `instances:`) becomes a single instance named
`default` with no `instance` param and no list tool — byte-for-byte the child's normal
surface. Existing `instances:` configs keep working, including auth-as-a-unit and
mTLS-as-a-pair global fall-through (see `_ATOMIC_GROUPS`).
"""

import logging
from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import ConfigDict

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import build_config_example

logger = logging.getLogger(__name__)

INSTANCE_PARAM_NAME = "instance"
INSTANCE_PARAM = ToolParameter(
    description=(
        "Name of the instance to target. Required when more than one instance is "
        "configured (see the `*_list_instances` tool). Leave empty when a single "
        "instance is configured."
    ),
    type="string",
    required=False,
)

# Field groups inherited from the top-level globals as an atomic unit: if an
# instance sets ANY field in a group, it inherits NONE of that group. This
# reproduces the existing fall-through semantics generically — auth methods are
# mutually exclusive (api_key XOR basic XOR bearer) and mTLS is a cert/key pair —
# so a global default never gets cross-wired into an instance that picked another.
_ATOMIC_GROUPS: List[set] = [
    {"api_key", "username", "password", "bearer_token"},
    {"client_cert", "client_key"},
]

# Non-secret fields surfaced by the list-instances tool when present on an instance.
_IDENTIFYING_FIELDS = ("api_url", "url", "prometheus_url", "connection_url", "domain")


def _merge_instance_config(globals_: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    """Merge top-level globals into one instance entry (entry wins per key).

    Atomic groups (auth, mTLS) are dropped from the inherited globals when the
    instance sets any member of the group, preserving the original fall-through.
    """
    inherited = dict(globals_)
    for group in _ATOMIC_GROUPS:
        if any(key in entry for key in group):
            for key in group:
                inherited.pop(key, None)
    return {**inherited, **entry}


def _parse_instances(config: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Decompose a wrapper config into ordered (instance_name, flat_child_config) pairs.

    Flat config (no `instances:`) → one instance named `default`.
    """
    raw = config.get("instances")
    if not raw:
        flat = {k: v for k, v in config.items() if k != "instances"}
        return [("default", flat)]
    if not isinstance(raw, list):
        raise ValueError("`instances` must be a list")
    globals_ = {k: v for k, v in config.items() if k != "instances"}
    seen: set = set()
    out: List[Tuple[str, Dict[str, Any]]] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"`instances[{idx}]` must be a dict, got {type(entry).__name__}")
        name = entry.get("name") or f"instance-{idx}"
        if name in seen:
            raise ValueError(f"Duplicate instance name: '{name}'")
        seen.add(name)
        # `name` is the wrapper's routing key, not part of the child config.
        child_entry = {k: v for k, v in entry.items() if k != "name"}
        out.append((name, _merge_instance_config(globals_, child_entry)))
    return out


class _RoutingTool(Tool):
    """Proxy tool: strips `instance`, picks the child, delegates to its same-named tool.

    Delegating to `child_tool.invoke(...)` preserves the child's approval, parameter
    coercion, transformers and logging untouched. `toolset` points at the wrapper so
    wrapper-level `approval_required_tools` gating still applies during tool listing.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, wrapper: "MultiInstanceToolset", template: Tool, add_instance_param: bool):
        params = dict(template.parameters)
        if add_instance_param:
            params.setdefault(INSTANCE_PARAM_NAME, INSTANCE_PARAM)
        super().__init__(
            name=template.name,
            description=template.description,
            parameters=params,
            user_description=template.user_description,
            icon_url=template.icon_url,
        )
        self._wrapper = wrapper
        self._template = template
        self._add_instance_param = add_instance_param

    @property
    def toolset(self):
        return self._wrapper

    def invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        call_params = dict(params)
        requested = call_params.pop(INSTANCE_PARAM_NAME, None)
        try:
            name, child = self._wrapper._resolve_child(requested)
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR, error=str(e), params=params
            )
        child_tool = self._wrapper._child_tool(child, self.name)
        if child_tool is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Tool '{self.name}' is not available on instance "
                    f"'{requested or 'default'}'."
                ),
                params=params,
            )
        result = child_tool.invoke(call_params, context)
        # Record which instance answered so it's visible in the tool output.
        # Only when multi-instance, so a single/`default` toolset is unchanged.
        if self._add_instance_param:
            result.params = {**(result.params or call_params), INSTANCE_PARAM_NAME: name}
        return result

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        # `invoke` is overridden to delegate; `_invoke` is never reached.
        raise NotImplementedError

    def get_parameterized_one_liner(self, params: Dict) -> str:
        try:
            return self._template.get_parameterized_one_liner(params)
        except Exception:
            return f"{toolset_name_for_one_liner(self._wrapper.name)}: {self.name}"


class ListInstancesTool(Tool):
    """Lets the LLM discover configured instances. Added only when >1 instance."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, wrapper: "MultiInstanceToolset"):
        scoped = wrapper.name.replace("/", "_")
        super().__init__(
            name=f"{scoped}_list_instances",
            description=(
                f"List the instances configured for the `{wrapper.name}` toolset. "
                f"Returns each instance's name so subsequent calls can target the "
                f"right one via the `{INSTANCE_PARAM_NAME}` parameter."
            ),
            parameters={},
        )
        self._wrapper = wrapper

    @property
    def toolset(self):
        return self._wrapper

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={
                "instances": self._wrapper._instance_summaries(),
                "offline_instances": self._wrapper._offline_summaries(),
            },
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._wrapper.name)}: List instances"


class MultiInstanceToolset(Toolset):
    """Wraps a single-instance toolset class and routes calls across N instances."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, child_cls: Type[Toolset]):
        template = child_cls()
        super().__init__(
            name=template.name,
            description=template.description,
            icon_url=template.icon_url,
            docs_url=template.docs_url,
            tags=list(template.tags),
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            enabled=False,
            experimental=template.experimental,
        )
        # Mirror display metadata from the child so the wrapper is transparent.
        self.llm_instructions = template.llm_instructions
        # Mirror remote-exposure intent from the child class default; per-instance
        # overrides are resolved in remote_exposed_instances(). is_core must be
        # mirrored too so a wrapped internal toolset stays hard-excluded from
        # remote publication and execution.
        self.expose_remotely = template.expose_remotely
        self._is_core = template.is_core
        self._child_cls = child_cls
        self._children: Dict[str, Toolset] = {}
        self._instance_configs: Dict[str, Dict[str, Any]] = {}
        self._offline_instances: Dict[str, str] = {}

    # Config schema/example come from the child's config classes (the wrapper's own
    # `config_classes` ClassVar stays empty). The `instances:` shape is documented;
    # the flat child schema drives the UI form and stays backwards compatible.
    def get_config_schema(self) -> Optional[Dict[str, Any]]:
        classes = self._child_cls.config_classes
        if not classes:
            return None
        return {cls.__name__: cls.build_schema_entry() for cls in classes}  # type: ignore[attr-defined]

    def get_config_example(self) -> Optional[Dict[str, Any]]:
        classes = self._child_cls.config_classes
        return build_config_example(classes[0]) if classes else None

    # --- prerequisites: build children, run their health checks, build tools ---

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            instances = _parse_instances(config or {})
        except Exception as e:
            return False, f"Invalid {self.name} configuration: {e}"

        self._children = {}
        self._instance_configs = {}
        self._offline_instances = {}
        failures: List[str] = []
        successes: List[str] = []

        for name, flat in instances:
            child = self._child_cls()
            self._forward_overrides(child)
            ok, msg = self._run_child_prerequisites(child, flat)
            if ok:
                # Only healthy instances are routable; offline ones are tracked
                # separately so tools can't be silently called against them.
                self._children[name] = child
                self._instance_configs[name] = flat
                successes.append(f"[{name}] {msg}".strip())
            else:
                reason = msg or "prerequisite check failed"
                self._offline_instances[name] = reason
                failures.append(f"[{name}] {reason}".strip())

        self._build_tools()
        self._publish_instance_meta()
        self._publish_llm_instructions()
        return self._aggregate(failures, successes)

    def remote_exposed_instances(self) -> Optional[List[str]]:
        """Healthy instance names that should be exposed for remote execution
        (cross-cluster tool calls). Resolution per instance: the child's
        locality heuristic (`remote_exposure_default`) wins when it has an
        opinion, else fall back to the toolset-level `expose_remotely`.
        Returns the list (possibly empty); the publish step skips the toolset
        when it's empty. See design doc Business Logic B/C."""
        exposed: List[str] = []
        for name, child in self._children.items():
            flat = self._instance_configs.get(name, {})
            decision = child.remote_exposure_default(flat)
            if decision is None:
                decision = self.expose_remotely
            if decision:
                exposed.append(name)
        return exposed

    def _publish_instance_meta(self) -> None:
        """Expose per-instance health in `meta` so the UI can render each instance.

        Rides the existing free-form `meta` JSONB (holmes_sync_toolsets -> ToolsetDBModel
        -> supabase -> frontend); no storage schema change. The frontend derives a
        "degraded" state when any instance is unhealthy.
        """
        # Single-instance (flat/`default`) toolsets behave exactly like a normal
        # single toolset — don't advertise per-instance health, so the UI shows
        # them the old way (one row, no instance breakdown).
        if (len(self._instance_configs) + len(self._offline_instances)) <= 1:
            if self.meta:
                self.meta.pop("instances", None)
            return

        instances_meta: List[Dict[str, Any]] = []
        for name, flat in self._instance_configs.items():
            entry: Dict[str, Any] = {"name": name, "healthy": True, "reason": None}
            for field in _IDENTIFYING_FIELDS:
                if flat.get(field):
                    entry[field] = flat[field]
                    break
            instances_meta.append(entry)
        for name, reason in self._offline_instances.items():
            instances_meta.append({"name": name, "healthy": False, "reason": reason})

        meta = dict(self.meta or {})
        meta["instances"] = instances_meta
        self.meta = meta

    def _publish_llm_instructions(self) -> None:
        """Mirror the children's runtime-built llm_instructions onto the wrapper.

        Some toolsets (e.g. Confluence) only build their llm_instructions inside
        prerequisites_callable because the content depends on the configured
        endpoint (base URL, whitelisted paths, auth mode). The static mirror
        taken from the template in __init__ predates that, so without this the
        system prompt renders no usage instructions for the toolset and the
        LLM has to guess request URLs.
        """
        sections: List[str] = []
        multi = (len(self._children) + len(self._offline_instances)) > 1
        for name, child in self._children.items():
            if child.llm_instructions:
                if multi:
                    sections.append(f"### Instance `{name}`\n\n{child.llm_instructions}")
                else:
                    sections.append(child.llm_instructions)
        # Keep the template-derived instructions when no child built any, so
        # toolsets with static instructions are unaffected.
        if sections:
            self.llm_instructions = "\n\n".join(sections)

    def _forward_overrides(self, child: Toolset) -> None:
        """Propagate toolset-level overrides so the child enforces them too."""
        child.approval_required_tools = self.approval_required_tools

    def _run_child_prerequisites(
        self, child: Toolset, flat_config: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Run the child's own callable prerequisite (validation + health) on a flat config."""
        callable_prereq = next(
            (p for p in child.prerequisites if isinstance(p, CallablePrerequisite)), None
        )
        if callable_prereq is None:
            child.config = flat_config
            return True, ""
        try:
            ok, msg = callable_prereq.callable(flat_config)
            return bool(ok), msg or ""
        except Exception as e:
            return False, str(e)

    def _build_tools(self) -> None:
        """Expose the union of children's tools as routing proxies (+ list tool when >1).

        "Multi" counts offline instances too, so the `instance` param and the list tool
        appear whenever >1 instance is configured — even if some are currently down — so
        the LLM can still discover/target them (and get a clear offline error).
        """
        multi = (len(self._children) + len(self._offline_instances)) > 1
        templates: Dict[str, Tool] = {}
        for child in self._children.values():
            for tool in child.tools:
                templates.setdefault(tool.name, tool)
        tools: List[Tool] = [
            _RoutingTool(self, tmpl, add_instance_param=multi) for tmpl in templates.values()
        ]
        if multi:
            tools.append(ListInstancesTool(self))
        self.tools = tools

    def _aggregate(self, failures: List[str], successes: List[str]) -> Tuple[bool, str]:
        """Tolerant: succeed if at least one instance is healthy; surface every failure."""
        if not successes:
            return False, "\n".join(failures) or f"No instances configured for {self.name}"
        if failures:
            total = len(failures) + len(successes)
            logger.warning(
                "%s: %d/%d instance(s) healthy. Failed: %s",
                self.name,
                len(successes),
                total,
                failures,
            )
            return True, "; ".join(successes) + "; failed: " + " | ".join(failures)
        return True, "; ".join(successes)

    # --- routing helpers used by the proxy tools ---

    def _resolve_child(self, requested: Optional[str]) -> Tuple[str, Toolset]:
        configured = sorted(set(self._children) | set(self._offline_instances))
        if requested and requested in self._offline_instances:
            raise ValueError(
                f"Instance '{requested}' is offline: {self._offline_instances[requested]}"
            )
        if not requested:
            if len(self._children) == 1:
                name = next(iter(self._children))
                return name, self._children[name]
            raise ValueError(
                f"`{INSTANCE_PARAM_NAME}` is required (configured: {configured})"
            )
        if requested not in self._children:
            raise ValueError(
                f"Unknown {INSTANCE_PARAM_NAME} '{requested}'. Configured: {configured}"
            )
        return requested, self._children[requested]

    @staticmethod
    def _child_tool(child: Toolset, name: str) -> Optional[Tool]:
        return next((t for t in child.tools if t.name == name), None)

    def _instance_summaries(self) -> List[Dict[str, Any]]:
        summaries: List[Dict[str, Any]] = []
        for name, flat in self._instance_configs.items():
            summary: Dict[str, Any] = {"name": name}
            for field in _IDENTIFYING_FIELDS:
                if flat.get(field):
                    summary[field] = flat[field]
                    break
            summaries.append(summary)
        return summaries

    def _offline_summaries(self) -> List[Dict[str, Any]]:
        """Instances that failed their health check: name + why they're offline."""
        return [
            {"name": name, "reason": reason}
            for name, reason in self._offline_instances.items()
        ]


def multi_instance(child_cls: Type[Toolset]) -> MultiInstanceToolset:
    """Wrap a single-instance toolset class to make it multi-instance capable.

    A per-child subclass surfaces the child's ``config_classes`` so config-driven
    tooling keeps working — notably the CLI's interactive ``toolset config`` editor,
    which reads ``toolset.config_classes`` directly to list and build the form.
    Without this, wrapped toolsets would have empty ``config_classes`` and silently
    disappear from the editor, breaking single-instance configuration. The editor
    still edits the flat (single-instance) config; ``instances:`` is YAML-only.
    ``config_classes`` is a ClassVar, so it must be set on the class, not the instance.
    """
    wrapper_cls = type(
        f"MultiInstance{child_cls.__name__}",
        (MultiInstanceToolset,),
        {"config_classes": child_cls.config_classes},
    )
    return wrapper_cls(child_cls)

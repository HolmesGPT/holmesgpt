import json
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from kubernetes import config
from kubernetes.client import ApiClient  # type: ignore
from kubernetes.dynamic import DynamicClient  # type: ignore
from kubernetes.dynamic.exceptions import (  # type: ignore
    ResourceNotFoundError,
    ResourceNotUniqueError,
)

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

_cached_dynamic_client: Optional[DynamicClient] = None


def _get_dynamic_client() -> DynamicClient:
    global _cached_dynamic_client
    if _cached_dynamic_client:
        return _cached_dynamic_client

    try:
        if os.getenv("KUBERNETES_SERVICE_HOST"):
            config.load_incluster_config()
        else:
            config.load_kube_config()
        _cached_dynamic_client = DynamicClient(ApiClient())
        return _cached_dynamic_client
    except config.config_exception.ConfigException as exc:  # type: ignore[attr-defined]
        raise RuntimeError(f"Kubernetes configuration not found: {exc}") from exc
    except Exception as exc:  # pragma: no cover - defensive catch
        raise RuntimeError(f"Failed to initialize Kubernetes client: {exc}") from exc


def _normalize_kind(kind: str) -> str:
    cleaned = kind.strip()
    if "/" in cleaned:
        cleaned = cleaned.split("/")[-1]
    if "." in cleaned:
        cleaned = cleaned.split(".")[0]

    if cleaned.endswith("ses"):
        cleaned = cleaned[:-2]
    elif cleaned.endswith("ies"):
        cleaned = cleaned[:-3] + "y"
    elif cleaned.endswith("s") and len(cleaned) > 1:
        cleaned = cleaned[:-1]

    return cleaned[:1].upper() + cleaned[1:]


def _extract_continue(metadata: Dict[str, Any]) -> Optional[str]:
    return metadata.get("_continue") or metadata.get("continue")


def _extract_field(item: Dict[str, Any], path: str) -> Optional[Any]:
    parts = [part for part in path.split(".") if part]
    value: Any = item
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list):
            try:
                index = int(part)
            except ValueError:
                return None
            if index < 0 or index >= len(value):
                return None
            value = value[index]
        else:
            return None
    return value


def _candidate_strings(item_dict: Dict[str, Any], filter_paths: Sequence[str]) -> List[str]:
    candidates: List[str] = []
    for path in filter_paths:
        value = _extract_field(item_dict, path)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            candidates.append(
                json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            )
        else:
            candidates.append(str(value))

    if candidates:
        return candidates

    return [json.dumps(item_dict, ensure_ascii=False, separators=(",", ":"))]


def _item_matches_filter(
    item_dict: Dict[str, Any],
    filter_value: Optional[str],
    filter_paths: Sequence[str],
    use_regex: bool,
) -> bool:
    if not filter_value:
        return True

    candidates = _candidate_strings(item_dict, filter_paths)
    if not candidates:
        return False

    if use_regex:
        try:
            pattern = re.compile(filter_value, re.IGNORECASE)
            return any(pattern.search(candidate) for candidate in candidates)
        except re.error:
            logging.warning("Invalid regex provided, falling back to substring match")

    filter_lower = filter_value.lower()
    return any(filter_lower in candidate.lower() for candidate in candidates)


def _summarize_item(item_dict: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item_dict.get("metadata") or {}
    summary = {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "kind": item_dict.get("kind"),
        "apiVersion": item_dict.get("apiVersion"),
        "labels": metadata.get("labels"),
        "annotations": metadata.get("annotations"),
        "object": item_dict,
    }

    status = item_dict.get("status")
    if status is not None:
        summary["status"] = status

    return summary


def _resolve_resource(client: DynamicClient, api_version: str, kind: str):
    attempts = [
        {"api_version": api_version, "kind": kind},
        {"api_version": api_version, "name": kind},
        {"api_version": api_version, "kind": _normalize_kind(kind)},
    ]

    errors: list[str] = []
    for attempt in attempts:
        try:
            return client.resources.get(**attempt)
        except ResourceNotFoundError as exc:
            errors.append(str(exc))
            continue
        except ResourceNotUniqueError as exc:
            errors.append(str(exc))
            continue
    joined_errors = "; ".join(errors)
    raise ResourceNotFoundError(
        f"Failed to resolve Kubernetes resource '{kind}' in '{api_version}': {joined_errors}"
    )


class KubernetesGetResources(Tool):
    def __init__(self, toolset: "KubernetesGetToolset"):
        super().__init__(
            name="kubernetes_get_resources",
            description=(
                "List Kubernetes resources using the Kubernetes API with client-side filtering "
                "and chunked pagination to avoid high memory usage."
            ),
            parameters={
                "api_version": ToolParameter(
                    description="API version for the resource (e.g., v1, apps/v1).",
                    type="string",
                    required=True,
                ),
                "kind": ToolParameter(
                    description=(
                        "Resource kind or plural name (e.g., Pod, pods, Deployment). "
                        "The tool resolves the resource dynamically."
                    ),
                    type="string",
                    required=True,
                ),
                "namespace": ToolParameter(
                    description=(
                        "Namespace to query. Leave empty to search all namespaces when supported."
                    ),
                    type="string",
                    required=False,
                ),
                "label_selector": ToolParameter(
                    description="Server-side label selector, same syntax as kubectl.",
                    type="string",
                    required=False,
                ),
                "field_selector": ToolParameter(
                    description="Server-side field selector, same syntax as kubectl.",
                    type="string",
                    required=False,
                ),
                "client_filter": ToolParameter(
                    description=(
                        "Case-insensitive substring (or regex when use_regex=true) applied on the "
                        "selected fields. Filtering happens client-side while streaming pages."
                    ),
                    type="string",
                    required=False,
                ),
                "filter_paths": ToolParameter(
                    description=(
                        "List of dotted JSON paths to evaluate the filter against (e.g., "
                        "metadata.name, metadata.namespace). If omitted, the full object is used."
                    ),
                    type="array",
                    required=False,
                    items=ToolParameter(type="string"),
                ),
                "use_regex": ToolParameter(
                    description="Treat client_filter as a regular expression instead of substring.",
                    type="boolean",
                    required=False,
                ),
                "chunk_size": ToolParameter(
                    description=(
                        "Page size for Kubernetes API listing. Smaller values reduce memory spikes."
                    ),
                    type="integer",
                    required=False,
                ),
                "max_results": ToolParameter(
                    description=(
                        "Optional hard cap on the number of matching resources to return. Useful "
                        "to keep responses compact."
                    ),
                    type="integer",
                    required=False,
                ),
                "output_format": ToolParameter(
                    description=(
                        "Output format. Use 'count' to return only the count plus metadata; defaults to 'full'."
                    ),
                    type="string",
                    required=False,
                    enum=["full", "count"],
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        api_version = params.get("api_version", "v1")
        kind = params.get("kind")
        namespace = params.get("namespace")
        label_selector = params.get("label_selector")
        field_selector = params.get("field_selector")
        client_filter = params.get("client_filter")
        filter_paths = params.get("filter_paths") or [
            "metadata.name",
            "metadata.namespace",
        ]
        use_regex = bool(params.get("use_regex", False))
        chunk_size = int(params.get("chunk_size") or 500)
        max_results = params.get("max_results")
        max_results_int = int(max_results) if max_results else None
        output_format = (params.get("output_format") or "full").lower()

        if not kind:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Parameter 'kind' is required",
                params=params,
            )

        chunk_size = max(1, min(chunk_size, 1000))

        try:
            dynamic_client = _get_dynamic_client()
            resource = _resolve_resource(dynamic_client, api_version, kind)
        except Exception as exc:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(exc),
                params=params,
            )

        continue_token: Optional[str] = None
        processed = 0
        pages = 0
        matches: List[Dict[str, Any]] = []

        while True:
            try:
                response = resource.get(
                    namespace=namespace if resource.namespaced else None,
                    label_selector=label_selector,
                    field_selector=field_selector,
                    limit=chunk_size,
                    _continue=continue_token,
                )
            except Exception as exc:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Failed to list resources: {exc}",
                    params=params,
                )

            pages += 1
            response_dict = response.to_dict()
            items: Iterable[Any] = response_dict.get("items", [])
            processed += len(items)

            for item in items:
                item_dict = item if isinstance(item, dict) else getattr(item, "to_dict")()
                if _item_matches_filter(item_dict, client_filter, filter_paths, use_regex):
                    matches.append(_summarize_item(item_dict))
                    if max_results_int and len(matches) >= max_results_int:
                        break

            if max_results_int and len(matches) >= max_results_int:
                continue_token = _extract_continue(response_dict.get("metadata", {}))
                break

            continue_token = _extract_continue(response_dict.get("metadata", {}))
            if not continue_token:
                break

        summary = {
            "api_version": api_version,
            "kind": resource.kind,
            "namespaced": resource.namespaced,
            "namespace": namespace if resource.namespaced else None,
            "label_selector": label_selector,
            "field_selector": field_selector,
            "client_filter": client_filter,
            "filter_paths": filter_paths,
            "chunk_size": chunk_size,
            "pages_fetched": pages,
            "items_processed": processed,
            "items_matched": len(matches),
            "truncated": bool(max_results_int and len(matches) >= max_results_int),
            "last_continue_token": continue_token,
        }

        data: Dict[str, Any] = {"summary": summary, "count": len(matches)}
        if output_format != "count":
            data["items"] = matches

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        namespace = params.get("namespace") or "all namespaces"
        return (
            f"{toolset_name_for_one_liner(self.toolset.name)}: get {params.get('kind')} "
            f"in {namespace} (apiVersion={params.get('api_version', 'v1')})"
        )


class KubernetesGetToolset(Toolset):
    def __init__(self):
        super().__init__(
            name="kubernetes/get",
            description="List Kubernetes resources with optional client-side filtering and pagination.",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/kubernetes/",
            icon_url="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRPKA-U9m5BxYQDF1O7atMfj9EMMXEoGu4t0Q&s",
            prerequisites=[CallablePrerequisite(callable=self._health_check)],
            tools=[KubernetesGetResources(self)],
            tags=[ToolsetTag.CORE],
            is_default=True,
        )

    def _health_check(self, _config: dict[str, Any]) -> tuple[bool, str]:
        try:
            _get_dynamic_client()
            return True, ""
        except Exception as exc:
            logging.warning("kubernetes/get toolset disabled: %s", exc)
            return False, str(exc)

    def get_example_config(self) -> Dict[str, Any]:
        return {}

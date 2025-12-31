import json
import logging
from typing import Any, Dict, Optional, Tuple

from jsonpath_ng import parse as jsonpath_parse  # type: ignore

from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus, ToolParameter

logger = logging.getLogger(__name__)


def _truncate_to_depth(value: Any, max_depth: Optional[int], current_depth: int = 0):
    """Recursively truncate dictionaries/lists beyond the requested depth."""
    if max_depth is None or max_depth < 0:
        return value

    if current_depth >= max_depth:
        if isinstance(value, (dict, list)):
            return f"...truncated at depth {max_depth}"
        return value

    if isinstance(value, dict):
        return {
            key: _truncate_to_depth(sub_value, max_depth, current_depth + 1)
            for key, sub_value in value.items()
        }
    if isinstance(value, list):
        return [
            _truncate_to_depth(item, max_depth, current_depth + 1) for item in value
        ]

    return value


def _apply_jsonpath_filter(data: Any, expression: str) -> Tuple[Optional[Any], Optional[str]]:
    try:
        jsonpath_expr = jsonpath_parse(expression)
        matches = [match.value for match in jsonpath_expr.find(data)]
        if len(matches) == 1:
            return matches[0], None
        return matches, None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to apply jsonpath filter", exc_info=exc)
        return None, f"Invalid jsonpath expression: {exc}"


class JsonFilterMixin:
    """Opt-in mixin for tools that return JSON and want filtering controls."""

    filter_parameters: Dict[str, ToolParameter] = {
        "max_depth": ToolParameter(
            description="Maximum nesting depth to return from the JSON (0 returns only top-level keys). Leave empty for full response.",
            type="integer",
            required=False,
        ),
        "jsonpath": ToolParameter(
            description="Optional jsonpath expression to extract specific parts of the JSON.",
            type="string",
            required=False,
        ),
    }

    @classmethod
    def extend_parameters(cls, existing: Dict[str, ToolParameter]) -> Dict[str, ToolParameter]:
        merged = dict(cls.filter_parameters)
        merged.update(existing)
        return merged

    def _filter_result_data(self, data: Any, params: Dict) -> Tuple[Any, Optional[str]]:
        parsed_data = data
        if isinstance(data, str):
            try:
                parsed_data = json.loads(data)
            except Exception:
                # Not JSON, leave as-is
                return data, None

        if params.get("jsonpath"):
            parsed_data, error = _apply_jsonpath_filter(parsed_data, params["jsonpath"])
            if error:
                return None, error

        parsed_data = _truncate_to_depth(parsed_data, params.get("max_depth"))
        return parsed_data, None

    @staticmethod
    def _safe_string(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        try:
            return str(value)
        except Exception:
            return None

    def filter_result(self, result: StructuredToolResult, params: Dict) -> StructuredToolResult:
        base_result = result if isinstance(result, StructuredToolResult) else None
        if base_result is None:
            base_result = StructuredToolResult(
                status=getattr(result, "status", StructuredToolResultStatus.SUCCESS),
                data=getattr(result, "data", None),
                params=getattr(result, "params", params),
                url=self._safe_string(getattr(result, "url", None)),
                invocation=self._safe_string(getattr(result, "invocation", None)),
                icon_url=self._safe_string(getattr(result, "icon_url", None)),
            )
        else:
            # Normalize string fields to avoid MagicMock validation failures
            base_result.url = self._safe_string(base_result.url)
            base_result.invocation = self._safe_string(base_result.invocation)
            base_result.icon_url = self._safe_string(base_result.icon_url)

        filtered_data, error = self._filter_result_data(base_result.data, params)
        if error:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error,
                params=params,
                url=base_result.url,
                invocation=base_result.invocation,
                icon_url=base_result.icon_url,
            )

        base_result.data = filtered_data
        return base_result

"""Utilities for KRR (Kubernetes Resource Recommendations) data processing."""

import logging
from typing import Any, Dict


def parse_cpu(cpu_value: Any) -> float:
    """Parse Kubernetes CPU value to float (in cores).

    Handles:
    - Numeric values (0.1, 1, etc.) - already in cores
    - String values with 'm' suffix (100m = 0.1 cores)
    - String numeric values ("0.5")

    Args:
        cpu_value: CPU value to parse (can be int, float, str, or None)

    Returns:
        CPU value in cores as float, or 0.0 if invalid
    """
    if cpu_value is None or cpu_value == "" or cpu_value == "?":
        return 0.0
    try:
        if isinstance(cpu_value, (int, float)):
            return float(cpu_value)

        cpu_str = str(cpu_value).strip()
        if cpu_str.endswith("m"):
            return float(cpu_str[:-1]) / 1000.0
        return float(cpu_str)
    except (ValueError, AttributeError, TypeError):
        return 0.0


def parse_memory(memory_value: Any) -> float:
    """Parse Kubernetes memory value to float (in bytes).

    Handles:
    - Numeric values (already in bytes)
    - String values with units (100Mi, 1Gi, etc.)
    - String numeric values ("1048576")

    Args:
        memory_value: Memory value to parse (can be int, float, str, or None)

    Returns:
        Memory value in bytes as float, or 0.0 if invalid
    """
    if memory_value is None or memory_value == "" or memory_value == "?":
        return 0.0
    try:
        if isinstance(memory_value, (int, float)):
            return float(memory_value)

        memory_str = str(memory_value).strip()
        units = {
            "Ki": 1024,
            "Mi": 1024**2,
            "Gi": 1024**3,
            "Ti": 1024**4,
            "K": 1000,
            "M": 1000**2,
            "G": 1000**3,
            "T": 1000**4,
        }
        for unit, multiplier in units.items():
            if memory_str.endswith(unit):
                return float(memory_str[: -len(unit)]) * multiplier
        return float(memory_str)
    except (ValueError, AttributeError, TypeError):
        return 0.0


# Helper to get numeric value from allocated/recommended, handling "?" strings
def get_value(data: Dict, field: str, subfield: str) -> Any:
    if not data:
        return 0.0
    val = data.get(field, {}).get(subfield)
    if val is None or val == "?":
        return 0.0
    return val


def calculate_krr_savings(result: Dict, sort_by: str) -> float:
    """Calculate potential savings from KRR recommendation data.

    The KRR data structure has a 'content' field that contains a list of resource
    recommendations. Each item in the list represents either CPU or memory, with:
    - resource: "cpu" or "memory"
    - allocated: {request: value, limit: value} - current allocation
    - recommended: {request: value, limit: value} - recommended allocation

    Args:
        result: KRR scan result dictionary with 'content' field
        sort_by: Sorting criteria, one of:
            - "cpu_total": Total CPU savings (requests + limits)
            - "memory_total": Total memory savings (requests + limits)
            - "cpu_requests": CPU requests savings only
            - "memory_requests": Memory requests savings only
            - "cpu_limits": CPU limits savings only
            - "memory_limits": Memory limits savings only

    Returns:
        Calculated savings as a float (>= 0.0). Returns 0.0 for invalid data
        or when recommended values are higher than allocated.
    """
    try:
        content_list = result.get("content", [])
        if not content_list or not isinstance(content_list, list):
            return 0.0

        cpu_data = None
        memory_data = None
        for item in content_list:
            if item.get("resource") == "cpu":
                cpu_data = item
            elif item.get("resource") == "memory":
                memory_data = item

        if not cpu_data and not memory_data:
            return 0.0

        savings = 0.0

        if sort_by == "cpu_total" and cpu_data:
            cpu_req_allocated = parse_cpu(get_value(cpu_data, "allocated", "request"))
            cpu_req_recommended = parse_cpu(
                get_value(cpu_data, "recommended", "request")
            )
            cpu_lim_allocated = parse_cpu(get_value(cpu_data, "allocated", "limit"))
            cpu_lim_recommended = parse_cpu(get_value(cpu_data, "recommended", "limit"))

            savings = (cpu_req_allocated - cpu_req_recommended) + (
                cpu_lim_allocated - cpu_lim_recommended
            )

        elif sort_by == "memory_total" and memory_data:
            mem_req_allocated = parse_memory(
                get_value(memory_data, "allocated", "request")
            )
            mem_req_recommended = parse_memory(
                get_value(memory_data, "recommended", "request")
            )
            mem_lim_allocated = parse_memory(
                get_value(memory_data, "allocated", "limit")
            )
            mem_lim_recommended = parse_memory(
                get_value(memory_data, "recommended", "limit")
            )

            savings = (mem_req_allocated - mem_req_recommended) + (
                mem_lim_allocated - mem_lim_recommended
            )

        elif sort_by == "cpu_requests" and cpu_data:
            cpu_req_allocated = parse_cpu(get_value(cpu_data, "allocated", "request"))
            cpu_req_recommended = parse_cpu(
                get_value(cpu_data, "recommended", "request")
            )
            savings = cpu_req_allocated - cpu_req_recommended

        elif sort_by == "memory_requests" and memory_data:
            mem_req_allocated = parse_memory(
                get_value(memory_data, "allocated", "request")
            )
            mem_req_recommended = parse_memory(
                get_value(memory_data, "recommended", "request")
            )
            savings = mem_req_allocated - mem_req_recommended

        elif sort_by == "cpu_limits" and cpu_data:
            cpu_lim_allocated = parse_cpu(get_value(cpu_data, "allocated", "limit"))
            cpu_lim_recommended = parse_cpu(get_value(cpu_data, "recommended", "limit"))
            savings = cpu_lim_allocated - cpu_lim_recommended

        elif sort_by == "memory_limits" and memory_data:
            mem_lim_allocated = parse_memory(
                get_value(memory_data, "allocated", "limit")
            )
            mem_lim_recommended = parse_memory(
                get_value(memory_data, "recommended", "limit")
            )
            savings = mem_lim_allocated - mem_lim_recommended

        return savings
    except Exception as e:
        logging.debug(f"Error calculating savings for result: {e}")
        return 0.0

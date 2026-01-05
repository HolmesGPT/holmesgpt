from typing import Any, Dict, Tuple


def callable_success(config: Dict[str, Any]) -> Tuple[bool, str]:
    return True, ""


def callable_failure_with_message(config: Dict[str, Any]) -> Tuple[bool, str]:
    return False, "Callable check failed"


def callable_failure_no_message(config: Dict[str, Any]) -> Tuple[bool, str]:
    return False, ""


def failing_callable_for_test(config: dict):
    raise Exception("Failure in callable prerequisite")

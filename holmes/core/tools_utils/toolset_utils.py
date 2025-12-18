import logging
from holmes.core.tools import Toolset, ToolsetStatusEnum
from holmes.plugins.toolsets.logging_utils.logging_api import BasePodLoggingToolset


def filter_out_default_logging_toolset(toolsets: list[Toolset]) -> list[Toolset]:
    """
    Filters the list of toolsets to ensure there is a single enabled BasePodLoggingToolset.
    The selection logic for BasePodLoggingToolset is as follows:
    - If there is exactly one BasePodLoggingToolset, it is returned.
    - If there are multiple enabled BasePodLoggingToolsets and only one is enabled, the enabled one is included, the others are filtered out
    - If there are multiple enabled BasePodLoggingToolsets:
        - Toolsets not named "kubernetes/logs" are preferred.
        - Among the preferred (or if none are preferred, among all enabled),
          the one whose name comes first alphabetically is chosen.
    All other types of toolsets are included as is.
    """

    logging_toolsets: list[Toolset] = []
    final_toolsets: list[Toolset] = []

    for ts in toolsets:
        toolset_type = (
            ts.original_toolset_type
            if hasattr(ts, "original_toolset_type")
            else type(ts)
        )
        if (
            issubclass(toolset_type, BasePodLoggingToolset)
            and ts.status == ToolsetStatusEnum.ENABLED
        ):
            logging_toolsets.append(ts)
        else:
            final_toolsets.append(ts)

    if not logging_toolsets:
        logging.warning("NO ENABLED LOGGING TOOLSET")
        pass
    elif len(logging_toolsets) == 1:
        final_toolsets.append(logging_toolsets[0])
    else:
        non_k8s_logs_candidates = [
            ts for ts in logging_toolsets if ts.name != "kubernetes/logs"
        ]

        if non_k8s_logs_candidates:
            # Prefer non-"kubernetes/logs" toolsets
            # Sort them to ensure the behaviour is "stable" and does not change across restarts
            non_k8s_logs_candidates.sort(key=lambda ts: ts.name)
            logging.info(f"Using logging toolset {non_k8s_logs_candidates[0].name}")
            final_toolsets.append(non_k8s_logs_candidates[0])
        else:
            logging.info(f"Using logging toolset {logging_toolsets[0].name}")
            # If only "kubernetes/logs" toolsets
            final_toolsets.append(logging_toolsets[0])

    return final_toolsets

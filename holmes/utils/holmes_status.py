import json
import logging
import os
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from holmes import get_version  # type: ignore
from holmes.common.env_vars import (
    ENABLE_CONVERSATION_WORKER,
    CONVERSATION_WORKER_USE_REALTIME_BROADCAST,
)
from holmes.config import Config
from holmes.core.supabase_dal import SupabaseDal

# Default in-pod path mounted by Kubernetes for every Pod with a service
# account token (the default). Used as the fallback when POD_NAMESPACE
# isn't wired up via the downward API.
_SERVICEACCOUNT_NAMESPACE_FILE = Path(
    "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
)


@lru_cache(maxsize=1)
def _detect_runner_namespace() -> Optional[str]:
    """Best-effort detection of the namespace the Holmes runner Pod is in.

    Order of preference:
        1. ``POD_NAMESPACE`` env var (set via the Kubernetes downward API:
           ``valueFrom.fieldRef.fieldPath: metadata.namespace``). Preferred
           because it's explicit and easy to override in tests.
        2. The service-account namespace file mounted by default into every
           Pod that has a service account token,
           ``/var/run/secrets/kubernetes.io/serviceaccount/namespace``.

    Returns ``None`` when neither is available — i.e. when Holmes runs
    outside Kubernetes (CLI / local dev). The caller should leave the
    field unset / ``None`` in that case rather than fabricate a value.

    Cached because the namespace is invariant for the lifetime of the
    process.
    """
    env_val = os.environ.get("POD_NAMESPACE")
    if env_val and env_val.strip():
        return env_val.strip()
    try:
        if _SERVICEACCOUNT_NAMESPACE_FILE.is_file():
            content = _SERVICEACCOUNT_NAMESPACE_FILE.read_text(encoding="utf-8").strip()
            if content:
                return content
    except OSError as exc:
        logging.debug(f"Failed to read service-account namespace file: {exc}")
    return None


@dataclass
class HolmesMetadata:
    is_robusta_ai_enabled: bool
    supports_additional_system_prompt: bool = True
    supports_realtime_conversations: bool = False
    requires_realtime_broadcast: bool = False
    namespace: Optional[str] = None


def update_holmes_status_in_db(dal: SupabaseDal, config: Config):
    logging.info("Updating status of holmes")

    if not config.cluster_name:
        raise Exception(
            "Cluster name is missing in the configuration. Please ensure 'CLUSTER_NAME' is defined in the environment variables, "
            "or verify that a cluster name is provided in the Robusta configuration file."
        )

    metadata = HolmesMetadata(
        is_robusta_ai_enabled=config.should_try_robusta_ai,
        supports_realtime_conversations=bool(ENABLE_CONVERSATION_WORKER),
        requires_realtime_broadcast=bool(CONVERSATION_WORKER_USE_REALTIME_BROADCAST),
        namespace=_detect_runner_namespace(),
    )

    dal.upsert_holmes_status(
        {
            "cluster_id": config.cluster_name,
            "model": json.dumps(config.get_models_list()),
            "version": get_version(),
            "metadata": json.dumps(asdict(metadata)),
        }
    )

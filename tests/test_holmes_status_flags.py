"""HolmesStatus heartbeat capability flags for the platform UI's internal
request kinds (oauth_callback / holmes_logs)."""

import json
from dataclasses import asdict
from unittest.mock import MagicMock, patch

from holmes.utils.holmes_status import HolmesMetadata, update_holmes_status_in_db
from tests.core.test_holmes_key import PUBLIC_KEY


def _config():
    config = MagicMock()
    config.cluster_name = "c1"
    config.should_try_robusta_ai = False
    config.get_models_list.return_value = ["m"]
    config.llm_model_registry.reads_robusta_catalog.return_value = False
    return config


def _published_metadata(realtime_available):
    dal = MagicMock()
    with patch("holmes.utils.holmes_status.ENABLE_CONVERSATION_WORKER", True), patch(
        "holmes.utils.holmes_status._detect_runner_namespace", return_value="ns"
    ):
        update_holmes_status_in_db(dal, _config(), realtime_available=realtime_available)
    return json.loads(dal.upsert_holmes_status.call_args[0][0]["metadata"])


def test_flags_published_once_realtime_verified():
    metadata = _published_metadata(realtime_available=True)
    assert metadata["supports_realtime_conversations"] is True
    assert metadata["supports_oauth_via_realtime"] is True
    assert metadata["supports_holmes_logs"] is True


def test_flags_off_until_realtime_verified():
    # The worker serving these kinds runs only on the realtime path, so the
    # UI must not route to an agent that would never claim the row.
    metadata = _published_metadata(realtime_available=False)
    assert metadata["supports_oauth_via_realtime"] is False
    assert metadata["supports_holmes_logs"] is False


def test_flags_off_when_conversation_worker_disabled():
    dal = MagicMock()
    with patch("holmes.utils.holmes_status.ENABLE_CONVERSATION_WORKER", False), patch(
        "holmes.utils.holmes_status._detect_runner_namespace", return_value=None
    ):
        update_holmes_status_in_db(dal, _config(), realtime_available=True)
    metadata = json.loads(dal.upsert_holmes_status.call_args[0][0]["metadata"])
    assert metadata["supports_oauth_via_realtime"] is False
    assert metadata["supports_holmes_logs"] is False


def test_metadata_keys_are_additive():
    keys = set(asdict(HolmesMetadata(is_robusta_ai_enabled=False)))
    assert {
        "is_robusta_ai_enabled",
        "supports_additional_system_prompt",
        "supports_realtime_conversations",
        "requires_realtime_broadcast",
        "namespace",
        "honors_robusta_ai_disabled",
        "supports_oauth_via_realtime",
        "supports_holmes_logs",
        "holmes_public_key",
    } == keys


def test_public_key_published_from_the_signing_key():
    with patch(
        "holmes.config.Config.get_robusta_global_config_value",
        return_value="test-signing-key",
    ):
        metadata = _published_metadata(realtime_available=True)
    assert metadata["holmes_public_key"] == PUBLIC_KEY

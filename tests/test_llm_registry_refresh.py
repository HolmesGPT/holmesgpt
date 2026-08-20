"""LLMModelRegistry.refresh_robusta_models (ROB-795, ROB-707).

The Robusta catalog used to be read once, at startup: an agent that lost that
fetch served the legacy fallback until its pod restarted, and catalog changes
never reached a running agent. Refreshing must pick both up - without ever
downgrading a healthy registry when a refresh fails.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from holmes.clients.robusta_client import RobustaModel, RobustaModelsResponse
from holmes.config import Config
from holmes.core.llm import ROBUSTA_AI_MODEL_NAME, LLMModelRegistry, ModelEntry
from holmes.utils.holmes_status import update_holmes_status_in_db


def _catalog(*model_names: str, default: str = "") -> RobustaModelsResponse:
    return RobustaModelsResponse(
        models={
            name: RobustaModel(
                model=f"azure/{name}", holmes_args={}, is_default=name == default
            )
            for name in model_names
        }
    )


def _config() -> MagicMock:
    config = MagicMock()
    config.cluster_name = "test-cluster"
    config.should_try_robusta_ai = True
    # Not a Mock: an unset model must not look like a configured one.
    config.model = None
    config.api_base = None
    config.api_key = None
    config.api_version = None
    return config


def _dal() -> MagicMock:
    dal = MagicMock()
    dal.account_id = "account-id"
    dal.enabled = True
    dal.get_ai_credentials.return_value = ("account-id", "token")
    return dal


@pytest.fixture
def build_registry(monkeypatch):
    """A registry booted against `boot_catalog` (None = the fetch failed)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MODEL", raising=False)

    def factory(boot_catalog, file_models=None, robusta_ai=True):
        monkeypatch.setattr(
            LLMModelRegistry,
            "_parse_models_file",
            lambda self, path: dict(file_models or {}),
        )
        with (
            patch("holmes.core.llm.ROBUSTA_AI", robusta_ai),
            patch("holmes.core.llm.fetch_robusta_models", return_value=boot_catalog),
        ):
            return LLMModelRegistry(_config(), _dal())

    return factory


def _refresh_with(registry: LLMModelRegistry, catalog) -> bool:
    with patch("holmes.core.llm.fetch_robusta_models", return_value=catalog):
        return registry.refresh_robusta_models()


def test_adds_new_models_and_drops_deleted_ones(build_registry):
    registry = build_registry(
        _catalog("Robusta/sonnet-4-5", default="Robusta/sonnet-4-5")
    )

    changed = _refresh_with(
        registry, _catalog("Robusta/sonnet-5", default="Robusta/sonnet-5")
    )

    assert changed
    assert set(registry.models) == {"Robusta/sonnet-5"}
    assert registry.default_robusta_model == "Robusta/sonnet-5"


def test_drops_deleted_models_that_are_not_robusta_prefixed(build_registry):
    """Custom-named hosted models are catalog models too (ROB-707)."""
    registry = build_registry(
        _catalog("Playtika-sonnet-4-5", default="Playtika-sonnet-4-5")
    )

    _refresh_with(registry, _catalog("Playtika-sonnet-5", default="Playtika-sonnet-5"))

    assert set(registry.models) == {"Playtika-sonnet-5"}


def test_failed_refresh_keeps_the_loaded_models(build_registry):
    registry = build_registry(_catalog("Robusta/opus-4-6", default="Robusta/opus-4-6"))

    changed = _refresh_with(registry, None)

    assert not changed
    assert set(registry.models) == {"Robusta/opus-4-6"}
    assert registry.default_robusta_model == "Robusta/opus-4-6"


def test_empty_response_keeps_the_loaded_models(build_registry):
    registry = build_registry(_catalog("Robusta/opus-4-6", default="Robusta/opus-4-6"))

    changed = _refresh_with(registry, _catalog())

    assert not changed
    assert set(registry.models) == {"Robusta/opus-4-6"}


def test_heals_an_agent_that_booted_without_a_catalog(build_registry):
    """The ROB-795 recovery: the legacy entry gives way to the real catalog."""
    registry = build_registry(None)
    assert set(registry.models) == {ROBUSTA_AI_MODEL_NAME}

    changed = _refresh_with(
        registry,
        _catalog("Robusta/opus-4-6", "Robusta/gpt-5", default="Robusta/opus-4-6"),
    )

    assert changed
    assert set(registry.models) == {"Robusta/opus-4-6", "Robusta/gpt-5"}
    assert registry.default_robusta_model == "Robusta/opus-4-6"


def test_keeps_user_defined_models(build_registry):
    registry = build_registry(
        _catalog("Robusta/opus-4-6", default="Robusta/opus-4-6"),
        file_models={
            "my-azure-gpt4": ModelEntry(model="azure/gpt-4", name="my-azure-gpt4")
        },
    )

    _refresh_with(registry, _catalog("Robusta/gpt-5", default="Robusta/gpt-5"))

    assert set(registry.models) == {"my-azure-gpt4", "Robusta/gpt-5"}


def test_unknown_model_lookup_keeps_the_catalog_when_the_fetch_fails(build_registry):
    """Asking for a model the agent doesn't have must not cost it the ones it does.

    `get_model_params` re-syncs on an unknown `Robusta/` name, and that re-sync
    used to rebuild the registry from scratch: if the fetch failed mid-request,
    the whole catalog was replaced by the legacy fallback - ROB-795, triggered
    by a single click.
    """
    registry = build_registry(
        _catalog("Robusta/opus-4-6", "Robusta/gpt-5", default="Robusta/opus-4-6")
    )

    with patch("holmes.core.llm.fetch_robusta_models", return_value=None):
        registry.get_model_params("Robusta/retired-model")

    assert set(registry.models) == {"Robusta/opus-4-6", "Robusta/gpt-5"}
    assert registry.default_robusta_model == "Robusta/opus-4-6"


def test_unknown_custom_named_model_is_found_after_a_resync(build_registry):
    """A model added to the catalog after boot must become usable without a
    pod restart - including one whose name carries no `Robusta/` prefix, which
    is every model on a customer with a private catalog (ROB-707)."""
    registry = build_registry(
        _catalog("Playtika-sonnet-4-5", default="Playtika-sonnet-4-5")
    )

    with patch(
        "holmes.core.llm.fetch_robusta_models",
        return_value=_catalog(
            "Playtika-sonnet-4-5", "Playtika-sonnet-5", default="Playtika-sonnet-4-5"
        ),
    ):
        entry = registry.get_model_params("Playtika-sonnet-5")

    assert entry.name == "Playtika-sonnet-5"


def test_unchanged_catalog_reports_no_change(build_registry):
    registry = build_registry(_catalog("Robusta/opus-4-6", default="Robusta/opus-4-6"))

    changed = _refresh_with(
        registry, _catalog("Robusta/opus-4-6", default="Robusta/opus-4-6")
    )

    assert not changed
    assert set(registry.models) == {"Robusta/opus-4-6"}


def test_default_only_change_is_reported_as_a_change(build_registry):
    """Same model names, new default: the registry did change (CodeRabbit ROB-795)."""
    registry = build_registry(
        _catalog("Robusta/opus-4-6", "Robusta/gpt-5", default="Robusta/opus-4-6")
    )

    changed = _refresh_with(
        registry, _catalog("Robusta/opus-4-6", "Robusta/gpt-5", default="Robusta/gpt-5")
    )

    assert changed
    assert registry.default_robusta_model == "Robusta/gpt-5"


def test_repeated_failures_log_only_the_first_and_every_nth(build_registry):
    """An outage that outlives several 300s refresh cycles must not log the
    same failure every cycle (review feedback on ROB-795)."""
    registry = build_registry(_catalog("Robusta/opus-4-6", default="Robusta/opus-4-6"))

    log_failure_calls = []
    with patch(
        "holmes.core.llm.fetch_robusta_models",
        side_effect=lambda *a, **kw: log_failure_calls.append(kw["log_failure"])
        or None,
    ):
        for _ in range(6):
            assert not registry.refresh_robusta_models()

    # 1st failure logs; 2nd-5th are suppressed; the 6th (one full interval
    # later) logs again.
    assert log_failure_calls == [True, False, False, False, False, True]


def test_skipped_when_robusta_ai_is_disabled(build_registry):
    registry = build_registry(
        None,
        file_models={"my-model": ModelEntry(model="azure/gpt-4", name="my-model")},
        robusta_ai=False,
    )
    assert set(registry.models) == {"my-model"}

    with patch("holmes.core.llm.ROBUSTA_AI", False):
        changed = _refresh_with(registry, _catalog("Robusta/gpt-5"))

    assert not changed
    assert set(registry.models) == {"my-model"}


@patch("holmes.core.llm.ROBUSTA_AI", True)
@patch("holmes.config.Config._Config__get_cluster_name", return_value="test-cluster")
def test_heartbeat_advertises_the_refreshed_catalog(mock_cluster, monkeypatch):
    """The ROB-707 acceptance criterion: what a refresh loads is what the next
    HolmesStatus upsert advertises, without a pod restart."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.setattr(LLMModelRegistry, "_parse_models_file", lambda self, path: {})

    dal = _dal()
    with patch(
        "holmes.core.llm.fetch_robusta_models",
        return_value=_catalog("Playtika-sonnet-4-5", default="Playtika-sonnet-4-5"),
    ):
        config = Config.load_from_env()
        config._dal = dal
        assert config.llm_model_registry.models  # boot loaded the old catalog

    # Ops edit the catalog in the relay: 4-5 deleted, 5 added.
    with patch(
        "holmes.core.llm.fetch_robusta_models",
        return_value=_catalog("Playtika-sonnet-5", default="Playtika-sonnet-5"),
    ):
        assert config.llm_model_registry.refresh_robusta_models()

    update_holmes_status_in_db(dal, config)

    advertised = json.loads(dal.upsert_holmes_status.call_args[0][0]["model"])
    assert advertised == ["Playtika-sonnet-5"]

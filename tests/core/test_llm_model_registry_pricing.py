"""Tests for custom-model pricing registration in LLMModelRegistry."""

from unittest.mock import MagicMock

import litellm
import pytest
from pydantic import SecretStr

from holmes.config import Config
from holmes.core.llm import LLMModelRegistry, ModelEntry


@pytest.fixture
def mock_config(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MODEL", raising=False)
    config = MagicMock(spec=Config)
    config.should_try_robusta_ai = False
    config.model = None
    config.cluster_name = None
    config.api_base = None
    config.api_version = None
    config.api_key = None
    return config


@pytest.fixture
def mock_dal():
    dal = MagicMock()
    dal.enabled = False
    dal.account_id = None
    return dal


@pytest.fixture(autouse=True)
def _reset_pricing_warning_cache(monkeypatch):
    """Reset the per-process 'already warned' set between tests."""
    monkeypatch.setattr(
        "holmes.core.llm._warned_unknown_cost_models", set()
    )


@pytest.fixture
def _snapshot_litellm_model_cost(monkeypatch):
    """Snapshot litellm.model_cost so test-local registrations don't leak."""
    original = dict(litellm.model_cost)
    yield
    litellm.model_cost.clear()
    litellm.model_cost.update(original)


def _patch_models_file(monkeypatch, entries: dict):
    monkeypatch.setattr(
        "holmes.core.llm.LLMModelRegistry._parse_models_file",
        lambda self, path: entries,
    )


class TestUserPricingRegistration:
    def test_user_pricing_registers_in_litellm_model_cost(
        self,
        mock_config,
        mock_dal,
        monkeypatch,
        _snapshot_litellm_model_cost,
    ):
        """User pricing in model_extra reaches litellm.model_cost under the litellm name."""
        entry = ModelEntry.model_validate(
            {
                "model": "openai/my-internal-opus",
                "name": "internal-opus",
                "api_key": "k",
                "input_cost_per_token": 0.000003,
                "output_cost_per_token": 0.000015,
            }
        )
        _patch_models_file(monkeypatch, {"internal-opus": entry})

        LLMModelRegistry(mock_config, mock_dal)

        registered = litellm.model_cost.get("openai/my-internal-opus")
        assert registered is not None
        assert registered["input_cost_per_token"] == pytest.approx(0.000003)
        assert registered["output_cost_per_token"] == pytest.approx(0.000015)

    def test_cache_pricing_fields_are_registered(
        self,
        mock_config,
        mock_dal,
        monkeypatch,
        _snapshot_litellm_model_cost,
    ):
        entry = ModelEntry.model_validate(
            {
                "model": "openai/cached-opus",
                "input_cost_per_token": 0.000003,
                "output_cost_per_token": 0.000015,
                "cache_creation_input_token_cost": 0.00000375,
                "cache_read_input_token_cost": 0.0000003,
            }
        )
        _patch_models_file(monkeypatch, {"m": entry})

        LLMModelRegistry(mock_config, mock_dal)

        registered = litellm.model_cost["openai/cached-opus"]
        assert registered["cache_creation_input_token_cost"] == pytest.approx(
            0.00000375
        )
        assert registered["cache_read_input_token_cost"] == pytest.approx(0.0000003)

    def test_partial_pricing_is_ignored(
        self,
        mock_config,
        mock_dal,
        monkeypatch,
        _snapshot_litellm_model_cost,
    ):
        """If only one of input/output cost is set, do not register pricing."""
        entry = ModelEntry.model_validate(
            {
                "model": "openai/partial-cost-model",
                "input_cost_per_token": 0.000003,
                # output_cost_per_token deliberately missing
            }
        )
        _patch_models_file(monkeypatch, {"m": entry})

        LLMModelRegistry(mock_config, mock_dal)

        # Should not have registered (incomplete pricing).
        assert "openai/partial-cost-model" not in litellm.model_cost

    def test_robusta_entry_uses_corrected_litellm_name(
        self,
        mock_config,
        mock_dal,
        monkeypatch,
        _snapshot_litellm_model_cost,
    ):
        """For Robusta entries, pricing must register under the openai/<id> name."""
        entry = ModelEntry.model_validate(
            {
                "model": "anthropic/opus-4.6",
                "is_robusta_model": True,
                "input_cost_per_token": 0.000004,
                "output_cost_per_token": 0.000020,
            }
        )
        _patch_models_file(monkeypatch, {"Robusta/opus-4.6": entry})

        LLMModelRegistry(mock_config, mock_dal)

        # OpenAI_LLM.get_litellm_corrected_name_for_robusta_ai rewrites
        # "anthropic/opus-4.6" to "openai/opus-4.6", so pricing must land there.
        assert "openai/opus-4.6" in litellm.model_cost
        assert litellm.model_cost["openai/opus-4.6"][
            "input_cost_per_token"
        ] == pytest.approx(0.000004)
        # The original anthropic name should NOT have been registered.
        assert litellm.model_cost.get("anthropic/opus-4.6", {}).get(
            "input_cost_per_token"
        ) != pytest.approx(0.000004)


class TestRobustaAutoLookup:
    """Robusta entries pull pricing from litellm.model_cost automatically
    using the *real* upstream model name, without any hand-maintained table."""

    def test_robusta_entry_pulls_pricing_from_bundled_cost_map(
        self,
        mock_config,
        mock_dal,
        monkeypatch,
        _snapshot_litellm_model_cost,
    ):
        """For a Robusta bedrock/<...> entry, the bundled Bedrock pricing
        gets copied under the corrected openai/<...> name automatically."""
        # Seed a fake bundled entry so we don't depend on whatever Bedrock
        # prices ship in this version of litellm.
        litellm.model_cost["us.fake.opus-test-v1"] = {
            "input_cost_per_token": 6e-06,
            "output_cost_per_token": 3e-05,
            "cache_read_input_token_cost": 6e-07,
            "litellm_provider": "bedrock",
            "mode": "chat",
        }
        entry = ModelEntry.model_validate(
            {
                "model": "bedrock/us.fake.opus-test-v1",
                "is_robusta_model": True,
            }
        )
        _patch_models_file(monkeypatch, {"Robusta/opus-test": entry})

        LLMModelRegistry(mock_config, mock_dal)

        registered = litellm.model_cost.get("openai/us.fake.opus-test-v1")
        assert registered is not None
        assert registered["input_cost_per_token"] == pytest.approx(6e-06)
        assert registered["output_cost_per_token"] == pytest.approx(3e-05)
        assert registered["cache_read_input_token_cost"] == pytest.approx(6e-07)

    def test_auto_lookup_uses_exact_underlying_name_not_normalized(
        self,
        mock_config,
        mock_dal,
        monkeypatch,
        _snapshot_litellm_model_cost,
    ):
        """Regional `us.` prefix must be preserved -- we register the
        regional price tier, not the non-regional wholesale tier."""
        litellm.model_cost["us.fake.test-regional"] = {
            "input_cost_per_token": 5.5e-06,  # 1.1x regional premium
            "output_cost_per_token": 2.75e-05,
            "litellm_provider": "bedrock",
            "mode": "chat",
        }
        litellm.model_cost["fake.test-regional"] = {
            "input_cost_per_token": 5e-06,  # wholesale
            "output_cost_per_token": 2.5e-05,
            "litellm_provider": "bedrock",
            "mode": "chat",
        }
        entry = ModelEntry.model_validate(
            {
                "model": "bedrock/us.fake.test-regional",
                "is_robusta_model": True,
            }
        )
        _patch_models_file(monkeypatch, {"Robusta/test": entry})

        LLMModelRegistry(mock_config, mock_dal)

        registered = litellm.model_cost["openai/us.fake.test-regional"]
        # Regional tier, NOT the cheaper wholesale tier.
        assert registered["input_cost_per_token"] == pytest.approx(5.5e-06)
        assert registered["output_cost_per_token"] == pytest.approx(2.75e-05)

    def test_user_pricing_overrides_auto_lookup(
        self,
        mock_config,
        mock_dal,
        monkeypatch,
        _snapshot_litellm_model_cost,
    ):
        """User-configured pricing wins even when auto-lookup would succeed."""
        litellm.model_cost["us.fake.user-override"] = {
            "input_cost_per_token": 1e-05,
            "output_cost_per_token": 5e-05,
            "litellm_provider": "bedrock",
            "mode": "chat",
        }
        entry = ModelEntry.model_validate(
            {
                "model": "bedrock/us.fake.user-override",
                "is_robusta_model": True,
                "input_cost_per_token": 7e-07,
                "output_cost_per_token": 3e-06,
            }
        )
        _patch_models_file(monkeypatch, {"Robusta/override": entry})

        LLMModelRegistry(mock_config, mock_dal)

        registered = litellm.model_cost["openai/us.fake.user-override"]
        assert registered["input_cost_per_token"] == pytest.approx(7e-07)
        assert registered["output_cost_per_token"] == pytest.approx(3e-06)

    def test_no_auto_lookup_for_non_robusta_entries(
        self,
        mock_config,
        mock_dal,
        monkeypatch,
        _snapshot_litellm_model_cost,
    ):
        """A direct (non-Robusta) entry uses litellm's lookup as-is; we
        don't shadow-register a separate openai/ alias for it."""
        entry = ModelEntry.model_validate(
            {
                "model": "bedrock/us.fake.direct",
                "is_robusta_model": False,
            }
        )
        litellm.model_cost["us.fake.direct"] = {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 5e-06,
            "litellm_provider": "bedrock",
            "mode": "chat",
        }
        _patch_models_file(monkeypatch, {"direct": entry})

        LLMModelRegistry(mock_config, mock_dal)

        # No openai/ alias should appear -- direct entries hit litellm
        # natively without the Robusta name correction.
        assert "openai/us.fake.direct" not in litellm.model_cost


class TestUnknownModelWarning:
    def test_warns_once_for_unknown_unpriced_model(
        self,
        mock_config,
        mock_dal,
        monkeypatch,
        caplog,
        _snapshot_litellm_model_cost,
    ):
        """Operator gets one INFO line per un-priced unknown model."""
        entry = ModelEntry(
            model="openai/unknown-model-xyz",
            name="unknown",
            api_key=SecretStr("k"),
        )
        _patch_models_file(monkeypatch, {"unknown": entry})

        with caplog.at_level("INFO", logger="root"):
            LLMModelRegistry(mock_config, mock_dal)

        matching = [
            r
            for r in caplog.records
            if "openai/unknown-model-xyz" in r.getMessage()
            and "no entry in litellm's cost map" in r.getMessage()
        ]
        assert len(matching) == 1

    def test_no_warning_for_stock_model(
        self,
        mock_config,
        mock_dal,
        monkeypatch,
        caplog,
        _snapshot_litellm_model_cost,
    ):
        """Stock litellm-known models (e.g. gpt-4o) don't trigger the warning."""
        entry = ModelEntry(model="gpt-4o", name="gpt4o", api_key=SecretStr("k"))
        _patch_models_file(monkeypatch, {"gpt4o": entry})

        with caplog.at_level("INFO", logger="root"):
            LLMModelRegistry(mock_config, mock_dal)

        warnings = [
            r
            for r in caplog.records
            if "no entry in litellm's cost map" in r.getMessage()
        ]
        assert warnings == []

    def test_no_warning_when_pricing_is_configured(
        self,
        mock_config,
        mock_dal,
        monkeypatch,
        caplog,
        _snapshot_litellm_model_cost,
    ):
        entry = ModelEntry.model_validate(
            {
                "model": "openai/unknown-but-priced",
                "input_cost_per_token": 0.000003,
                "output_cost_per_token": 0.000015,
            }
        )
        _patch_models_file(monkeypatch, {"m": entry})

        with caplog.at_level("INFO", logger="root"):
            LLMModelRegistry(mock_config, mock_dal)

        warnings = [
            r
            for r in caplog.records
            if "no entry in litellm's cost map" in r.getMessage()
        ]
        assert warnings == []

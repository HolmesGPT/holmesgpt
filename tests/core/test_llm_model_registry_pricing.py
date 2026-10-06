"""Tests for custom-model pricing registration in LLMModelRegistry."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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


def _fake_bundled(name: str, in_cost: float, out_cost: float) -> None:
    litellm.model_cost[name] = {
        "input_cost_per_token": in_cost,
        "output_cost_per_token": out_cost,
        "litellm_provider": "bedrock",
        "mode": "chat",
    }


class TestRobustaUpstreamNameShapes:
    """ROB-1237: every shape of upstream name relay's catalog serves must
    resolve to a priced, collision-free litellm name."""

    @pytest.mark.parametrize(
        "upstream, expected",
        [
            ("gpt-4o", "gpt-4o"),
            (
                "bedrock/us.anthropic.claude-opus-4-7",
                "openai/us.anthropic.claude-opus-4-7",
            ),
            ("openai/claude-opus-4-8", "openai/claude-opus-4-8"),
            (
                "bedrock/converse/us.anthropic.claude-fable-5",
                "openai/us.anthropic.claude-fable-5",
            ),
            (
                "bedrock/mantle/anthropic.claude-fable-5",
                "openai/anthropic.claude-fable-5",
            ),
            ("novita/openai/gpt-oss-120b", "openai/gpt-oss-120b"),
        ],
    )
    def test_completion_name_matches_pricing_name(self, upstream, expected):
        from holmes.core.llm import DefaultLLM, _litellm_name_for_entry

        entry = ModelEntry(model=upstream, name="Robusta/x", is_robusta_model=True)
        llm = DefaultLLM.__new__(DefaultLLM)
        llm.model = upstream
        llm.is_robusta_model = True

        assert _litellm_name_for_entry(entry) == expected
        assert llm.get_litellm_corrected_name_for_robusta_ai() == expected

    def test_openai_prefixed_upstream_gets_bundled_pricing(
        self, mock_config, mock_dal, monkeypatch, _snapshot_litellm_model_cost
    ):
        """A gateway entry whose upstream is already `openai/<id>` used to skip
        the auto-lookup and always record cost 0."""
        _fake_bundled("fake-gateway-opus", 5e-06, 2.5e-05)
        entry = ModelEntry(
            model="openai/fake-gateway-opus", name="g", is_robusta_model=True
        )
        _patch_models_file(monkeypatch, {"Robusta/gw": entry})

        LLMModelRegistry(mock_config, mock_dal)

        registered = litellm.model_cost["openai/fake-gateway-opus"]
        assert registered["input_cost_per_token"] == pytest.approx(5e-06)
        assert registered["output_cost_per_token"] == pytest.approx(2.5e-05)

    def test_route_segment_upstreams_get_distinct_pricing(
        self, mock_config, mock_dal, monkeypatch, _snapshot_litellm_model_cost
    ):
        """`bedrock/converse/<id>` and `bedrock/mantle/<id>` used to collapse to
        `openai/converse` / `openai/mantle`: unpriced, and shared by every model
        on that route."""
        _fake_bundled("us.fake.fable-a", 1e-06, 2e-06)
        _fake_bundled("fake.fable-b", 3e-06, 4e-06)
        _patch_models_file(
            monkeypatch,
            {
                "Robusta/a": ModelEntry(
                    model="bedrock/converse/us.fake.fable-a", is_robusta_model=True
                ),
                "Robusta/b": ModelEntry(
                    model="bedrock/mantle/fake.fable-b", is_robusta_model=True
                ),
            },
        )

        LLMModelRegistry(mock_config, mock_dal)

        a = litellm.model_cost["openai/us.fake.fable-a"]
        b = litellm.model_cost["openai/fake.fable-b"]
        assert (a["input_cost_per_token"], a["output_cost_per_token"]) == (1e-06, 2e-06)
        assert (b["input_cost_per_token"], b["output_cost_per_token"]) == (3e-06, 4e-06)
        assert "openai/converse" not in litellm.model_cost
        assert "openai/mantle" not in litellm.model_cost

    def test_full_upstream_name_wins_over_bare_id(
        self, mock_config, mock_dal, monkeypatch, _snapshot_litellm_model_cost
    ):
        """A provider-specific price (e.g. Novita's) beats the generic one."""
        _fake_bundled("novita/openai/fake-oss", 1e-07, 2e-07)
        _fake_bundled("fake-oss", 9e-07, 9e-07)
        _patch_models_file(
            monkeypatch,
            {
                "Robusta/oss": ModelEntry(
                    model="novita/openai/fake-oss", is_robusta_model=True
                )
            },
        )

        LLMModelRegistry(mock_config, mock_dal)

        registered = litellm.model_cost["openai/fake-oss"]
        assert registered["input_cost_per_token"] == pytest.approx(1e-07)

    def test_unpriced_longer_match_falls_through_to_priced_id(
        self, mock_config, mock_dal, monkeypatch, _snapshot_litellm_model_cost
    ):
        litellm.model_cost["converse/us.fake.no-price"] = {
            "litellm_provider": "bedrock",
            "mode": "chat",
        }
        _fake_bundled("us.fake.no-price", 2e-06, 8e-06)
        _patch_models_file(
            monkeypatch,
            {
                "Robusta/np": ModelEntry(
                    model="bedrock/converse/us.fake.no-price", is_robusta_model=True
                )
            },
        )

        LLMModelRegistry(mock_config, mock_dal)

        registered = litellm.model_cost["openai/us.fake.no-price"]
        assert registered["input_cost_per_token"] == pytest.approx(2e-06)

    def test_unpriced_upstream_still_warns(
        self, mock_config, mock_dal, monkeypatch, caplog, _snapshot_litellm_model_cost
    ):
        _patch_models_file(
            monkeypatch,
            {
                "Robusta/x": ModelEntry(
                    model="azure/fake-unpriced-deployment", is_robusta_model=True
                )
            },
        )

        with caplog.at_level("INFO", logger="root"):
            LLMModelRegistry(mock_config, mock_dal)

        assert "openai/fake-unpriced-deployment" not in litellm.model_cost
        assert any(
            "openai/fake-unpriced-deployment" in r.getMessage()
            and "no entry in litellm's cost map" in r.getMessage()
            for r in caplog.records
        )


@pytest.fixture
def fake_relay(monkeypatch):
    """OpenAI-compatible stand-in for relay's /llm/<model> proxy."""
    requests_seen: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests_seen.append(body)
            payload = json.dumps(
                {
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "hi"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 500,
                        "total_tokens": 1500,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    yield f"http://127.0.0.1:{server.server_port}", requests_seen
    server.shutdown()


class TestRobustaCostEndToEnd:
    """A completion through the Robusta proxy records the upstream model's
    price, for every upstream name shape relay serves."""

    @pytest.mark.parametrize(
        "upstream, model_id",
        [
            ("bedrock/us.fake.e2e-opus", "us.fake.e2e-opus"),
            ("openai/fake-e2e-gateway", "fake-e2e-gateway"),
            ("bedrock/converse/us.fake.e2e-fable", "us.fake.e2e-fable"),
            ("novita/openai/fake-e2e-oss", "fake-e2e-oss"),
        ],
    )
    def test_completion_records_upstream_price(
        self,
        upstream,
        model_id,
        fake_relay,
        mock_config,
        mock_dal,
        monkeypatch,
        _snapshot_litellm_model_cost,
    ):
        from holmes.core.llm import DefaultLLM
        from holmes.core.llm_usage import RequestStats

        base_url, requests_seen = fake_relay
        _fake_bundled(model_id, 1e-06, 2e-06)
        _patch_models_file(
            monkeypatch,
            {"Robusta/m": ModelEntry(model=upstream, is_robusta_model=True)},
        )
        LLMModelRegistry(mock_config, mock_dal)

        llm = DefaultLLM(
            model=upstream,
            api_key="acct token",
            api_base=base_url,
            is_robusta_model=True,
        )
        response = llm.completion(messages=[{"role": "user", "content": "hi"}])

        stats = RequestStats.from_response(response)
        assert stats.total_tokens == 1500
        assert stats.total_cost == pytest.approx(1000 * 1e-06 + 500 * 2e-06)
        assert requests_seen[0]["model"] == model_id

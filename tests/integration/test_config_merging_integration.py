"""
Integration tests for the fast_model class-level default flow.

Tests that Config.fast_model → LLMSummarizeTransformer._default_fast_model
correctly causes new transformer instances to use the default fast model.
"""

from unittest.mock import patch

from holmes.core.tools import ToolsetTag, YAMLTool, YAMLToolset
from holmes.core.toolset_manager import ToolsetManager
from holmes.core.transformers import Transformer
from holmes.core.transformers.llm_summarize import LLMSummarizeTransformer


class TestFastModelClassDefault:
    """Tests for the class-level default fast model on LLMSummarizeTransformer."""

    def setup_method(self):
        self._original = LLMSummarizeTransformer._default_fast_model

    def teardown_method(self):
        LLMSummarizeTransformer._default_fast_model = self._original

    def test_class_default_used_when_no_instance_fast_model(self):
        """Transformer instances without fast_model use the class default."""
        LLMSummarizeTransformer.set_default_fast_model("gpt-4o-mini")

        with patch(
            "holmes.core.transformers.llm_summarize.DefaultLLM"
        ) as mock_llm:
            instance = LLMSummarizeTransformer(input_threshold=1000)
            mock_llm.assert_called_once_with("gpt-4o-mini", None)
            assert instance._fast_llm is not None

    def test_instance_fast_model_overrides_class_default(self):
        """Per-instance fast_model takes precedence over class default."""
        LLMSummarizeTransformer.set_default_fast_model("gpt-4o-mini")

        with patch(
            "holmes.core.transformers.llm_summarize.DefaultLLM"
        ) as mock_llm:
            instance = LLMSummarizeTransformer(
                input_threshold=1000, fast_model="claude-haiku"
            )
            mock_llm.assert_called_once_with("claude-haiku", None)

    def test_no_class_default_no_instance_fast_model(self):
        """Without class default or instance fast_model, no LLM is created."""
        LLMSummarizeTransformer._default_fast_model = None

        with patch(
            "holmes.core.transformers.llm_summarize.DefaultLLM"
        ) as mock_llm:
            instance = LLMSummarizeTransformer(input_threshold=1000)
            mock_llm.assert_not_called()
            assert instance._fast_llm is None


class TestToolsetManagerWithoutFastModelInjection:
    """Verify ToolsetManager no longer injects fast_model into transformer configs."""

    def test_toolsets_loaded_without_global_fast_model_param(self):
        """ToolsetManager works without global_fast_model parameter."""
        toolset = YAMLToolset(
            name="test_toolset",
            tags=[ToolsetTag.CORE],
            description="Test toolset",
            tools=[
                YAMLTool(
                    name="test_tool",
                    description="Test",
                    command="echo test",
                    transformers=[
                        Transformer(
                            name="llm_summarize",
                            config={"input_threshold": 1000},
                        )
                    ],
                )
            ],
        )

        with patch("holmes.core.toolset_manager.load_builtin_toolsets") as mock_load:
            mock_load.return_value = [toolset]
            manager = ToolsetManager()
            toolsets = manager._list_all_toolsets(check_prerequisites=False)

            result_tool = toolsets[0].tools[0]
            config = {t.name: t.config for t in result_tool.transformers}

            # No global_fast_model should be injected into config
            assert "global_fast_model" not in config["llm_summarize"]
            assert config["llm_summarize"]["input_threshold"] == 1000

    def test_backward_compatibility_toolsets_without_transformers(self):
        """Toolsets without transformers still work correctly."""
        simple_toolset = YAMLToolset(
            name="simple_toolset",
            tags=[ToolsetTag.CORE],
            description="Simple toolset without transformers",
            tools=[
                YAMLTool(name="simple_tool", description="Simple", command="echo")
            ],
        )

        with patch("holmes.core.toolset_manager.load_builtin_toolsets") as mock_load:
            mock_load.return_value = [simple_toolset]
            manager = ToolsetManager()
            toolsets = manager._list_all_toolsets(check_prerequisites=False)

            result_toolset = toolsets[0]
            assert result_toolset.transformers is None
            assert result_toolset.tools[0].transformers is None

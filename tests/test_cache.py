# type: ignore
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List
from unittest.mock import patch

import litellm
import pytest

from holmes.config import Config
from holmes.core.conversations import build_chat_messages
from holmes.core.llm import DefaultLLM
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools_utils.tool_executor import ToolExecutor
from tests.llm.utils.mock_dal import load_mock_dal
from tests.llm.utils.mock_toolset import (
    MockGenerationConfig,
    MockMode,
    MockToolsetManager,
)
from tests.llm.utils.test_case_utils import get_models

logger = logging.getLogger(__name__)


def extract_cached_tokens_from_dict(usage: Dict[str, Any]) -> int:
    prompt_details = usage.get("prompt_tokens_details", {})
    return prompt_details.get("cached_tokens", 0)


def extract_cached_tokens_from_object(usage: Any) -> int:
    if not hasattr(usage, "prompt_tokens_details"):
        return 0
    prompt_details = usage.prompt_tokens_details
    if not hasattr(prompt_details, "cached_tokens"):
        return 0
    return prompt_details.cached_tokens or 0


def get_cached_tokens(raw_response: Any) -> int:
    if not hasattr(raw_response, "usage") or not raw_response.usage:
        return 0
    usage = raw_response.usage
    if isinstance(usage, dict):
        return extract_cached_tokens_from_dict(usage)
    return extract_cached_tokens_from_object(usage)


def get_prompt_tokens(raw_response: Any) -> int:
    if not hasattr(raw_response, "usage") or not raw_response.usage:
        return 0
    usage = raw_response.usage
    if isinstance(usage, dict):
        return usage.get("prompt_tokens", 0)
    return getattr(usage, "prompt_tokens", 0)


def extract_cached_tokens_list(raw_responses: List[Any]) -> List[int]:
    return [get_cached_tokens(response) for response in raw_responses]


def extract_prompt_tokens_list(raw_responses: List[Any]) -> List[int]:
    return [get_prompt_tokens(response) for response in raw_responses]


@pytest.mark.llm
@pytest.mark.parametrize("model", get_models())
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_cached_output(model: str, request):
    models_str = os.environ.get("MODEL", model)
    test_model = models_str.split(",")[0].strip() if "," in models_str else model

    env_check = litellm.validate_environment(model=test_model)
    if not env_check["keys_in_environment"]:
        pytest.skip(
            f"Missing API keys for model {test_model}. Required: {', '.join(env_check['missing_keys'])}"
        )

    raw_responses: List[Any] = []
    original_litellm_completion = litellm.completion

    def capture_litellm_completion(*args, **kwargs):
        result = original_litellm_completion(*args, **kwargs)
        raw_responses.append(result)
        return result

    with patch.object(litellm, "completion", side_effect=capture_litellm_completion):
        llm = DefaultLLM(model, tracer=None)
        mock_generation_config = MockGenerationConfig(
            generate_mocks_enabled=False,
            regenerate_all_enabled=False,
            mock_mode=MockMode.MOCK,
        )

        temp_dir = TemporaryDirectory()
        try:
            toolset_manager = MockToolsetManager(
                test_case_folder=str(temp_dir.name),
                mock_generation_config=mock_generation_config,
                request=request,
            )
            tool_executor = ToolExecutor(toolset_manager.toolsets)
            ai = ToolCallingLLM(tool_executor=tool_executor, max_steps=1, llm=llm)
            config = Config()

            mock_dal = load_mock_dal(
                Path(temp_dir.name), generate_mocks=False, initialize_base=False
            )
            runbooks = config.get_runbook_catalog()

            asks = [
                "how many pods are running?",
                "what is the status of the cluster?",
                "show me the recent events",
                "list all namespaces",
            ]
            conversation_history: List[Dict[str, Any]] = None

            for iteration, ask in enumerate(asks):
                global_instructions = mock_dal.get_global_instructions_for_account()
                messages = build_chat_messages(
                    ask=ask,
                    conversation_history=conversation_history,
                    ai=ai,
                    config=config,
                    global_instructions=global_instructions,
                    additional_system_prompt=None,
                    runbooks=runbooks,
                )
                result = ai.messages_call(messages=messages, trace_span=None)
                assert result is not None
                assert len(raw_responses) >= iteration + 1
                conversation_history = messages.copy()
                conversation_history.append(
                    {"role": "assistant", "content": result.result or ""}
                )

            cached_tokens_list = extract_cached_tokens_list(raw_responses)
            prompt_tokens_list = extract_prompt_tokens_list(raw_responses)

            for i, (cached_tokens, prompt_tokens) in enumerate(
                zip(cached_tokens_list, prompt_tokens_list)
            ):
                current_cache_ratio = (
                    cached_tokens / prompt_tokens if prompt_tokens > 0 else 0
                )
                logger.info(
                    f"Call {i+1}: {cached_tokens} cached tokens, {prompt_tokens} prompt tokens ({current_cache_ratio:.1%} of prompt was from cache)"
                )
                if i > 0:
                    prev_prompt = prompt_tokens_list[i - 1]
                    prev_cache_ratio = (
                        cached_tokens / prev_prompt if prev_prompt > 0 else 0
                    )
                    logger.info(
                        f"  {prev_cache_ratio:.1%} of previous prompt was cached"
                    )
                    if prev_cache_ratio > 1.0:
                        logger.info(
                            f"  Note: Cache exceeds previous prompt because it accumulates tokens from entire conversation history (calls 1-{i+1}), not just the previous call"
                        )

            if not any(cached_tokens_list):
                pytest.skip("No cached tokens found in responses")

            assert (
                len(cached_tokens_list) >= 2
            ), "Need at least 2 responses to compare cached tokens"

            for i in range(len(cached_tokens_list) - 1):
                if cached_tokens_list[i + 1] > cached_tokens_list[i]:
                    expected_min_cache = prompt_tokens_list[i] * 0.95
                    assert (
                        cached_tokens_list[i + 1] >= expected_min_cache
                    ), f"Call {i+2}: cached tokens ({cached_tokens_list[i+1]}) must be at least 95% of previous call's prompt tokens ({prompt_tokens_list[i]}), expected at least {expected_min_cache:.0f}"

            assert (
                cached_tokens_list[-1] > 0
            ), f"Expected cached tokens > 0 in last response, but got {cached_tokens_list[-1]}"
        finally:
            temp_dir.cleanup()

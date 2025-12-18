import json
import os
from pathlib import Path

import pytest

from holmes.core.llm import DefaultLLM
from holmes.core.truncation.compaction import compact_conversation_history

CONVERSATION_HISTORY_FILE_PATH = (
    Path(__file__).parent / "conversation_history_for_compaction.json"
)

# Skip tests if Azure credentials are not available
pytestmark = pytest.mark.skipif(
    not all(
        [
            os.environ.get("AZURE_API_BASE"),
            os.environ.get("AZURE_API_VERSION"),
            os.environ.get("AZURE_API_KEY"),
        ]
    ),
    reason="Azure credentials (AZURE_API_BASE, AZURE_API_VERSION, AZURE_API_KEY) are not set",
)


def test_conversation_history_compaction_system_prompt_untouched():
    llm = DefaultLLM(model=os.environ.get("model", "azure/gpt-4o"))
    with open(CONVERSATION_HISTORY_FILE_PATH) as file:
        conversation_history = json.load(file)

        system_prompt = {"role": "system", "content": "this is a system prompt"}

        conversation_history.insert(0, system_prompt)

        compacted_history = compact_conversation_history(
            original_conversation_history=conversation_history, llm=llm
        )
        assert compacted_history
        assert (
            len(compacted_history) == 4
        )  # [0]=system prompt, [1]=last user prompt, [2]=compacted content, [3]=message to continue

        assert compacted_history[0]["role"] == "system"
        assert compacted_history[0]["content"] == system_prompt["content"]

        assert compacted_history[1]["role"] == "user"

        assert compacted_history[2]["role"] == "assistant"

        assert compacted_history[3]["role"] == "system"
        assert "compacted" in compacted_history[3]["content"].lower()


def test_conversation_history_compaction():
    llm = DefaultLLM(model=os.environ.get("model", "azure/gpt-4o"))
    with open(CONVERSATION_HISTORY_FILE_PATH) as file:
        conversation_history = json.load(file)

        compacted_history = compact_conversation_history(
            original_conversation_history=conversation_history, llm=llm
        )
        assert compacted_history
        assert (
            len(compacted_history) == 3
        )  # [0]=last user prompt, [1]=compacted content, [2]=message to continue

        assert compacted_history[0]["role"] == "user"

        assert compacted_history[1]["role"] == "assistant"

        assert compacted_history[2]["role"] == "system"
        assert "compacted" in compacted_history[2]["content"].lower()

        original_tokens = llm.count_tokens(conversation_history)
        compacted_tokens = llm.count_tokens(compacted_history)
        expected_max_compacted_token_count = original_tokens.total_tokens * 0.2
        print(
            f"original_tokens={original_tokens.total_tokens} compacted_tokens={compacted_tokens.total_tokens}"
        )
        print(compacted_history[1]["content"])
        assert compacted_tokens.total_tokens < expected_max_compacted_token_count

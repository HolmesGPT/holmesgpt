"""LangChain-based LLM implementation for HolmesGPT.

This module provides a LangChain ChatOpenAI adapter that works with
OpenAI-compatible proxy endpoints for any model provider (Anthropic, OpenAI, Google, etc.).
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, Type, Union

from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import ModelResponse
from pydantic import BaseModel

from holmes.core.llm import ContextWindowUsage, LLM

logger = logging.getLogger(__name__)


class LangChainLLM(LLM):
    """LangChain chat model adapter for HolmesGPT.

    Automatically selects ChatOpenAI or ChatAnthropic based on model name,
    and supports custom base_url for proxy endpoints.

    Example:
        # For Anthropic models via proxy
        llm = LangChainLLM(
            model="anthropic--claude-4.6-sonnet",
            api_key="your-key",
            base_url="http://localhost:6655/litellm/v1"
        )

        # For OpenAI models via proxy
        llm = LangChainLLM(
            model="gpt-5",
            api_key="your-key",
            base_url="http://localhost:6655/litellm/v1"
        )
    """

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        context_window: int = 200000,
        max_output_tokens: int = 8192,
        temperature: float = 0.0,
        **kwargs
    ):
        """Initialize LangChain LLM.

        Args:
            model: Model name (e.g., "anthropic--claude-4.6-sonnet" or "gpt-5")
            api_key: API key for the endpoint
            base_url: Base URL for the OpenAI-compatible proxy endpoint
            context_window: Maximum context window size in tokens
            max_output_tokens: Maximum output tokens
            temperature: Sampling temperature
            **kwargs: Additional arguments
        """
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self._context_window = context_window
        self._max_output_tokens = max_output_tokens
        self.temperature = temperature

        # Always use ChatOpenAI since the proxy exposes an OpenAI-compatible API
        # regardless of the underlying model provider (Anthropic, OpenAI, Google, etc.)
        logger.info(f"Using ChatOpenAI for model: {model} via OpenAI-compatible proxy")
        self.client = ChatOpenAI(
            model=model,
            api_key=api_key or "dummy-key",
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_output_tokens,
            **kwargs
        )

        logger.info(
            f"Initialized LangChain LLM: model={model}, base_url={base_url}, "
            f"context={context_window}, max_output={max_output_tokens}"
        )

    def get_context_window_size(self) -> int:
        """Return the context window size."""
        return self._context_window

    def get_maximum_output_token(self) -> int:
        """Return the maximum output tokens."""
        return self._max_output_tokens

    def count_tokens(
        self, messages: list[dict], tools: Optional[list[dict[str, Any]]] = None
    ) -> ContextWindowUsage:
        """Count tokens in messages and tools.

        Note: This is a rough approximation based on character count.
        """
        # Simple approximation: ~4 chars per token
        total_chars = 0
        system_chars = 0
        user_chars = 0
        assistant_chars = 0
        tool_chars = 0

        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                # Handle multimodal content
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        content = item.get("text", "")
                        break
                else:
                    content = ""

            chars = len(str(content))
            total_chars += chars

            role = msg.get("role", "")
            if role == "system":
                system_chars += chars
            elif role == "user":
                user_chars += chars
            elif role == "assistant":
                assistant_chars += chars
            elif role == "tool":
                tool_chars += chars

        # Rough estimate: 4 chars per token
        system_tokens = system_chars // 4
        user_tokens = user_chars // 4
        assistant_tokens = assistant_chars // 4
        tool_tokens = tool_chars // 4

        # Estimate tool definition tokens
        tools_to_call_tokens = 0
        if tools:
            tools_json = json.dumps(tools)
            tools_to_call_tokens = len(tools_json) // 4

        total_tokens = (
            system_tokens + user_tokens + assistant_tokens +
            tool_tokens + tools_to_call_tokens
        )

        return ContextWindowUsage(
            total_tokens=total_tokens,
            tools_tokens=tool_tokens,
            system_tokens=system_tokens,
            user_tokens=user_tokens,
            tools_to_call_tokens=tools_to_call_tokens,
            assistant_tokens=assistant_tokens,
            other_tokens=0,
        )

    def _convert_messages_to_langchain(
        self, messages: List[Dict[str, Any]]
    ) -> List[Union[SystemMessage, HumanMessage, AIMessage, ToolMessage]]:
        """Convert OpenAI-style messages to LangChain format."""
        langchain_messages = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            # Handle content that might be a list (multimodal)
            if isinstance(content, list):
                # Extract text content only
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                content = "\n".join(text_parts)

            if role == "system":
                langchain_messages.append(SystemMessage(content=content))
            elif role == "user":
                langchain_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    # Create AIMessage with tool calls
                    langchain_messages.append(
                        AIMessage(
                            content=content or "",
                            additional_kwargs={"tool_calls": tool_calls}
                        )
                    )
                else:
                    langchain_messages.append(AIMessage(content=content))
            elif role == "tool":
                # Tool result message
                tool_call_id = msg.get("tool_call_id", "")
                langchain_messages.append(
                    ToolMessage(content=content, tool_call_id=tool_call_id)
                )

        return langchain_messages

    def _convert_response_to_litellm_format(self, response: AIMessage) -> ModelResponse:
        """Convert LangChain AIMessage to LiteLLM ModelResponse format."""
        # Extract tool calls - LangChain stores them in the tool_calls attribute
        tool_calls = None

        # Check both locations where tool calls might be stored
        if hasattr(response, 'tool_calls') and response.tool_calls:
            # LangChain format: list of ToolCall objects
            tool_calls = []
            for tc in response.tool_calls:
                tool_calls.append({
                    "id": tc.get("id", f"call_{int(time.time())}"),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(tc.get("args", {}))
                    }
                })
        elif "tool_calls" in (response.additional_kwargs or {}):
            # Alternative location in additional_kwargs
            tool_calls = response.additional_kwargs["tool_calls"]

        # Build the response in LiteLLM format
        response_dict = {
            "id": "langchain-" + str(time.time()),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response.content,
                        "tool_calls": tool_calls,
                    },
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

        return ModelResponse(**response_dict)

    def completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, dict]] = None,
        response_format: Optional[Union[dict, Type[BaseModel]]] = None,
        temperature: Optional[float] = None,
        drop_params: Optional[bool] = None,
        stream: Optional[bool] = None,
    ) -> Union[ModelResponse, CustomStreamWrapper]:
        """Call the LLM with the given messages and tools.

        Args:
            messages: List of message dicts in OpenAI format
            tools: Optional list of tool definitions
            tool_choice: Optional tool choice
            response_format: Optional response format specification
            temperature: Optional temperature override
            drop_params: Ignored (for compatibility)
            stream: Whether to stream the response (not yet implemented)

        Returns:
            ModelResponse in LiteLLM format
        """
        if stream:
            raise NotImplementedError("Streaming not yet implemented for LangChainLLM")

        # Convert messages to LangChain format
        langchain_messages = self._convert_messages_to_langchain(messages)

        # Prepare kwargs
        kwargs = {}
        if temperature is not None:
            kwargs["temperature"] = temperature

        # Bind tools if provided
        if tools and len(tools) > 0:
            model_with_tools = self.client.bind_tools(tools)
        else:
            model_with_tools = self.client

        # Call the LLM
        try:
            logger.debug(f"Calling LangChain LLM with model={self.model}")
            response = model_with_tools.invoke(langchain_messages, **kwargs)

            # Debug: Log the response structure
            logger.debug(f"LangChain response type: {type(response)}")
            logger.debug(f"Response has tool_calls attr: {hasattr(response, 'tool_calls')}")
            if hasattr(response, 'tool_calls'):
                logger.debug(f"Tool calls: {response.tool_calls}")
            logger.debug(f"Response additional_kwargs: {response.additional_kwargs}")

            # Convert response to LiteLLM format
            result = self._convert_response_to_litellm_format(response)
            logger.debug(f"Converted to LiteLLM format, tool_calls: {result.choices[0].message.tool_calls}")
            return result

        except Exception as e:
            logger.error(f"LangChain LLM call failed: {e}", exc_info=True)
            raise

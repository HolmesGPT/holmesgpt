from typing import Any, Dict, List, Optional, Type, Union

from litellm.types.utils import ModelResponse
from pydantic import BaseModel

from holmes.core.llm import LLM, TokenCountMetadata
from holmes.core.prompt import generate_user_prompt
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import Tool
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.toolsets import load_builtin_toolsets


class MyCustomLLM(LLM):
    def get_context_window_size(self) -> int:
        return 128000

    def get_maximum_output_token(self) -> int:
        return 4096

    def count_tokens(
        self, messages: list[dict], tools: Optional[list[dict[str, Any]]] = None
    ) -> TokenCountMetadata:
        return TokenCountMetadata(
            total_tokens=1000,
            tools_to_call_tokens=100,
            system_tokens=200,
            tools_tokens=0,
            user_tokens=700,
            other_tokens=0,
            assistant_tokens=0,
        )

    def completion(  # type: ignore
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Tool]] = [],
        tool_choice: Optional[Union[str, dict]] = None,
        response_format: Optional[Union[dict, Type[BaseModel]]] = None,
        temperature: Optional[float] = None,
        drop_params: Optional[bool] = None,
    ) -> ModelResponse:
        return ModelResponse(
            choices=[
                {
                    "finish_reason": "stop",
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "There are no issues with your cluster",
                    },
                }
            ],
            usage={
                "prompt_tokens": 0,  # Integer
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        )


def ask_holmes():
    prompt = "what pods are unhealthy in my cluster?"

    system_prompt = load_and_render_prompt(
        prompt="builtin://generic_ask.jinja2", context={}
    )

    tool_executor = ToolExecutor(load_builtin_toolsets())
    ai = ToolCallingLLM(tool_executor, max_steps=40, llm=MyCustomLLM())

    user_prompt = generate_user_prompt(prompt, context={})
    response = ai.prompt_call(system_prompt, user_prompt)

    print(response.model_dump())


ask_holmes()

"""HTTP clients for Anthropic Messages and OpenAI-compatible completions."""

from types import SimpleNamespace
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from tracepilot.config import LLMConfig
from tracepilot.mcp_gateway import required_environment


class ModelServiceError(RuntimeError):
    pass


class TransientModelError(ModelServiceError):
    pass


class HTTPCompletion:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.key = required_environment(config.api_key_env)
        self.usage: list[dict[str, Any]] = []

    @retry(retry=retry_if_exception_type(TransientModelError), stop=stop_after_attempt(2),
           wait=wait_random_exponential(multiplier=0.5, max=2), reraise=True)
    def _post(self, endpoint: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = requests.post(endpoint, headers=headers, json=payload,
                                     timeout=self.config.timeout_seconds, allow_redirects=False)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientModelError("Reasoning service connection failed") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientModelError(f"Reasoning service temporarily unavailable: HTTP {response.status_code}")
        if response.status_code != 200:
            raise ModelServiceError(f"Reasoning service rejected request: HTTP {response.status_code}")
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Expected object")
            return data
        except ValueError as exc:
            raise ModelServiceError("Reasoning service returned invalid JSON") from exc

    def completion(self, **kwargs: Any) -> Any:
        messages = kwargs["messages"]
        config = self.config
        if config.provider == "anthropic":
            base = config.base_url or "https://api.anthropic.com/v1"
            result = self._post(base.rstrip("/") + "/messages", {
                "x-api-key": self.key, "anthropic-version": "2023-06-01",
            }, {"model": config.model, "max_tokens": config.max_output_tokens,
                "system": "\n".join(m["content"] for m in messages if m["role"] == "system"),
                "messages": [m for m in messages if m["role"] != "system"]})
            if result.get("stop_reason") == "max_tokens":
                raise ModelServiceError("Reasoning response exceeded its output budget")
            content = "".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
        else:
            base = config.base_url or "https://api.openai.com/v1"
            result = self._post(base.rstrip("/") + "/chat/completions", {"Authorization": f"Bearer {self.key}"}, {
                "model": config.model, "messages": messages, "response_format": {"type": "json_object"},
                "stream": False, "max_completion_tokens": config.max_output_tokens})
            choices = result.get("choices", [])
            if not choices or choices[0].get("finish_reason") != "stop":
                raise ModelServiceError("Reasoning response did not finish normally")
            content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelServiceError("Reasoning response contains no text")
        self.usage.append(result.get("usage", {}))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

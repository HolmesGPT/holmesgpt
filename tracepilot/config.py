"""Explicit operator policy; remote metadata cannot grant permissions."""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import Field, model_validator

from tracepilot.models import Budget, InvestigationScope, Record


class ScopeBinding(Record):
    service: str = "service"
    namespace: str = "namespace"
    start: str | None = "start"
    end: str | None = "end"

    @model_validator(mode="after")
    def paired_fields(self) -> "ScopeBinding":
        if (self.start is None) != (self.end is None):
            raise ValueError("Time bindings must be configured together")
        names = [n for n in (self.service, self.namespace, self.start, self.end) if n is not None]
        if any(not n for n in names) or len(set(names)) != len(names):
            raise ValueError("Scope bindings must be nonempty and distinct")
        return self

    def validate_arguments(self, args: dict[str, Any], scope: InvestigationScope) -> None:
        if args.get(self.service) != scope.service or args.get(self.namespace) != scope.namespace:
            raise PermissionError("Tool arguments exceed task service/namespace scope")
        if self.start is not None and self.end is not None:
            try:
                start = datetime.fromisoformat(args[self.start].replace("Z", "+00:00"))
                end = datetime.fromisoformat(args[self.end].replace("Z", "+00:00"))
                valid = start.utcoffset() is not None and end.utcoffset() is not None and scope.start <= start < end <= scope.end
            except (KeyError, TypeError, ValueError, AttributeError):
                valid = False
            if not valid:
                raise PermissionError("Tool arguments exceed task time window or lack a timezone")


class ToolPolicy(Record):
    read_only: Literal[True]
    retry_safe: bool = False
    approval_required: bool = False
    scope: ScopeBinding = Field(default_factory=ScopeBinding)


def validate_endpoint(url: str) -> None:
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Endpoint must be a credential-free URL without query or fragment")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("Use HTTPS or loopback HTTP")


class MCPServerConfig(Record):
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env_names: list[str] = Field(default_factory=list)
    headers_env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=20, gt=0, le=120)
    max_result_chars: int = Field(default=1_000_000, ge=100, le=5_000_000)
    tools: dict[str, ToolPolicy] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def transport_fields(self) -> "MCPServerConfig":
        if self.transport == "stdio":
            if not self.command or self.url or self.headers_env:
                raise ValueError("stdio requires command and rejects URL/headers")
        else:
            if not self.url or self.command or self.args or self.env_names:
                raise ValueError("streamable_http requires URL and rejects process fields")
            validate_endpoint(self.url)
        return self


class LLMConfig(Record):
    provider: Literal["anthropic", "openai-compatible"]
    model: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)
    base_url: str | None = None
    timeout_seconds: float = Field(default=60, gt=0, le=120)
    max_output_tokens: int = Field(default=4096, ge=256, le=16384)

    @model_validator(mode="after")
    def endpoint(self) -> "LLMConfig":
        if self.base_url:
            validate_endpoint(self.base_url)
        return self


class AgentConfig(Record):
    llm: LLMConfig
    mcp_servers: dict[str, MCPServerConfig] = Field(min_length=1, max_length=10)
    jev_model: str = "jev-latest"
    jev_api_key_env: str = "TYPESAFE_API_KEY"
    budget: Budget = Field(default_factory=Budget)

    @model_validator(mode="after")
    def namespaces(self) -> "AgentConfig":
        for name in self.mcp_servers:
            if not name or "__" in name or not name.replace("-", "").replace("_", "").isalnum():
                raise ValueError("Server names must be alphanumeric with single hyphens/underscores")
        return self

    @classmethod
    def load(cls, path: Path) -> "AgentConfig":
        if path.stat().st_size > 1_000_000:
            raise ValueError("Configuration is too large")
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

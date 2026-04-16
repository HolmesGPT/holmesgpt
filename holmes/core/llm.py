import json
import logging
import os
import threading
import time

display_logger = logging.getLogger("holmes.display.llm")
from abc import abstractmethod
from math import floor
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Type, Union

import boto3
import litellm
import sentry_sdk
from botocore.exceptions import BotoCoreError
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.litellm_core_utils.token_counter import get_image_dimensions
from litellm.types.utils import ModelResponse, TextCompletionResponse
from pydantic import BaseModel, ConfigDict, SecretStr
from typing_extensions import Self

from holmes.clients.robusta_client import (
    RobustaModel,
    RobustaModelsResponse,
    fetch_robusta_models,
)
from holmes.common.env_vars import (
    AZURE_AD_TOKEN_AUTH,
    EXTRA_HEADERS,
    FALLBACK_CONTEXT_WINDOW_SIZE,
    LLM_REQUEST_TIMEOUT,
    LOAD_ALL_ROBUSTA_MODELS,
    REASONING_EFFORT,
    ROBUSTA_AI,
    ROBUSTA_API_ENDPOINT,
    THINKING,
    TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT,
    TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_TOKENS,
)
from holmes.core.azure_token import get_azure_ad_token
from holmes.core.llm_usage import extract_usage_from_response
from holmes.core.supabase_dal import SupabaseDal
from holmes.utils.env import environ_get_safe_int, replace_env_vars_values
from holmes.utils.file_utils import load_yaml_file

if TYPE_CHECKING:
    from holmes.config import Config

MODEL_LIST_FILE_LOCATION = os.environ.get(
    "MODEL_LIST_FILE_LOCATION", "/etc/holmes/config/model_list.yaml"
)


OVERRIDE_MAX_OUTPUT_TOKEN = environ_get_safe_int("OVERRIDE_MAX_OUTPUT_TOKEN")
OVERRIDE_MAX_CONTENT_SIZE = environ_get_safe_int("OVERRIDE_MAX_CONTENT_SIZE")


def get_context_window_compaction_threshold_pct() -> int:
    """Get the compaction threshold percentage at runtime to support test overrides."""
    return environ_get_safe_int("CONTEXT_WINDOW_COMPACTION_THRESHOLD_PCT", default="95")


ROBUSTA_AI_MODEL_NAME = "Robusta"


class ContextWindowUsage(BaseModel):
    total_tokens: int
    tools_tokens: int
    system_tokens: int
    user_tokens: int
    tools_to_call_tokens: int
    assistant_tokens: int
    other_tokens: int


class ModelEntry(BaseModel):
    """ModelEntry represents a single LLM model configuration."""

    model: str
    # TODO: the name field seems to be redundant, can we remove it?
    name: Optional[str] = None
    api_key: Optional[SecretStr] = None
    base_url: Optional[str] = None
    is_robusta_model: Optional[bool] = None
    custom_args: Optional[Dict[str, Any]] = None

    # LLM configuration fields used by services like Azure AI Foundry
    api_base: Optional[str] = None
    api_version: Optional[str] = None

    model_config = ConfigDict(
        extra="allow",
    )

    @classmethod
    def load_from_dict(cls, data: dict) -> Self:
        return cls.model_validate(data)


_ANTHROPIC_MODEL_IDENTIFIERS = ("claude", "opus", "sonnet", "haiku")


def is_anthropic_model(model_name: str) -> bool:
    """Check if a model name refers to an Anthropic model.

    Returns True if 'anthropic' is in the name, or if the name contains a
    known Anthropic model family identifier (e.g. 'claude', 'opus', 'sonnet',
    'haiku'). This covers all routing prefixes like vertex_ai/, bedrock/,
    robusta/, etc.
    """
    name_lower = model_name.lower()
    if "anthropic" in name_lower:
        return True
    return any(ident in name_lower for ident in _ANTHROPIC_MODEL_IDENTIFIERS)


def _get_image_dimensions(url: str) -> Tuple[int, int]:
    """Get image dimensions from a data URI.

    Delegates to litellm's get_image_dimensions which handles URLs (via HTTP),
    base64 data URIs, and all major formats (PNG, JPEG, GIF, WebP).
    Falls back to (768, 768) on any failure.
    """
    if not url.startswith("data:"):
        return 768, 768
    try:
        return get_image_dimensions(data=url)
    except Exception:
        return 768, 768


def _anthropic_image_token_count(width: int, height: int) -> int:
    """Calculate image tokens using Anthropic's formula.

    Anthropic resizes images to fit within a 1568x1568 bounding box, then
    charges (width * height) / 750 tokens.
    See: https://platform.claude.com/docs/en/build-with-claude/vision#calculate-image-costs
    """
    max_dim = 1568
    if width > max_dim or height > max_dim:
        scale = max_dim / max(width, height)
        width = int(width * scale)
        height = int(height * scale)
    return max(1, (width * height) // 750)


def _is_image_block(block: Any) -> bool:
    """Check if a content block is an image (URL or base64 data URI).

    OpenAI's vision format uses type "image_url" for all image blocks,
    including base64 data URIs.
    """
    return isinstance(block, dict) and block.get("type") == "image_url"


def _has_images(message: dict) -> bool:
    """Check if a message contains any image content blocks."""
    content = message.get("content")
    return isinstance(content, list) and any(_is_image_block(b) for b in content)


def _strip_images(message: dict) -> dict:
    """Return a shallow copy of the message with image blocks removed."""
    content = message.get("content")
    if not isinstance(content, list):
        return message
    filtered = [b for b in content if not _is_image_block(b)]
    new_msg = dict(message)
    new_msg["content"] = filtered if filtered else ""
    return new_msg


def _count_anthropic_image_tokens(message: dict) -> int:
    """Count image tokens in a message using Anthropic's formula."""
    content = message.get("content")
    if not isinstance(content, list):
        return 0
    image_tokens = 0
    for block in content:
        if not _is_image_block(block):
            continue
        image_url = block.get("image_url", {})
        url = (
            image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
        )
        if url:
            w, h = _get_image_dimensions(url)
            image_tokens += _anthropic_image_token_count(w, h)
        else:
            image_tokens += 1600  # conservative fallback
    return image_tokens


class LLM:
    @abstractmethod
    def __init__(self):
        self.model: str  # type: ignore

    @abstractmethod
    def get_context_window_size(self) -> int:
        pass

    @abstractmethod
    def get_maximum_output_token(self) -> int:
        pass

    def get_max_token_count_for_single_tool(self) -> int:
        if (
            0 < TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT
            and TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT <= 100
        ):
            context_window_size = self.get_context_window_size()
            calculated_max_tokens = int(
                context_window_size * TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT // 100
            )
            return min(calculated_max_tokens, TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_TOKENS)
        else:
            return TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_TOKENS

    @abstractmethod
    def count_tokens(
        self, messages: list[dict], tools: Optional[list[dict[str, Any]]] = None
    ) -> ContextWindowUsage:
        pass

    @abstractmethod
    def completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = [],
        tool_choice: Optional[Union[str, dict]] = None,
        response_format: Optional[Union[dict, Type[BaseModel]]] = None,
        temperature: Optional[float] = None,
        drop_params: Optional[bool] = None,
        stream: Optional[bool] = None,
    ) -> Union[ModelResponse, CustomStreamWrapper]:
        pass


class DefaultLLM(LLM):
    model: str
    api_key: Optional[str]
    api_base: Optional[str]
    api_version: Optional[str]
    args: Dict
    is_robusta_model: bool

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        api_version: Optional[str] = None,
        args: Optional[Dict] = None,
        tracer: Optional[Any] = None,
        name: Optional[str] = None,
        is_robusta_model: bool = False,
    ):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.api_version = api_version
        self.args = args or {}
        self.tracer = tracer
        self.name = name
        self.is_robusta_model = is_robusta_model
        self.update_custom_args()
        self.check_llm(
            self.model, self.api_key, self.api_base, self.api_version, self.args
        )

    def update_custom_args(self):
        self.max_context_size = self.args.get("custom_args", {}).get("max_context_size")
        self.args.pop("custom_args", None)

    def check_llm(
        self,
        model: str,
        api_key: Optional[str],
        api_base: Optional[str],
        api_version: Optional[str],
        args: Optional[dict] = None,
    ):
        if self.is_robusta_model:
            # The model is assumed correctly configured if it is a robusta model
            # For robusta models, this code would fail because Holmes has no knowledge of the API keys
            # to azure or bedrock as all completion API calls go through robusta's LLM proxy
            return
        args = args or {}
        logging.debug(f"Checking LiteLLM model {model}")
        lookup = litellm.get_llm_provider(model)
        if not lookup:
            raise Exception(f"Unknown provider for model {model}")
        provider = lookup[1]
        if provider == "watsonx":
            # NOTE: LiteLLM's validate_environment does not currently include checks for IBM WatsonX.
            # The following WatsonX-specific variables are set based on documentation from:
            # https://docs.litellm.ai/docs/providers/watsonx
            # Required variables for WatsonX:
            # - WATSONX_URL: Base URL of your WatsonX instance (required)
            # - WATSONX_APIKEY or WATSONX_TOKEN: IBM Cloud API key or IAM auth token (one is required)
            model_requirements = {"missing_keys": [], "keys_in_environment": True}
            if api_key:
                os.environ["WATSONX_APIKEY"] = api_key
            if "WATSONX_URL" not in os.environ:
                model_requirements["missing_keys"].append("WATSONX_URL")  # type: ignore
                model_requirements["keys_in_environment"] = False
            if "WATSONX_APIKEY" not in os.environ and "WATSONX_TOKEN" not in os.environ:
                model_requirements["missing_keys"].extend(  # type: ignore
                    ["WATSONX_APIKEY", "WATSONX_TOKEN"]
                )
                model_requirements["keys_in_environment"] = False
            # WATSONX_PROJECT_ID is required because we don't let user pass it to completion call directly
            if "WATSONX_PROJECT_ID" not in os.environ:
                model_requirements["missing_keys"].append("WATSONX_PROJECT_ID")  # type: ignore
                model_requirements["keys_in_environment"] = False
            # https://docs.litellm.ai/docs/providers/watsonx#usage---models-in-deployment-spaces
            # using custom watsonx deployments might require to set WATSONX_DEPLOYMENT_SPACE_ID env
            if "watsonx/deployment/" in self.model:
                logging.warning(
                    "Custom WatsonX deployment detected. You may need to set the WATSONX_DEPLOYMENT_SPACE_ID "
                    "environment variable for proper functionality. For more information, refer to the documentation: "
                    "https://docs.litellm.ai/docs/providers/watsonx#usage---models-in-deployment-spaces"
                )
        elif provider == "bedrock":
            if (
                os.environ.get("AWS_PROFILE")
                or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
                or (os.environ.get("AWS_ROLE_ARN") and os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE"))
            ):
                model_requirements = {"keys_in_environment": True, "missing_keys": []}
            elif args.get("aws_access_key_id") and args.get("aws_secret_access_key"):
                return  # break fast.
            else:
                # Final fallback: try boto3 default credential chain
                # (covers EC2 instance profile, ECS task role, ~/.aws/credentials, etc.)
                try:
                    session = boto3.Session()
                    credentials = session.get_credentials()
                    if credentials is not None:
                        model_requirements = {"keys_in_environment": True, "missing_keys": []}
                    else:
                        model_requirements = litellm.validate_environment(
                            model=model, api_key=api_key, api_base=api_base
                        )
                except BotoCoreError:
                    model_requirements = litellm.validate_environment(
                        model=model, api_key=api_key, api_base=api_base
                    )
        elif provider == "azure":
            model_requirements = litellm.validate_environment(
                model=model, api_key=api_key, api_base=api_base, api_version=api_version
            )
            # litellm.validate_environment simply set all AZURE_* variables to missing_keys for azure models when any
            # of the variables are missing.
            # Remove AZURE_* keys from missing if they are actually set in the environment
            for key in ["AZURE_API_BASE", "AZURE_API_KEY", "AZURE_API_VERSION"]:
                if key in os.environ and key in model_requirements["missing_keys"]:
                    model_requirements["missing_keys"].remove(key)  # type: ignore
            # When using Azure AD token auth, AZURE_API_KEY is not required
            if AZURE_AD_TOKEN_AUTH and "AZURE_API_KEY" in model_requirements["missing_keys"]:
                model_requirements["missing_keys"].remove("AZURE_API_KEY")  # type: ignore

            if not model_requirements["missing_keys"]:
                model_requirements["keys_in_environment"] = True

        else:
            model_requirements = litellm.validate_environment(
                model=model, api_key=api_key, api_base=api_base
            )

        if not model_requirements["keys_in_environment"]:
            raise Exception(
                f"model {model} requires the following environment variables: {model_requirements['missing_keys']}"
            )

    def _get_model_name_variants_for_lookup(self) -> list[str]:
        """
        Generate model name variants to try when looking up in litellm.model_cost.
        Returns a list of names to try in order: exact, lowercase, without prefix, etc.
        """
        names_to_try = [self.model, self.model.lower()]

        # If there's a prefix, also try without it
        if "/" in self.model:
            base_model = self.model.split("/", 1)[1]
            names_to_try.extend([base_model, base_model.lower()])

        # Remove duplicates while preserving order (dict.fromkeys maintains insertion order in Python 3.7+)
        return list(dict.fromkeys(names_to_try))

    def get_context_window_size(self) -> int:
        if self.max_context_size:
            return self.max_context_size

        if OVERRIDE_MAX_CONTENT_SIZE:
            logging.debug(
                f"Using override OVERRIDE_MAX_CONTENT_SIZE {OVERRIDE_MAX_CONTENT_SIZE}"
            )
            return OVERRIDE_MAX_CONTENT_SIZE

        # Try each name variant
        for name in self._get_model_name_variants_for_lookup():
            try:
                return litellm.model_cost[name]["max_input_tokens"]
            except Exception:
                continue

        # Log which lookups we tried
        logging.warning(
            f"Couldn't find model {self.model} in litellm's model list (tried: {', '.join(self._get_model_name_variants_for_lookup())}), "
            f"using default {FALLBACK_CONTEXT_WINDOW_SIZE} tokens for max_input_tokens. "
            f"To override, set OVERRIDE_MAX_CONTENT_SIZE environment variable to the correct value for your model."
        )
        return FALLBACK_CONTEXT_WINDOW_SIZE

    def _is_anthropic_model(self) -> bool:
        return is_anthropic_model(self.model)

    @sentry_sdk.trace
    def count_tokens(
        self, messages: list[dict], tools: Optional[list[dict[str, Any]]] = None
    ) -> ContextWindowUsage:
        t0 = time.monotonic()

        # For Anthropic/Claude models, litellm's token counter severely underestimates
        # image tokens (uses OpenAI's 85 tokens/image instead of Anthropic's (w*h)/750).
        # We strip images before litellm counts text, then add correct image tokens.
        is_anthropic = self._is_anthropic_model()

        tools_tokens = 0
        system_tokens = 0
        assistant_tokens = 0
        user_tokens = 0
        other_tokens = 0
        tools_to_call_tokens = 0
        anthropic_image_tokens = 0
        cached_count = 0
        counted_count = 0
        for message in messages:
            # Reuse cached per-message token counts when available.
            # The cache is invalidated (key removed) whenever a message is modified (e.g. truncation).
            cached = message.get("token_count")
            if cached is not None:
                token_count = cached
                cached_count += 1
            elif is_anthropic and _has_images(message):
                stripped = _strip_images(message)
                token_count = litellm.token_counter(  # type: ignore
                    model=self.model, messages=[stripped]
                )
                img_tokens = _count_anthropic_image_tokens(message)
                token_count += img_tokens
                anthropic_image_tokens += img_tokens
                message["token_count"] = token_count
                counted_count += 1
            else:
                token_count = litellm.token_counter(  # type: ignore
                    model=self.model, messages=[message]
                )
                message["token_count"] = token_count
                counted_count += 1
            role = message.get("role")
            if role == "system":
                system_tokens += token_count
            elif role == "user":
                user_tokens += token_count
            elif role == "tool":
                tools_tokens += token_count
            elif role == "assistant":
                assistant_tokens += token_count
            else:
                other_tokens += token_count

        # For Anthropic, strip images from bulk calls so litellm doesn't apply its
        # wrong 85-per-image estimate. We add back the correct image tokens
        # (already computed in the per-message loop) after.
        if is_anthropic:
            bulk_messages = [_strip_images(m) if _has_images(m) else m for m in messages]
        else:
            bulk_messages = messages

        messages_token_count_without_tools = litellm.token_counter(  # type: ignore
            model=self.model, messages=bulk_messages
        )

        total_tokens = litellm.token_counter(  # type: ignore
            model=self.model,
            messages=bulk_messages,
            tools=tools,  # type: ignore
        )

        tools_to_call_tokens = max(0, total_tokens - messages_token_count_without_tools)
        total_tokens += anthropic_image_tokens

        elapsed_ms = (time.monotonic() - t0) * 1000
        logging.debug(
            f"count_tokens: {elapsed_ms:.1f}ms | {len(messages)} msgs ({cached_count} cached, {counted_count} counted) | total={total_tokens}"
        )

        return ContextWindowUsage(
            total_tokens=total_tokens,
            system_tokens=system_tokens,
            user_tokens=user_tokens,
            tools_tokens=tools_tokens,
            tools_to_call_tokens=tools_to_call_tokens,
            other_tokens=other_tokens,
            assistant_tokens=assistant_tokens,
        )

    def get_litellm_corrected_name_for_robusta_ai(self) -> str:
        if self.is_robusta_model:
            # For robusta models, self.model is the underlying provider/model used by Robusta AI
            # To avoid litellm modifying the API URL according to the provider, the provider name
            # is replaced with 'openai/' just before doing a completion() call
            # Cf. https://docs.litellm.ai/docs/providers/openai_compatible
            split_model_name = self.model.split("/")
            return (
                split_model_name[0]
                if len(split_model_name) == 1
                else f"openai/{split_model_name[1]}"
            )
        else:
            return self.model

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
        tools_args = {}
        allowed_openai_params = None

        if tools and len(tools) > 0 and tool_choice == "auto":
            tools_args["tools"] = tools
            tools_args["tool_choice"] = tool_choice  # type: ignore

        if THINKING:
            self.args.setdefault("thinking", json.loads(THINKING))

        if EXTRA_HEADERS:
            self.args.setdefault("extra_headers", json.loads(EXTRA_HEADERS))

        litellm.modify_params = True

        if REASONING_EFFORT:
            self.args.setdefault("reasoning_effort", REASONING_EFFORT)
            allowed_openai_params = [
                "reasoning_effort"
            ]  # can be removed after next litelm version

        existing_allowed = self.args.pop("allowed_openai_params", None)
        if existing_allowed:
            if allowed_openai_params is None:
                allowed_openai_params = []
            allowed_openai_params.extend(existing_allowed)

        self.args.setdefault("temperature", temperature)

        # Get the litellm module to use (wrapped or unwrapped)
        litellm_to_use = self.tracer.wrap_llm(litellm) if self.tracer else litellm

        # Strip internal fields (e.g. token_count cache) so provider APIs only
        # receive valid message schema fields.  Shallow-copy only when needed to
        # avoid mutating the caller's dicts (which would invalidate the cache).
        _INTERNAL_FIELDS = {"token_count"}
        sanitized_messages: List[Dict[str, Any]] = [
            {k: v for k, v in m.items() if k not in _INTERNAL_FIELDS}
            if m.keys() & _INTERNAL_FIELDS
            else m
            for m in messages
        ]

        litellm_model_name = self.get_litellm_corrected_name_for_robusta_ai()

        # When Azure AD (Entra ID) token auth is enabled, obtain a cached token
        # and pass it to litellm instead of an API key.
        azure_ad_kwargs: Dict[str, Any] = {}
        if AZURE_AD_TOKEN_AUTH and litellm_model_name.startswith("azure/"):
            # For LiteLLM Azure provider, pass the bearer token via azure_ad_token
            # LiteLLM will send it as Authorization: Bearer <token>
            azure_ad_kwargs["azure_ad_token"] = get_azure_ad_token()
            # Also, ensure we do not leak stale API keys when using Entra ID
            # Leave api_key as None in completion call when AZURE_AD_TOKEN_AUTH is enabled
            self.api_key = None

        result = litellm_to_use.completion(
            model=litellm_model_name,
            api_key=self.api_key,
            base_url=self.api_base,
            api_version=self.api_version,
            messages=sanitized_messages,
            response_format=response_format,
            drop_params=drop_params,
            allowed_openai_params=allowed_openai_params,
            stream=stream,
            timeout=LLM_REQUEST_TIMEOUT,
            **azure_ad_kwargs,
            **tools_args,
            **self.args,
            cache_control_injection_points=[
                {
                    "location": "message",
                    "index": -1,  # -1 targets the last message.
                }
            ],
        )

        if isinstance(result, ModelResponse):
            return result
        elif isinstance(result, CustomStreamWrapper):
            return result
        else:
            raise Exception(f"Unexpected type returned by the LLM {type(result)}")

    def get_maximum_output_token(self) -> int:
        max_output_tokens = floor(min(64000, self.get_context_window_size() / 5))

        if OVERRIDE_MAX_OUTPUT_TOKEN:
            logging.debug(
                f"Using OVERRIDE_MAX_OUTPUT_TOKEN {OVERRIDE_MAX_OUTPUT_TOKEN}"
            )
            return OVERRIDE_MAX_OUTPUT_TOKEN

        # Try each name variant
        for name in self._get_model_name_variants_for_lookup():
            try:
                litellm_max_output_tokens = litellm.model_cost[name][
                    "max_output_tokens"
                ]
                if litellm_max_output_tokens < max_output_tokens:
                    max_output_tokens = litellm_max_output_tokens
                return max_output_tokens
            except Exception:
                continue

        # Log which lookups we tried
        logging.warning(
            f"Couldn't find model {self.model} in litellm's model list (tried: {', '.join(self._get_model_name_variants_for_lookup())}), "
            f"using {max_output_tokens} tokens for max_output_tokens. "
            f"To override, set OVERRIDE_MAX_OUTPUT_TOKEN environment variable to the correct value for your model."
        )
        return max_output_tokens


class LLMModelRegistry:
    def __init__(self, config: "Config", dal: SupabaseDal) -> None:
        self.config = config
        self._llms: dict[str, ModelEntry] = {}
        self._default_robusta_model = None
        self.dal = dal
        self._lock = threading.RLock()

        self._init_models()

    @property
    def default_robusta_model(self) -> Optional[str]:
        return self._default_robusta_model

    def _init_models(self):
        self._llms = self._parse_models_file(MODEL_LIST_FILE_LOCATION)

        if self._should_load_robusta_ai():
            self.configure_robusta_ai_model()

        if self._should_load_config_model():
            self._llms[self.config.model] = self._create_model_entry(
                model=self.config.model,
                model_name=self.config.model,
                base_url=self.config.api_base,
                is_robusta_model=False,
                api_key=self.config.api_key,
                api_version=self.config.api_version,
            )

    def _should_load_config_model(self) -> bool:
        if self.config.model is not None:
            if self._llms and self.config.model in self._llms:
                # model already loaded from file
                return False
            return True

        # backward compatibility - in the past config.model was set by default to gpt-4o.
        # so we need to check if the user has set an OPENAI_API_KEY to load the config model.
        has_openai_key = os.environ.get("OPENAI_API_KEY")
        if has_openai_key:
            self.config.model = "gpt-5.4"
            self.config._model_source = "default"
            return True

        return False

    def configure_robusta_ai_model(self) -> None:
        try:
            if not self.config.cluster_name or not LOAD_ALL_ROBUSTA_MODELS:
                self._load_default_robusta_config()
                return

            if not self.dal.account_id or not self.dal.enabled:
                self._load_default_robusta_config()
                return

            account_id, token = self.dal.get_ai_credentials()

            robusta_models: RobustaModelsResponse | None = fetch_robusta_models(
                account_id, token
            )
            if not robusta_models or not robusta_models.models:
                self._load_default_robusta_config()
                return

            default_model = None
            for model_name, model_data in robusta_models.models.items():
                logging.info(f"Loading Robusta AI model: {model_name}")
                self._llms[model_name] = self._create_robusta_model_entry(
                    model_name=model_name, model_data=model_data
                )
                if model_data.is_default:
                    default_model = model_name

            if default_model:
                logging.info(f"Setting default Robusta AI model to: {default_model}")
                self._default_robusta_model: str = default_model  # type: ignore

        except Exception:
            logging.exception("Failed to get all robusta models")
            # fallback to default behavior
            self._load_default_robusta_config()

    def _load_default_robusta_config(self):
        if self._should_load_robusta_ai():
            logging.info("Loading default Robusta AI model")
            self._llms[ROBUSTA_AI_MODEL_NAME] = ModelEntry(
                name=ROBUSTA_AI_MODEL_NAME,
                model="gpt-4o",  # TODO: tech debt, this isn't really
                base_url=ROBUSTA_API_ENDPOINT,
                is_robusta_model=True,
            )
            self._default_robusta_model = ROBUSTA_AI_MODEL_NAME

    def _should_load_robusta_ai(self) -> bool:
        if not self.config.should_try_robusta_ai:
            return False

        # ROBUSTA_AI were set in the env vars, so we can use it directly
        if ROBUSTA_AI is not None:
            return ROBUSTA_AI

        # MODEL is set in the env vars, e.g. the user is using a custom model
        # so we don't need to load the robusta AI model and keep the behavior backward compatible
        if "MODEL" in os.environ:
            return False

        # if the user has provided a model list, we don't need to load the robusta AI model
        if self._llms:
            return False

        return True

    def get_model_params(self, model_key: Optional[str] = None) -> ModelEntry:
        with self._lock:
            if not self._llms:
                raise Exception(
                    "No LLM models were loaded. Configure a model using one of: "
                    "--model '<provider/model>', export MODEL='<provider/model>', "
                    "or MODEL_LIST_FILE_LOCATION/config model list. "
                    "Setting only an API key (for example OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, AZURE_API_KEY) is not enough without a model."
                )

            if model_key:
                model_params = self._llms.get(model_key)
                if model_params:
                    display_logger.info(f"Using selected model: {model_key}")
                    return model_params.model_copy()

                if model_key.startswith("Robusta/"):
                    logging.warning("Resyncing Registry and Robusta models.")
                    self._init_models()
                    model_params = self._llms.get(model_key)
                    if model_params:
                        display_logger.info(f"Using selected model: {model_key}")
                        return model_params.model_copy()

                logging.error(f"Couldn't find model: {model_key} in model list")

            if self._default_robusta_model:
                model_params = self._llms.get(self._default_robusta_model)
                if model_params is not None:
                    logging.info(
                        f"Using default Robusta AI model: {self._default_robusta_model}"
                    )
                    return model_params.model_copy()

                logging.error(
                    f"Couldn't find default Robusta AI model: {self._default_robusta_model} in model list"
                )

            # Prefer the model set via config/env (e.g. MODEL=sonnet-4.5) over
            # the first entry in the model list file.
            if self.config.model and self.config.model in self._llms:
                logging.debug(f"Using config model: {self.config.model}")
                return self._llms[self.config.model].model_copy()

            model_key, first_model_params = next(iter(self._llms.items()))
            logging.debug(f"Using first available model: {model_key}")
            return first_model_params.model_copy()

    @property
    def models(self) -> dict[str, ModelEntry]:
        with self._lock:
            return self._llms

    def _parse_models_file(self, path: str) -> dict[str, ModelEntry]:
        models = load_yaml_file(path, raise_error=False, warn_not_found=False)
        for _, params in models.items():
            params = replace_env_vars_values(params)

        llms = {}
        for model_name, params in models.items():
            llms[model_name] = ModelEntry.model_validate(params)

        return llms

    def _create_robusta_model_entry(
        self, model_name: str, model_data: RobustaModel
    ) -> ModelEntry:
        entry = self._create_model_entry(
            model=model_data.model,
            model_name=model_name,
            base_url=f"{ROBUSTA_API_ENDPOINT}/llm/{model_name}",
            is_robusta_model=True,
        )
        entry.custom_args = model_data.holmes_args or {}  # type: ignore[assignment]
        return entry

    def _create_model_entry(
        self,
        model: str,
        model_name: str,
        base_url: Optional[str] = None,
        is_robusta_model: Optional[bool] = None,
        api_key: Optional[SecretStr] = None,
        api_base: Optional[str] = None,
        api_version: Optional[str] = None,
    ) -> ModelEntry:
        return ModelEntry(
            name=model_name,
            model=model,
            base_url=base_url,
            is_robusta_model=is_robusta_model,
            api_key=api_key,
            api_base=api_base,
            api_version=api_version,
        )


def build_usage_metadata(
    llm_response: Union[ModelResponse, CustomStreamWrapper, TextCompletionResponse],
) -> dict:
    if isinstance(llm_response, CustomStreamWrapper):
        complete_response = litellm.stream_chunk_builder(chunks=llm_response)  # type: ignore
        if complete_response:
            return build_usage_metadata(complete_response)
        return {}

    if not (
        isinstance(llm_response, (ModelResponse, TextCompletionResponse))
        and hasattr(llm_response, "usage")
        and llm_response.usage
    ):
        return {}

    raw = extract_usage_from_response(llm_response)  # type: ignore[arg-type]
    usage: dict = {
        "prompt_tokens": raw["prompt_tokens"],
        "completion_tokens": raw["completion_tokens"],
        "total_tokens": raw["total_tokens"],
    }
    if raw["cached_tokens"] is not None:
        usage["cached_tokens"] = raw["cached_tokens"]
    if raw["reasoning_tokens"]:
        usage["reasoning_tokens"] = raw["reasoning_tokens"]
    return usage

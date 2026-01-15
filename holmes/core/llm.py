import json
import logging
import os
import threading
from abc import abstractmethod
from math import floor
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type, Union

import litellm
import sentry_sdk
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import ModelResponse, TextCompletionResponse
from pydantic import BaseModel, ConfigDict, SecretStr
from typing_extensions import Self

from holmes.clients.robusta_client import (
    RobustaModel,
    RobustaModelsResponse,
    fetch_robusta_models,
)
from holmes.common.env_vars import (
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


class TokenCountMetadata(BaseModel):
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

    # LLM configurations used services like Azure OpenAI Service
    api_base: Optional[str] = None
    api_version: Optional[str] = None

    model_config = ConfigDict(
        extra="allow",
    )

    @classmethod
    def load_from_dict(cls, data: dict) -> Self:
        return cls.model_validate(data)


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
    ) -> TokenCountMetadata:
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
            if os.environ.get("AWS_PROFILE") or os.environ.get(
                "AWS_BEARER_TOKEN_BEDROCK"
            ):
                model_requirements = {"keys_in_environment": True, "missing_keys": []}
            elif args.get("aws_access_key_id") and args.get("aws_secret_access_key"):
                return  # break fast.
            else:
                model_requirements = litellm.validate_environment(
                    model=model, api_key=api_key, api_base=api_base
                )
        else:
            model_requirements = litellm.validate_environment(
                model=model, api_key=api_key, api_base=api_base
            )
            # validate_environment does not accept api_version, and as a special case for Azure OpenAI Service,
            # when all the other AZURE environments are set expect AZURE_API_VERSION, validate_environment complains
            # the missing of it even after the api_version is set.
            # TODO: There's an open PR in litellm to accept api_version in validate_environment, we can leverage this
            # change if accepted to ignore the following check.
            # https://github.com/BerriAI/litellm/pull/13808
            if (
                provider == "azure"
                and ["AZURE_API_VERSION"] == model_requirements["missing_keys"]
                and api_version is not None
            ):
                model_requirements["missing_keys"] = []
                model_requirements["keys_in_environment"] = True

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

    @sentry_sdk.trace
    def count_tokens(
        self, messages: list[dict], tools: Optional[list[dict[str, Any]]] = None
    ) -> TokenCountMetadata:
        # TODO: Add a recount:bool flag to save time. When the flag is false, reuse 'message["token_count"]' for individual messages.
        # It's only necessary to recount message tokens at the beginning of a session because the LLM model may have changed.
        # Changing the model requires recounting tokens because the tokenizer may be different
        total_tokens = 0
        tools_tokens = 0
        system_tokens = 0
        assistant_tokens = 0
        user_tokens = 0
        other_tokens = 0
        tools_to_call_tokens = 0
        for message in messages:
            # count message tokens individually because it gives us fine grain information about each tool call/message etc.
            # However be aware that the sum of individual message tokens is not equal to the overall messages token
            token_count = litellm.token_counter(  # type: ignore
                model=self.model, messages=[message]
            )
            message["token_count"] = token_count
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
                # although this should not be needed,
                # it is defensive code so that all tokens are accounted for
                # and can potentially make debugging easier
                other_tokens += token_count

        messages_token_count_without_tools = litellm.token_counter(  # type: ignore
            model=self.model, messages=messages
        )

        total_tokens = litellm.token_counter(  # type: ignore
            model=self.model,
            messages=messages,
            tools=tools,  # type: ignore
        )
        tools_to_call_tokens = max(0, total_tokens - messages_token_count_without_tools)

        return TokenCountMetadata(
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

        if self.args.get("thinking", None):
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

        self._add_cache_control_to_last_message(messages)

        # Get the litellm module to use (wrapped or unwrapped)
        litellm_to_use = self.tracer.wrap_llm(litellm) if self.tracer else litellm

        litellm_model_name = self.get_litellm_corrected_name_for_robusta_ai()
        result = litellm_to_use.completion(
            model=litellm_model_name,
            api_key=self.api_key,
            base_url=self.api_base,
            api_version=self.api_version,
            messages=messages,
            response_format=response_format,
            drop_params=drop_params,
            allowed_openai_params=allowed_openai_params,
            stream=stream,
            timeout=LLM_REQUEST_TIMEOUT,
            **tools_args,
            **self.args,
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

    def _add_cache_control_to_last_message(
        self, messages: List[Dict[str, Any]]
    ) -> None:
        """
        Add cache_control to the last non-user message for Anthropic prompt caching.
        Removes any existing cache_control from previous messages to avoid accumulation.
        """
        # Skip cache_control for VertexAI/Gemini models as they don't support it with tools
        if self.model and (
            "vertex" in self.model.lower() or "gemini" in self.model.lower()
        ):
            return

        # First, remove any existing cache_control from all messages
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and "cache_control" in block:
                        del block["cache_control"]
                        logging.debug(
                            f"Removed existing cache_control from {msg.get('role')} message"
                        )

        # Find the last non-user message to add cache_control to.
        # Adding cache_control to user message requires changing its structure, so we avoid it
        # This avoids breaking parse_messages_tags which only processes user messages
        target_msg = None
        for msg in reversed(messages):
            if msg.get("role") != "user":
                target_msg = msg
                break

        if not target_msg:
            logging.debug("No non-user message found for cache_control")
            return

        content = target_msg.get("content")

        if content is None:
            return

        if isinstance(content, str) and content:
            # Convert string to structured format with cache_control
            target_msg["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            logging.debug(
                f"Added cache_control to {target_msg.get('role')} message (converted from string)"
            )
        elif isinstance(content, list) and content:
            # Add cache_control to the last content block
            last_block = content[-1]
            if isinstance(last_block, dict) and "type" in last_block:
                last_block["cache_control"] = {"type": "ephemeral"}
                logging.debug(
                    f"Added cache_control to {target_msg.get('role')} message (structured content)"
                )


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
            self.config.model = "gpt-4.1"
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
                raise Exception("No llm models were loaded")

            if model_key:
                model_params = self._llms.get(model_key)
                if model_params:
                    logging.info(f"Using selected model: {model_key}")
                    return model_params.model_copy()

                if model_key.startswith("Robusta/"):
                    logging.warning("Resyncing Registry and Robusta models.")
                    self._init_models()
                    model_params = self._llms.get(model_key)
                    if model_params:
                        logging.info(f"Using selected model: {model_key}")
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


def get_llm_usage(
    llm_response: Union[ModelResponse, CustomStreamWrapper, TextCompletionResponse],
) -> dict:
    usage: dict = {}
    if (
        (
            isinstance(llm_response, ModelResponse)
            or isinstance(llm_response, TextCompletionResponse)
        )
        and hasattr(llm_response, "usage")
        and llm_response.usage
    ):  # type: ignore
        usage["prompt_tokens"] = llm_response.usage.prompt_tokens  # type: ignore
        usage["completion_tokens"] = llm_response.usage.completion_tokens  # type: ignore
        usage["total_tokens"] = llm_response.usage.total_tokens  # type: ignore
    elif isinstance(llm_response, CustomStreamWrapper):
        complete_response = litellm.stream_chunk_builder(chunks=llm_response)  # type: ignore
        if complete_response:
            return get_llm_usage(complete_response)
    return usage

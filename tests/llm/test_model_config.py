import functools
import importlib.metadata
import json
import os
import re
import warnings
from typing import Any, Callable, FrozenSet, List, Optional

import boto3
import litellm
import pytest
from litellm.integrations.custom_logger import CustomLogger
from pydantic import BaseModel, ValidationError

from holmes.core.llm import DefaultLLM
from tests.llm.utils.test_case_utils import (
    _get_models_from_model_list,
    create_eval_llm,
    get_models,
)

KNOWN_PARAMS = {
    "audio",
    "cache_control",
    "context_management",
    "extra_headers",
    "frequency_penalty",
    "function_call",
    "functions",
    "include_server_side_tool_invocations",
    "logit_bias",
    "logprobs",
    "max_completion_tokens",
    "max_retries",
    "max_tokens",
    "modalities",
    "n",
    "parallel_tool_calls",
    "prediction",
    "presence_penalty",
    "prompt_cache_key",
    "prompt_cache_retention",
    "reasoning_effort",
    "requestMetadata",
    "response_format",
    "safety_identifier",
    "seed",
    "service_tier",
    "speed",
    "stop",
    "store",
    "stream",
    "stream_options",
    "temperature",
    "thinking",
    "tool_choice",
    "tools",
    "top_logprobs",
    "top_p",
    "user",
    "verbosity",
    "web_search_options",
}

KNOWN_CAPABILITY_FLAGS = {
    "bedrock_output_config_effort_ceiling",
    "supports_adaptive_thinking",
    "supports_anthropic_compaction",
    "supports_assistant_prefill",
    "supports_audio_input",
    "supports_audio_output",
    "supports_computer_use",
    "supports_forced_tool_use",
    "supports_function_calling",
    "supports_legacy_thinking",
    "supports_max_reasoning_effort",
    "supports_mid_conversation_system",
    "supports_minimal_reasoning_effort",
    "supports_native_streaming",
    "supports_native_structured_output",
    "supports_none_reasoning_effort",
    "supports_output_config",
    "supports_parallel_function_calling",
    "supports_parallel_tool_use_config",
    "supports_pdf_input",
    "supports_prompt_caching",
    "supports_reasoning",
    "supports_response_schema",
    "supports_sampling_params",
    "supports_system_messages",
    "supports_tool_choice",
    "supports_tool_search",
    "supports_url_context",
    "supports_video_input",
    "supports_vision",
    "supports_web_search",
    "supports_xhigh_reasoning_effort",
}

PROVIDER_ARG_PREFIXES = ("aws_", "vertex_", "azure_")
LITELLM_CONTROL_ARGS = {
    "allowed_openai_params",
    "drop_params",
    "input_cost_per_token",
    "output_cost_per_token",
}
TOKEN_LIMIT_KEYS = (
    "max_tokens",
    "maxTokens",
    "max_completion_tokens",
    "max_output_tokens",
    "maxOutputTokens",
)
RESPONSES_ROUTE = "/responses/"
EFFORT_KEYS = ("effort", "reasoning_effort", "thinkingLevel")
ON_OFF_REASONING_MODELS = {"openrouter/moonshotai/kimi-k2.5"}
EMULATED_STRUCTURED_OUTPUT_TOOL = "json_tool_call"
# litellm 1.89.0 hardcodes native structured output models up to 4.7
KNOWN_EMULATED_NATIVE_MODELS = {("1.89.0", "anthropic/claude-fable-5")}
NATIVE_STRUCTURED_OUTPUT_KEYS = (
    "response_format",
    "format",
    "textFormat",
    "response_json_schema",
    "response_schema",
)
MIN_SAFE_DEFAULT_MAX_TOKENS = 16000
PING = [{"role": "user", "content": "Reply with the single word ok"}]
TOOL_CALL_MESSAGES = [
    {"role": "system", "content": "Always call get_pods before answering."},
    {"role": "user", "content": "Which pods are running?"},
]
GET_PODS_TOOL = {
    "type": "function",
    "function": {
        "name": "get_pods",
        "description": "List pods in the cluster",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
VERIFY_MODELS = [
    m.strip() for m in os.environ.get("VERIFY_MODELS", "").split(",") if m.strip()
]
MODELS = VERIFY_MODELS or _get_models_from_model_list() or get_models()
litellm.suppress_debug_info = True
REGION_ENDPOINTS = {"us": "us-east-1", "eu": "eu-south-2", "ap": "ap-northeast-1"}
REGION_PROFILE_PREFIXES = {
    "us": ("us.", "global."),
    "eu": ("eu.",),
    "ap": ("apac.", "jp.", "au.", "global."),
}
GEO_PREFIX_PATTERN = re.compile(r"^(us|eu|apac|jp|au|global)\.")


class Word(BaseModel):
    word: str


class RequestCapture(CustomLogger):
    def __init__(self) -> None:
        super().__init__()
        self.bodies: List[dict] = []

    def log_pre_api_call(self, model, messages, kwargs):
        body = kwargs.get("additional_args", {}).get("complete_input_dict") or {}
        self.bodies.append(json.loads(body) if isinstance(body, str) else body)


@pytest.fixture
def request_capture():
    capture = RequestCapture()
    litellm.callbacks.append(capture)
    yield capture
    litellm.callbacks.remove(capture)


def find_values(node: Any, keys: tuple) -> List[Any]:
    if isinstance(node, list):
        return [value for item in node for value in find_values(item, keys)]
    if not isinstance(node, dict):
        return []
    found = [value for key, value in node.items() if key in keys]
    return found + find_values(list(node.values()), keys)


def create_llm_or_fail(model: str) -> DefaultLLM:
    try:
        return create_eval_llm(model)
    except Exception as e:
        error = str(e)[:400]
    pytest.fail(f"{model}: could not create LLM - {error}", pytrace=False)


def get_model_info_or_fail(model_name: str) -> dict:
    try:
        return dict(litellm.get_model_info(model_name.replace(RESPONSES_ROUTE, "/")))
    except Exception as e:
        pytest.fail(f"{model_name}: not in litellm's model map ({e})")


def get_raw_cost_entry(info: dict) -> dict:
    return litellm.model_cost.get(info["key"], {})


def is_provider_arg(arg: str) -> bool:
    return arg.startswith(PROVIDER_ARG_PREFIXES) or arg in LITELLM_CONTROL_ARGS


def call_provider_or_fail(
    model_key: str, completion: Callable[..., Any], **kwargs
) -> Any:
    try:
        return completion(**kwargs)
    except Exception as e:
        error = str(e)[:400]
    pytest.fail(f"{model_key}: provider call failed - {error}", pytrace=False)


def assert_no_failures(failures: List[str]) -> None:
    if failures:
        pytest.fail("\n".join(failures), pytrace=False)


@functools.lru_cache
def list_bedrock_profiles(
    region: str, access_key_id: Optional[str], secret_access_key: Optional[str]
) -> FrozenSet[str]:
    client = boto3.client(
        "bedrock",
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )
    pages = client.get_paginator("list_inference_profiles").paginate()
    return frozenset(
        profile["inferenceProfileId"]
        for page in pages
        for profile in page["inferenceProfileSummaries"]
    )


@pytest.mark.llm
@pytest.mark.parametrize("model", MODELS)
def test_model_known_to_litellm(model):
    llm = create_llm_or_fail(model)
    info = get_model_info_or_fail(llm.model)
    print(
        f"\n{model}: {llm.model} -> litellm key '{info['key']}' "
        f"(input ${info['input_cost_per_token']}/tok, output ${info['output_cost_per_token']}/tok)"
    )


@pytest.mark.llm
@pytest.mark.parametrize("model", MODELS)
def test_regions_supported(model):
    llm = create_llm_or_fail(model)
    if not llm.model.startswith("bedrock/"):
        pytest.skip(f"{model}: region check only covers bedrock")
    base_model = GEO_PREFIX_PATTERN.sub("", llm.model.split("/")[-1])
    failures = []
    for region, endpoint in REGION_ENDPOINTS.items():
        profiles = list_bedrock_profiles(
            endpoint,
            llm.args.get("aws_access_key_id"),
            llm.args.get("aws_secret_access_key"),
        )
        candidates = [
            f"{prefix}{base_model}" for prefix in REGION_PROFILE_PREFIXES[region]
        ]
        available = [profile for profile in candidates if profile in profiles]
        print(f"\n{model}: {region} ({endpoint}) profiles {available}")
        if not available:
            failures.append(
                f"{model}: no {region} inference profile in {endpoint} (need one of {candidates})"
            )
    assert_no_failures(failures)


@pytest.mark.llm
@pytest.mark.parametrize("model", MODELS)
def test_no_unknown_capabilities(model):
    llm = create_llm_or_fail(model)
    info = get_model_info_or_fail(llm.model)
    raw_entry = get_raw_cost_entry(info)
    new_params = set(info.get("supported_openai_params") or []) - KNOWN_PARAMS
    new_flags = {
        key
        for key in raw_entry
        if (key.startswith("supports_") or key.endswith("_ceiling"))
        and key not in KNOWN_CAPABILITY_FLAGS
    }
    failures = [
        f"{model}: new param '{key}' not in KNOWN_PARAMS - investigate, "
        "then configure it in the model list or add it to the known list"
        for key in sorted(new_params)
    ] + [
        f"{model}: new capability flag '{key}' not in KNOWN_CAPABILITY_FLAGS - investigate, "
        "then configure it in the model list or add it to the known list"
        for key in sorted(new_flags)
    ]
    assert_no_failures(failures)


@pytest.mark.llm
@pytest.mark.parametrize("model", MODELS)
def test_args_supported(model):
    llm = create_llm_or_fail(model)
    info = get_model_info_or_fail(llm.model)
    supported = set(info.get("supported_openai_params") or [])
    allowed = set(llm.args.get("allowed_openai_params") or [])
    failures = [
        f"{model}: arg '{arg}' is not supported by litellm for {llm.model}"
        for arg in llm.args
        if not is_provider_arg(arg) and arg not in supported | allowed
    ]
    if get_raw_cost_entry(info).get("supports_sampling_params") is False:
        if llm.args.get("temperature", 1) != 1:
            failures.append(f"{model}: only temperature 1 is allowed")
        if "top_p" in llm.args:
            failures.append(f"{model}: top_p is not allowed")
    assert_no_failures(failures)


@pytest.mark.llm
@pytest.mark.parametrize("model", MODELS)
def test_reasoning_pinned(model):
    llm = create_llm_or_fail(model)
    info = get_model_info_or_fail(llm.model)
    if not info.get("supports_reasoning"):
        pytest.skip(f"{model}: not a reasoning model")
    if not {"reasoning_effort", "thinking"} & set(
        info.get("supported_openai_params") or []
    ):
        pytest.skip(f"{model}: reasoning is not configurable through litellm")
    # OpenRouter Kimi K2.5 only toggles reasoning, no effort
    if llm.model in ON_OFF_REASONING_MODELS:
        pytest.skip(f"{model}: reasoning is on/off only, no effort levels")

    effort = llm.args.get("reasoning_effort")
    thinking = llm.args.get("thinking") or {}
    has_budget = thinking.get("type") == "enabled" and thinking.get("budget_tokens")
    failures = []
    if not (effort or has_budget):
        failures.append(
            f"{model}: reasoning is provider-defined - set reasoning_effort "
            f"or thinking.type=enabled with budget_tokens (thinking={thinking})"
        )
    if effort and info.get(f"supports_{effort}_reasoning_effort") is False:
        failures.append(f"{model}: reasoning_effort '{effort}' is not supported")
    assert_no_failures(failures)


@pytest.mark.llm
@pytest.mark.parametrize("model", MODELS)
def test_request_sends_configured_reasoning(model, request_capture):
    llm = create_llm_or_fail(model)
    effort = llm.args.get("reasoning_effort")
    budget = (llm.args.get("thinking") or {}).get("budget_tokens")
    call_provider_or_fail(model, llm.completion, messages=PING)
    body = request_capture.bodies[-1]
    sent_efforts = find_values(body, EFFORT_KEYS)
    sent_budgets = find_values(body, ("budget_tokens",))
    sent_tokens = find_values(body, TOKEN_LIMIT_KEYS)
    print(
        f"\n{model}: sent effort={sent_efforts} budget={sent_budgets} max_tokens={sent_tokens}"
    )

    failures = []
    if effort and effort not in sent_efforts:
        failures.append(f"{model}: effort '{effort}' missing from request")
    if budget and budget not in sent_budgets:
        failures.append(f"{model}: budget_tokens {budget} missing from request")
    expected_tokens = llm.args.get("max_completion_tokens") or llm.args["max_tokens"]
    if expected_tokens not in sent_tokens:
        failures.append(f"{model}: max tokens {expected_tokens} missing from request")
    assert_no_failures(failures)


@pytest.mark.llm
@pytest.mark.parametrize("model", MODELS)
def test_max_output_tokens(model, request_capture):
    llm = create_llm_or_fail(model)
    info = get_model_info_or_fail(llm.model)
    explicit = llm.args.get("max_completion_tokens") or llm.args.get("max_tokens")
    litellm_max = info.get("max_output_tokens")
    args_without_limit = {
        key: value for key, value in llm.args.items() if key not in TOKEN_LIMIT_KEYS
    }
    call_provider_or_fail(
        model,
        litellm.completion,
        model=llm.model,
        messages=PING,
        api_key=llm.api_key,
        base_url=llm.api_base,
        api_version=llm.api_version,
        **args_without_limit,
    )
    default_sent = find_values(request_capture.bodies[-1], TOKEN_LIMIT_KEYS)
    print(
        f"\n{model}: holmes sends {llm.get_maximum_output_token()}, explicit={explicit}, "
        f"litellm max={litellm_max}, default when unset={default_sent or 'provider-side'}"
    )

    if default_sent and default_sent[0] < MIN_SAFE_DEFAULT_MAX_TOKENS:
        warnings.warn(
            f"{model}: default max output tokens is {default_sent[0]} when unset - truncation risk"
        )
    if explicit and litellm_max and explicit > litellm_max:
        pytest.fail(
            f"{model}: max_tokens {explicit} exceeds litellm max_output_tokens {litellm_max}"
        )


@pytest.mark.llm
@pytest.mark.parametrize("model", MODELS)
def test_structured_output(model, request_capture):
    llm = create_llm_or_fail(model)
    info = get_model_info_or_fail(llm.model)
    response = call_provider_or_fail(
        model,
        llm.completion,
        messages=[{"role": "user", "content": "Return the word ok"}],
        response_format=Word,
    )
    body = request_capture.bodies[-1]
    emulated = EMULATED_STRUCTURED_OUTPUT_TOOL in find_values(body, ("name",))
    native = not emulated and bool(find_values(body, NATIVE_STRUCTURED_OUTPUT_KEYS))
    mode = "emulated" if emulated else "native" if native else "unsupported"
    print(
        f"\n{model}: structured output {mode} "
        f"(supports_native_structured_output={info.get('supports_native_structured_output')})"
    )

    failures = []
    if mode == "unsupported":
        failures.append(f"{model}: response_format was dropped from request")
    known_emulated = (
        importlib.metadata.version("litellm"),
        llm.model,
    ) in KNOWN_EMULATED_NATIVE_MODELS
    if (
        info.get("supports_native_structured_output")
        and not native
        and not known_emulated
    ):
        failures.append(
            f"{model}: litellm claims native structured output but emulated it"
        )
    try:
        Word.model_validate_json(response.choices[0].message.content)  # type: ignore[union-attr]
    except ValidationError as e:
        warnings.warn(f"{model}: structured response did not parse ({e})")
    assert_no_failures(failures)


@pytest.mark.llm
@pytest.mark.parametrize("model", MODELS)
def test_tool_calling(model):
    llm = create_llm_or_fail(model)
    response = call_provider_or_fail(
        model,
        llm.completion,
        messages=TOOL_CALL_MESSAGES,
        tools=[GET_PODS_TOOL],
        tool_choice="auto",
    )
    tool_calls = response.choices[0].message.tool_calls or []  # type: ignore[union-attr]
    print(f"\n{model}: tool calls {[call.function.name for call in tool_calls]}")
    if not tool_calls:
        warnings.warn(f"{model}: accepted tools but did not call get_pods")

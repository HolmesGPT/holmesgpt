# Using LangChain LLM with HolmesGPT

HolmesGPT now supports using LangChain's `ChatOpenAI` instead of LiteLLM. This is useful when working with custom proxy endpoints that expose an OpenAI-compatible API but use non-standard model naming conventions.

## Why Use LangChain LLM Mode?

LangChain's `ChatOpenAI` with a custom `base_url` works with any OpenAI-compatible proxy, regardless of the underlying model provider (Anthropic, OpenAI, Google, etc.). This bypasses LiteLLM's provider detection, which can fail with non-standard model names.

## Installation

First, install the required LangChain packages:

```bash
poetry add langchain-openai langchain-core
```

Note: You don't need `langchain-anthropic` since we use `ChatOpenAI` for all models via the OpenAI-compatible proxy API.

## Configuration Methods

You can enable LangChain LLM mode in three ways (in order of priority):

### 1. CLI Flag (Highest Priority)

```bash
holmes ask "your question" \
  --use-langchain-llm \
  --model anthropic--claude-4.6-sonnet \
  --api-base http://localhost:6655/litellm/v1
```

Or disable it explicitly:

```bash
holmes ask "your question" --no-use-langchain-llm
```

### 2. Config File

Edit `~/.holmes/config.yaml`:

```yaml
use_langchain_llm: true
model: anthropic--claude-4.6-sonnet
api_base: http://localhost:6655/litellm/v1
api_key: your-proxy-key
```

### 3. Environment Variable (Lowest Priority)

```bash
export USE_LANGCHAIN_LLM=true
export MODEL=anthropic--claude-4.6-sonnet
export OPENAI_API_BASE=http://localhost:6655/litellm/v1
export OPENAI_API_KEY=your-proxy-key

holmes ask "your question"
```

## How It Works

When `use_langchain_llm` is enabled, HolmesGPT uses LangChain's `ChatOpenAI` with your custom `base_url`. This works with **any OpenAI-compatible proxy** regardless of the underlying model provider:

- Anthropic models (Claude, Opus, Sonnet, Haiku)
- OpenAI models (GPT-5, GPT-4.1, etc.)
- Google models (Gemini)
- Any other provider exposed through an OpenAI-compatible API

The proxy handles the translation between OpenAI's API format and the actual provider's API.

## Example: Using with LiteLLM Proxy

If you have a LiteLLM proxy running with custom model names:

```yaml
# ~/.holmes/config.yaml
use_langchain_llm: true
model: anthropic--claude-4.6-sonnet  # Your proxy's model name
api_base: http://localhost:6655/litellm/v1
api_key: your-proxy-key  # Or dummy-key if your proxy doesn't require auth
```

Then run:

```bash
holmes ask "List the Kubernetes namespaces"
```

## Supported Models

Any model name that your OpenAI-compatible proxy supports will work, including:

- Anthropic: `anthropic--claude-4.6-sonnet`, `anthropic--claude-4.6-opus`, etc.
- OpenAI: `gpt-5`, `gpt-4.1`, `gpt-5-mini`, etc.
- Google: `gemini-2.5-pro`, `gemini-2.5-flash`, etc.
- Any other provider your proxy exposes

The `ChatOpenAI` client will send requests to your proxy's OpenAI-compatible endpoint, and the proxy will route them to the appropriate provider.

## Troubleshooting

### Import Errors

If you see import errors for `langchain_openai`:

```bash
poetry add langchain-openai langchain-core
```

You only need these two packages - `langchain-anthropic` is not required.

### Provider Detection Errors

If you get LiteLLM provider detection errors, enable LangChain mode to bypass LiteLLM's validation:

```bash
holmes ask "your question" --use-langchain-llm
```

### Model Not Found

Make sure your model name matches exactly what your proxy expects. Check your proxy's model list:

```bash
curl http://localhost:6655/models
```

## When to Use LangChain LLM Mode

Use LangChain LLM mode when:

1. Your proxy uses non-standard model naming (e.g., `anthropic--claude-4.6-sonnet` instead of `claude-sonnet-4`)
2. You get LiteLLM provider detection errors
3. You want to use LangChain's native chat interfaces directly
4. Your proxy endpoint is OpenAI-compatible but not recognized by LiteLLM

Use the default LiteLLM mode when:

1. Using standard provider APIs (OpenAI, Anthropic, Azure, etc.)
2. Your model names follow standard conventions
3. You want LiteLLM's built-in provider support and features

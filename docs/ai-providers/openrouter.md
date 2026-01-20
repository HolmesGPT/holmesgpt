# OpenRouter

Configure HolmesGPT to use [OpenRouter](https://openrouter.ai/) for access to multiple AI models through a single API.

## Methods

### Method 1: Native LiteLLM OpenRouter Provider (Recommended)

The simplest approach uses LiteLLM's native OpenRouter support. Only `OPENROUTER_API_KEY` is required.

```bash
export OPENROUTER_API_KEY="sk-or-..."  # your OpenRouter key
holmes ask "hello" --model="openrouter/anthropic/claude-3.5-sonnet" --no-interactive
```

**Optional environment variables:**

- `OPENROUTER_API_BASE` - Custom API base URL (defaults to `https://openrouter.ai/api/v1`)
- `OR_SITE_URL` - Your site URL for OpenRouter rankings
- `OR_APP_NAME` - Your app name for OpenRouter rankings

### Method 2: OpenAI-Compatible Endpoint

Alternatively, you can use OpenRouter's OpenAI-compatible endpoint by setting the base URL and using `OPENAI_API_KEY`.

```bash
export OPENAI_API_BASE="https://openrouter.ai/api/v1"
export OPENAI_API_KEY="sk-or-..."  # your OpenRouter key
holmes ask "hello" --model="openrouter/anthropic/claude-3.5-sonnet" --no-interactive
```

## Available Models

You can use any model available on OpenRouter by using the `openrouter/` prefix followed by the model ID. For example:

- `openrouter/anthropic/claude-3.5-sonnet`
- `openrouter/openai/gpt-4o`
- `openrouter/google/gemini-pro`
- `openrouter/meta-llama/llama-3-70b-instruct`

See the [OpenRouter models page](https://openrouter.ai/models) for a complete list of available models.

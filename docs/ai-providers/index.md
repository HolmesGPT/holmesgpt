# AI Providers

HolmesGPT supports multiple AI providers, giving you flexibility in choosing the best model for your needs and budget.

<div class="grid cards" markdown>

-   [:simple-anthropic:{ .lg .middle } **Anthropic**](anthropic.md)
-   [:material-aws:{ .lg .middle } **AWS Bedrock**](aws-bedrock.md)
-   [:material-microsoft-azure:{ .lg .middle } **Azure OpenAI**](azure-openai.md)
-   [:simple-googlegemini:{ .lg .middle } **Gemini**](gemini.md)
-   [:simple-github:{ .lg .middle } **GitHub Models**](github.md)
-   [:material-google-cloud:{ .lg .middle } **Google Vertex AI**](google-vertex-ai.md)
-   [:simple-ollama:{ .lg .middle } **Ollama**](ollama.md)
-   [:simple-openai:{ .lg .middle } **OpenAI**](openai.md)
-   [:material-api:{ .lg .middle } **OpenAI-Compatible**](openai-compatible.md)
-   [:material-earth:{ .lg .middle } **OpenRouter**](openrouter.md)
-   [:material-robot:{ .lg .middle } **Robusta AI**](robusta-ai.md)
-   [:material-layers-triple:{ .lg .middle } **Using Multiple Providers**](using-multiple-providers.md)

</div>

## Quick Start

!!! tip "Recommended for New Users"
    **Anthropic Claude models** give the best results by far. We recommend Sonnet 4.5 (`anthropic/claude-sonnet-4-5-20250929`).

    **OpenAI models** are a good alternative with fast responses.

    To get started with Anthropic:

    1. Get an [Anthropic API key](https://support.anthropic.com/en/articles/8114521-how-can-i-access-the-anthropic-api){:target="_blank"}
    2. Set `export ANTHROPIC_API_KEY="your-api-key"`
    3. Run `holmes ask "what pods are failing?" --model="anthropic/claude-sonnet-4-5-20250929"`

Choose your provider above to see detailed configuration instructions.

## Configuration

Each AI provider requires specific environment variables for authentication. See the [Environment Variables Reference](../reference/environment-variables.md) for a complete list of all configuration options beyond just API keys.

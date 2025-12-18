import os

# Common help texts
system_prompt_help = "Advanced. System prompt for LLM. Values starting with builtin:// are loaded from holmes/plugins/prompts, values starting with file:// are loaded from the given path, other values are interpreted as a prompt string"

# Agent name used in welcome banner, logging, and prompts
agent_name: str = os.environ.get("AGENT_NAME", "HolmesGPT")

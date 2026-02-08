# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

HolmesGPT is an AI-powered troubleshooting agent that connects to observability platforms (Kubernetes, Prometheus, Grafana, etc.) to automatically diagnose and analyze infrastructure and application issues. It uses an agentic loop to investigate problems by calling tools to gather data from multiple sources.

## Development Commands

### Environment Setup
```bash
# Install dependencies with Poetry
poetry install

# Install pre-commit hooks
poetry run pre-commit install
```

### Testing

```bash
# Install test dependencies with Poetry
poetry install --with dev
```

```bash
# Run all non-LLM tests (unit and integration tests)
make test-without-llm
poetry run pytest tests -m "not llm"

# Run LLM evaluation tests (requires API keys)
make test-llm-ask-holmes          # Test single-question interactions
make test-llm-investigate         # Test AlertManager investigations
poetry run pytest tests/llm/ -n 6 -vv  # Run all LLM tests in parallel

# Run pre-commit checks (includes ruff, mypy, poetry validation)
make check
poetry run pre-commit run -a
```

### Code Quality
```bash
# Format code with ruff
poetry run ruff format

# Check code with ruff (auto-fix issues)
poetry run ruff check --fix

# Type checking with mypy
poetry run mypy
```

## Architecture Overview

### Core Components

**CLI Entry Point** (`holmes/main.py`):
- Typer-based CLI with subcommands for `ask`, `investigate`, `toolset`
- Handles configuration loading, logging setup, and command routing

** Interactive mode for CLI** (`holmes/interactive.py`):
- Handles interactive mode for `ask` subcommand
- Implements slash commands

**Configuration System** (`holmes/config.py`):
- Loads settings from `~/.holmes/config.yaml` or via CLI options
- Manages API keys, model selection, and toolset configurations
- Factory methods for creating sources (AlertManager, Jira, PagerDuty, etc.)

**Core Investigation Engine** (`holmes/core/`):
- `tool_calling_llm.py`: Main LLM interaction with tool calling capabilities
- `investigation.py`: Orchestrates multi-step investigations with runbooks
- `toolset_manager.py`: Manages available tools and their configurations
- `tools.py`: Tool definitions and execution logic

**Plugin System** (`holmes/plugins/`):
- **Sources**: AlertManager, Jira, PagerDuty, OpsGenie integrations
- **Toolsets**: Kubernetes, Prometheus, Grafana, AWS, Docker, etc.
- **Prompts**: Jinja2 templates for different investigation scenarios
- **Destinations**: Slack integration for sending results

### Key Patterns

**Toolset Architecture**:
- Each toolset is a YAML file defining available tools and their parameters
- Tools can be Python functions or bash commands with safety validation
- Toolsets are loaded dynamically and can be customized via config files
- **Important**: All toolsets MUST return detailed error messages from underlying APIs to enable LLM self-correction
  - Include the exact query/command that was executed
  - Include time ranges, parameters, and filters used
  - Include the full API error response (status code and message)
  - For "no data" responses, specify what was searched and where

**Thin API Wrapper Pattern for Python Toolsets**:
- Reference implementation: `servicenow_tables/servicenow_tables.py`
- Use `requests` library for HTTP calls (not specialized client libraries like `opensearchpy`)
- Simple config class with Pydantic validation
- Health check in `prerequisites_callable()` method
- Each tool is a thin wrapper around a single API endpoint

**Server-Side Filtering is Critical**:
- **Never return unbounded data from APIs** - this causes token overflow
- Always include filter parameters on tools that query collections (e.g., `index` parameter for Elasticsearch _cat APIs)
- Example problem: `opensearch_list_shards` returned ALL shards → 25K+ tokens on large clusters
- Example fix: `elasticsearch_cat` tool requires `index` parameter for shards/segments endpoints
- When server-side filtering is not possible, use `JsonFilterMixin` (see `json_filter_mixin.py`) to add `max_depth` and `jq` parameters for client-side filtering

**Toolset Config Backwards Compatibility**:
When renaming config fields in a toolset, maintain backwards compatibility using Pydantic's `extra="allow"`:

```python
# ✅ DO: Use extra="allow" to accept deprecated fields without polluting schema
class MyToolsetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Only define current field names in schema
    new_field_name: int = 10

    @model_validator(mode="after")
    def handle_deprecated_fields(self):
        extra = self.model_extra or {}
        deprecated = []

        # Map old names to new names
        if "old_field_name" in extra:
            self.new_field_name = extra["old_field_name"]
            deprecated.append("old_field_name -> new_field_name")

        if deprecated:
            logging.warning(f"Deprecated config names: {', '.join(deprecated)}")
        return self

# ❌ DON'T: Define deprecated fields in schema with Optional[None]
class BadConfig(BaseModel):
    new_field_name: int = 10
    old_field_name: Optional[int] = None  # Pollutes schema, shows in model_dump()
```

Benefits of `extra="allow"` approach:
- Schema only shows current field names
- `model_dump()` returns clean output without deprecated fields
- Old configs still work (backwards compatible)
- Deprecation warnings guide users to update

See `prometheus/prometheus.py` PrometheusConfig for a complete example.

**LLM Integration**:
- Uses LiteLLM for multi-provider support (OpenAI, Anthropic, Azure, etc.)
- Structured tool calling with automatic retry and error handling
- Context-aware prompting with system instructions and examples

**Investigation Flow**:
1. Load user question/alert
2. Select relevant toolsets based on context
3. Execute LLM with available tools
4. LLM calls tools to gather data
5. LLM analyzes results and provides conclusions
6. Optionally write results back to source system

## Testing Framework

**Three-tier testing approach**:

1. **Unit Tests** (`tests/`): Standard pytest tests for individual components
2. **Integration Tests**: Test toolset integrations
3. **LLM Evaluation Tests** (`tests/llm/`): End-to-end tests using fixtures

**Running regular (non-LLM) tests**:
```bash
poetry run pytest tests -m "not llm"
make test-without-llm
```

**Running LLM eval tests**:
```bash
# Run specific eval - IMPORTANT: Use -k flag, NOT full test path with brackets
poetry run pytest -k "09_crashpod" --no-cov

# Run all evals in parallel
poetry run pytest tests/llm/ -n 6 --no-cov

# Regression evals
poetry run pytest -m 'llm and easy' --no-cov
```

For the complete eval CLI reference (flags, env vars, model comparison, debugging), see the `/create-eval` skill which contains full documentation in its reference files.

## Configuration

**Config File Location**: `~/.holmes/config.yaml`

**Key Configuration Sections**:
- `model`: LLM model to use (default: gpt-4.1)
- `api_key`: LLM API key (or use environment variables)
- `custom_toolsets`: Override or add toolsets
- `custom_runbooks`: Add investigation runbooks
- Platform-specific settings (alertmanager_url, jira_url, etc.)

**Environment Variables**:
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`: LLM API keys
- `OPENROUTER_API_KEY`: Alternative LLM provider via OpenRouter (domain: `api.openrouter.ai`)
- `MODEL`: Override default model(s) - supports comma-separated list
- `RUN_LIVE`: Use live tools in tests (strongly recommended)
- `BRAINTRUST_API_KEY`: For test result tracking and CI/CD report generation
- `BRAINTRUST_ORG`: Braintrust organization name (default: "robustadev")
- `ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY`: For Elasticsearch/OpenSearch cloud testing

## Development Guidelines

**Code Quality**:
- Use Ruff for formatting and linting (configured in pyproject.toml)
- Type hints required (mypy configuration in pyproject.toml)
- Pre-commit hooks enforce quality checks
- **ALWAYS place Python imports at the top of the file**, not inside functions or methods

**Documentation Examples**:
- **ALWAYS use Anthropic Claude models** in code examples and documentation:
  - Recommended: `anthropic/claude-sonnet-4-5-20250929` or `anthropic/claude-opus-4-5-20251101`
  - Use the latest Claude 4.5 family models (Sonnet or Opus)
- Avoid using deprecated or older model versions like `claude-3.5-sonnet`, `gpt-4-vision-preview`
- Do NOT use GPT-4o or Gemini models in documentation examples

**Testing Requirements**:
- All new features require unit tests
- New toolsets require integration tests
- Complex investigations should have LLM evaluation tests
- Maintain 40% minimum test coverage
- **Live execution is now enabled by default** to ensure tests match real-world behavior

**Pull Request Process**:
- PRs require maintainer approval
- Pre-commit hooks must pass
- LLM evaluation tests run automatically in CI
- Keep PRs focused and include tests
- **ALWAYS use `git commit -s`** to sign off commits (required for DCO)

**Git Workflow Guidelines**:
- ALWAYS create commits, NEVER amend
- ALWAYS merge, NEVER rebase
- ALWAYS push, NEVER force push
- Maintain a history of your work to allow the user to revert back to a previous iteration


**File Structure Conventions**:
- Toolsets: `holmes/plugins/toolsets/{name}.yaml` or `{name}/`
- Prompts: `holmes/plugins/prompts/{name}.jinja2`
- Tests: Match source structure under `tests/`

## Security Notes

- All tools have read-only access by design
- Bash toolset validates commands for safety
- No secrets should be committed to repository
- Use environment variables or config files for API keys
- RBAC permissions are respected for Kubernetes access

## Eval Tests (LLM Evaluations)

For creating, running, and debugging LLM eval tests, use the `/create-eval` skill. It contains the complete workflow, test_case.yaml field reference, anti-hallucination patterns, infrastructure setup guides, and CLI reference.

**Always run evals before submitting when possible:**
1. `poetry run pytest -k "test_name" --only-setup --no-cov` — verify setup
2. `poetry run pytest -k "test_name" --no-cov` — run full test
3. Verify cleanup: `kubectl get namespace app-NNN` should return NotFound

## Documentation Lookup

When asked about content from the HolmesGPT documentation website (https://holmesgpt.dev/), look in the local `docs/` directory:
- Python SDK examples: `docs/installation/python-installation.md`
- CLI installation: `docs/installation/cli-installation.md`
- Kubernetes deployment: `docs/installation/kubernetes-installation.md`
- Toolset documentation: `docs/data-sources/builtin-toolsets/`
- API reference: `docs/reference/`

## MkDocs Formatting Notes

When writing documentation in the `docs/` directory:

- **Lists after headers**: Always add a blank line between a header/bold text and a list, otherwise MkDocs won't render the list properly
  ```markdown
  **Good:**

  - item 1
  - item 2

  **Bad:**
  - item 1
  - item 2
  ```

- **Headers inside tabs**: Use **bold text** for section headings inside tabs, not markdown headers (`##`, `###`, etc.)

  **Why:** MkDocs Material font sizes make H2 (~25px) and H3 (~20px) visually larger than tab titles (~14px). When a header inside a tab is bigger than the tab title itself, it looks like it belongs outside/above the tabs, breaking the visual hierarchy.

  ```markdown
  <!-- GOOD: Bold text for sections inside tabs -->
  === "Tab Name"

      **Create the policy:**

      Instructions here...

      **Create the role:**

      More instructions...

  <!-- BAD: Headers inside tabs look like they're outside -->
  === "Tab Name"

      ### Create the policy

      Instructions here...
  ```

- **Avoid excessive headers**: Don't create a header for every small section. Headers should be used sparingly for major sections. For minor sections like test steps or examples, use bold text or combine content into a single code block with comments instead of separate headers.

  ```markdown
  <!-- BAD: Header for every test step -->
  ## Testing
  ### Test 1: Check Status
  ### Test 2: Check Logs
  ### Test 3: Health Check

  <!-- GOOD: Single section with combined content -->
  ## Testing the Connection

  ```bash
  # Check pod status
  kubectl get pods -n YOUR_NAMESPACE

  # Check logs
  kubectl logs -n YOUR_NAMESPACE

  # Health check
  curl http://localhost:8000/health
  ```
  ```

- **Don't describe Holmes's behavior**: In "Common Use Cases" sections, show only the example prompts. Don't explain what Holmes will do or list steps like "Holmes will: 1. Query X, 2. Analyze Y, 3. Return Z". Users will see this when they run it.

- **Skip Capabilities sections**: Don't list what a toolset/integration can do. Users discover capabilities by using Holmes. Feature lists become stale quickly.

- **Skip Security Best Practices sections**: Assume users understand basics like rotating credentials, using least privilege, and deleting local secrets. These sections add little value.

- **Consolidate troubleshooting commands**: Instead of separate headers for each troubleshooting scenario, use a single code block with comments:
  ```bash
  # Authentication errors - check if secret is mounted
  kubectl exec ...

  # Permission denied - verify roles
  gcloud projects get-iam-policy ...
  ```

- **Common Use Cases format**: Just example prompts, one per code block, no sub-headers, no explanations.

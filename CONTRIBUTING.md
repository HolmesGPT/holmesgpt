# Contributing

## Before you get started

### Code of Conduct

Please make sure to read and observe our [Governance](./GOVERNANCE.md) docs.

### Prerequisites

- Python `3.11` to `3.13` (Python 3.14+ is not yet supported due to a dependency limitation in prometrix)
  - **If using Python 3.14**, you can disable the Prometheus toolset to work around the issue:
    ```bash
    export DISABLE_PROMETHEUS_TOOLSET=true
    ```
- Poetry `1.8.4` or higher
- Git
- An LLM API key (required to use and test HolmesGPT)
  - HolmesGPT supports multiple providers: OpenAI, Anthropic, Azure, Google Vertex AI, and more
  - See [Supported LLM Providers](https://holmesgpt.dev/ai-providers/) to choose and set up your provider

## Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/HolmesGPT/holmesgpt.git
cd holmesgpt
```

### 2. Install dependencies

```bash
poetry install --with dev
```

This installs HolmesGPT and all development dependencies including test tools.

### 3. Set up your LLM API key

Set your API key as an environment variable based on your chosen provider:

```bash
# OpenAI
export OPENAI_API_KEY="your-api-key-here"

# Anthropic
export ANTHROPIC_API_KEY="your-api-key-here"

# Azure AI Foundry
export AZURE_API_KEY="your-api-key-here"
export AZURE_API_BASE="your-api-base-url"

# Other providers - see https://holmesgpt.dev/ai-providers/
```

Or add it to your shell profile (`.bashrc`, `.zshrc`, etc.) to persist across sessions.

You can also specify the model to use via the `MODEL` environment variable:

```bash
export MODEL="anthropic/claude-opus-4-5-20251101"
```

For full details on all supported providers and setup, see [Supported LLM Providers](https://holmesgpt.dev/ai-providers/).

### 4. Verify your setup

Test that Holmes runs correctly:

```bash
poetry run holmes ask "what OS are you running on?"
```

### 5. Run tests

```bash
# Run all tests except LLM-dependent ones
make test-without-llm

# Run LLM evaluation tests (requires API key)
make test-llm-ask-holmes
```

### 6. Run pre-commit checks (optional)

CI enforces configured pre-commit hooks on every PR (including docs-only PRs):
Ruff lint and formatting, private-key detection, end-of-file checks (for Python
and YAML), and isort. Failures fail the `build-and-test-gate` check. Local
checks are optional; skipping them with `--no-verify` does not skip CI checks.

Two hooks are currently skipped in CI and deferred to future work:
- `poetry-check`: skipped due to PEP 621 metadata vs. Poetry 1.8.5 lockfile format incompatibility
- `mypy`: skipped due to pre-existing type errors across legacy modules

To run the same checks locally:

```bash
SKIP=poetry-check,mypy poetry run pre-commit run --all-files --show-diff-on-failure
```

Some hooks apply automatic fixes. If CI reports changed files, run the checks
locally, review and commit the fixes, then rerun. CI does not push fixes to your
branch. The hooks use your available `python3` interpreter, including Python
3.14; the CI job uses Python 3.11.

## Reporting bugs

We encourage those interested to contribute code and also appreciate when issues are reported.

- Create a new issue and label is as `bug`
- Clearly state how to reproduce the bug:
  - Which LLM you've used
  - Which steps are required to reproduce
    - As LLMs answers may differ between runs - Does it always reproduce, or occasionally?


## Contributing Code

### Development Workflow

1. **Fork and clone**: Fork the repository and clone it locally
2. **Setup**: Follow the [Getting Started](#getting-started) section above
3. **Create a branch**: `git checkout -b feature/your-feature-name`
4. **Make changes**: Implement your feature or fix
5. **Add tests**: Add or update tests to ensure your changes are covered
6. **Run tests**: `make test-without-llm` to verify all tests pass
7. **Commit**: `git commit -s` (the `-s` flag signs your commit for DCO)
8. **Push and create a pull request**: Push to your fork and create a PR back to the upstream repository
9. **Code review**: Wait for a review and address any comments
10. **Follow governance**: See our [Governance](./GOVERNANCE.md) docs for code contribution guidelines

### Guidelines

- Keep pull requests small and focused—if you have multiple changes, open a separate PR for each
- All new features require unit tests
- New toolsets require integration tests
- Maintain 40% minimum test coverage
- Use `git commit -s --no-verify` when committing to skip local pre-commit hooks (all non-skipped hooks run in CI)
- Always create commits and merge (never force push or rebase)
- For complex documentation changes, see [docs/README.md](docs/README.md)

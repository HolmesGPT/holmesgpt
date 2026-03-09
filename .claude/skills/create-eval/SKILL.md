---
name: create-eval
description: This skill should be used when the user asks to "create an eval", "write an eval test", "add a new eval", "create a test case", "write a test for Holmes", or discusses LLM evaluation tests, eval fixtures, or test_case.yaml files for the HolmesGPT project.
version: 0.1.0
---

# Creating HolmesGPT Eval Tests

This skill provides the complete workflow for creating LLM evaluation tests in the HolmesGPT project. Eval tests validate that Holmes can correctly answer questions by querying real infrastructure and services.

## Test Structure

Each eval lives in its own directory under `tests/llm/fixtures/test_ask_holmes/`:

```
tests/llm/fixtures/test_ask_holmes/<NNN>_<descriptive_name>/
├── test_case.yaml          # Required: test definition
├── toolsets.yaml            # Optional: enable specific toolsets
├── manifest.yaml            # Optional: Kubernetes manifests
├── generate_*.py            # Optional: data generation scripts
└── other supporting files
```

Naming convention: `<3-digit-number>_<snake_case_description>` (e.g., `212_large_configmap_needle`).

## Creation Workflow

### Step 1: Choose Test Number and Namespace

Check existing tests to find the next available number:

```bash
ls tests/llm/fixtures/test_ask_holmes/ | sort -n | tail -5
```

The namespace must be `app-<testid>` (e.g., `app-212`). All pod and resource names must be unique across all tests.

### Step 2: Validate Tags

Only use tags that exist in `pyproject.toml` markers section. **Using invalid tags causes test collection failures.** Read `pyproject.toml` and check the `[tool.pytest.ini_options]` markers list before assigning tags. Ask the user before adding any new tag.

### Step 3: Write test_case.yaml

Core fields:

```yaml
user_prompt: "Specific question for Holmes to answer"

expected_output:
  - "Criterion 1: Must report exact value X"
  - "Criterion 2: Must include specific identifier Y"

tags:
  - kubernetes
  - question-answer
  - hard

before_test: |
  set -e
  # Setup infrastructure...

after_test: |
  kubectl delete namespace app-NNN --ignore-not-found
```

For the complete field reference and all available options, consult **`references/test-case-format.md`**.

### Step 4: Write toolsets.yaml (if needed)

When the test requires specific toolsets (Prometheus, Grafana, Elasticsearch, etc.):

```yaml
toolsets:
  kubernetes/core:
    enabled: true
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: http://localhost:10033
```

When a `toolsets.yaml` exists, **only explicitly enabled toolsets are available** to the LLM. All others are disabled.

### Step 5: Write Setup Scripts

The `before_test` script runs from the test's directory via `/bin/bash`. Key rules:

- Always start with `set -e` to fail on any error
- Use `kubectl create namespace app-NNN --dry-run=client -o yaml | kubectl apply -f -` for idempotent namespace creation
- Use `exit 1` when verification fails to fail the test early
- Clean up temp files at the end of before_test

**Verification focus: verify the needle, not the haystack.** The only verification that matters is that Holmes can discover the answer. Run the same kind of query Holmes would run and check that the expected value (the "smoking gun") is present. Do NOT exhaustively verify every piece of infrastructure — if the needle is queryable, the environment is working. Keep setup scripts short and readable.

```bash
# GOOD - verify the needle is discoverable (one targeted check)
kubectl get configmap platform-config -n app-212 \
  -o jsonpath='{.data.platform-config\.yaml}' | grep -q '7k3m9x'

# BAD - verifying everything (pod health, service endpoints, API responses, readiness...)
# This bloats the script without adding value
```

For retry loop patterns and other infrastructure details, consult **`references/infrastructure-patterns.md`**.

### Step 6: Design Anti-Hallucination Measures

Every eval must be designed so the LLM cannot pass by guessing. This is the most critical aspect of eval design.

Key principles:
- Embed unique random identifiers that cannot be guessed (e.g., `7k3m9x`)
- Test for specific values discoverable only by querying
- Use neutral resource names that don't hint at the problem
- Write prompts that test discovery ability, not domain knowledge

For detailed anti-hallucination patterns and examples, consult **`references/anti-hallucination.md`**.

## Mandatory Testing Workflow

**Always run evals before submitting when possible.** Follow this sequence:

### Phase 1: Verify Collection

```bash
poetry run pytest -k "test_name" --collect-only -q --no-cov
```

Confirm the test appears in the output. If not, check for tag or YAML errors.

### Phase 2: Run Setup Only

```bash
poetry run pytest -k "test_name" --only-setup --no-cov
```

Verify setup completes without errors. Check that infrastructure is ready.

### Phase 3: Run Full Test

```bash
poetry run pytest -k "test_name" --no-cov --skip-setup
```

Use `--skip-setup` to reuse the infrastructure from Phase 2. Verify the test passes.

### Phase 4: Verify Cleanup

```bash
kubectl get namespace app-NNN
```

Should return NotFound after the test completes (unless `--skip-cleanup` was used).

## Key Rules

1. **Namespace isolation**: Every test uses `app-<testid>` namespace
2. **Unique resource names**: Never reuse pod/service names across tests
3. **No `:latest` tags**: Always use specific container image versions
4. **Secrets for scripts**: Use Kubernetes Secrets for scripts, not ConfigMaps or inline
5. **No hints in names**: Avoid `broken-pod`, `crashloop-app` — use neutral names
6. **Sign commits**: Always use `git commit -s` for DCO compliance

## Quick Reference: Common Patterns

| Pattern | Example |
|---------|---------|
| Simple K8s test | Deploy pod, ask about status |
| Log analysis | Generate logs via script, ask Holmes to analyze |
| Metrics query | Deploy Prometheus + exporters, query metrics |
| Large data needle | Create large ConfigMap/resource, find specific value |
| Cloud service | Test against Elasticsearch/external API via env vars |

## Additional Resources

### Reference Files

For detailed documentation, consult:
- **`references/test-case-format.md`** — Complete test_case.yaml field reference with all options
- **`references/anti-hallucination.md`** — Anti-cheat testing patterns and prompt design
- **`references/infrastructure-patterns.md`** — Setup scripts, retry loops, port forwards, shared infra
- **`references/running-evals.md`** — CLI flags, environment variables, model comparison, debugging
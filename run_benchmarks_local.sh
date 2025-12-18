#!/bin/bash
# Local equivalent of .github/workflows/eval-benchmarks.yaml
# Run benchmarks locally with the same parameters and behavior as CI/CD

set -e  # Exit on error

# Default values from workflow
DEFAULT_MODELS="gpt-5.1,gpt-5,sonnet-4.5,haiku-4.5,deepseek-3.1"
DEFAULT_MARKERS="easy or medium or hard"
DEFAULT_ITERATIONS="1"

# Parse command line arguments
MODELS="${1:-$DEFAULT_MODELS}"
TEST_MARKERS="${2:-$DEFAULT_MARKERS}"
ITERATIONS="${3:-$DEFAULT_ITERATIONS}"
K_FILTER="${4:-}"  # Optional -k filter
PARALLEL="${5:-}"  # Optional -n parallelism
STRICT_SETUP="${6:-true}"  # Optional strict setup mode (default: true for benchmarks)

# Display help if requested
if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    echo "Usage: $0 [models] [test_markers] [iterations] [k_filter] [parallel] [strict_setup]"
    echo ""
    echo "Run LLM evaluation benchmarks locally (equivalent to GitHub Actions workflow)"
    echo ""
    echo "Arguments:"
    echo "  models        Comma-separated list of models (default: $DEFAULT_MODELS)"
    echo "  test_markers  Additional pytest markers - will be combined with 'llm'"
    echo "                (default: $DEFAULT_MARKERS)"
    echo "  iterations    Number of iterations per test, max 10 (default: $DEFAULT_ITERATIONS)"
    echo "  k_filter      Optional: Filter tests by name pattern (pytest -k equivalent, use '' to skip)"
    echo "  parallel      Optional: Number of parallel workers (pytest -n equivalent, use '' to skip)"
    echo "  strict_setup  Optional: true/false - fail if any test setup fails (default: true)"
    echo ""
    echo "Examples:"
    echo "  $0                                           # Use all defaults"
    echo "  $0 'gpt-4o'                                 # Test only gpt-4o with defaults"
    echo "  $0 'gpt-4o,claude-3-5-sonnet' 'easy' 3     # Custom models, easy tests, 3 iterations"
    echo "  $0 'gpt-4o' 'kubernetes and logs' 5        # Specific markers"
    echo "  $0 'gpt-4o' 'easy' 1 '01_how_many_pods'    # Run specific test by name"
    echo "  $0 'gpt-4o' 'easy' 1 'not 02_what_is_wrong' # Exclude specific test"
    echo "  $0 'gpt-4o' 'easy' 1 '' 6                  # Run with 6 parallel workers"
    echo "  $0 'gpt-4o' 'easy' 1 '01_how_many_pods' 4  # Specific test with 4 workers"
    echo "  $0 'gpt-4o' 'easy' 1 '' '' false           # Disable strict setup mode"
    echo ""
    echo "Environment variables used:"
    echo "  OPENAI_API_KEY, ANTHROPIC_API_KEY, AZURE_API_BASE, AZURE_API_KEY, AZURE_API_VERSION"
    echo "  BRAINTRUST_API_KEY (optional)"
    echo ""
    exit 0
fi

# Cap iterations at 10 (same as workflow)
if [ "$ITERATIONS" -gt 10 ]; then
    echo "Capping iterations at 10 (requested: $ITERATIONS)"
    ITERATIONS="10"
fi

# Build test command with markers (same logic as workflow)
# Always prepend "llm and " to user-provided markers with proper parentheses
TEST_MARKERS="llm and ($TEST_MARKERS)"

echo "=============================================="
echo "🧪 Running Local Benchmarks (CI/CD equivalent)"
echo "=============================================="
echo "Models:       $MODELS"
echo "Markers:      $TEST_MARKERS"
echo "Iterations:   $ITERATIONS"
[ -n "$K_FILTER" ] && echo "K Filter:     $K_FILTER"
[ -n "$PARALLEL" ] && echo "Parallel:     $PARALLEL workers"
echo "Strict Setup: $STRICT_SETUP"
echo "=============================================="
echo ""

# Check for Kubernetes cluster (tests may need it)
if kubectl cluster-info &>/dev/null; then
    echo "✅ Kubernetes cluster is accessible"
else
    echo "⚠️  No Kubernetes cluster found. Some tests may require a cluster."
fi

# Export environment variables (same as workflow step)
# Only set these if not already set
export MODEL="${MODELS:-$MODEL}"
export ITERATIONS="${ITERATIONS:-$ITERATIONS}"
export RUN_LIVE="${RUN_LIVE:-true}"
export CLASSIFIER_MODEL="${CLASSIFIER_MODEL:-gpt-4.1}"  # Always use OpenAI for classification (same as workflow)

# Set experiment ID if not already set
if [ -z "$EXPERIMENT_ID" ]; then
    export EXPERIMENT_ID="local-benchmark-$(date +%Y%m%d-%H%M%S)"
fi
export UPLOAD_DATASET="true"

echo "Environment setup:"
echo "  MODEL=$MODEL"
echo "  ITERATIONS=$ITERATIONS"
echo "  RUN_LIVE=$RUN_LIVE"
echo "  CLASSIFIER_MODEL=$CLASSIFIER_MODEL"
echo "  EXPERIMENT_ID=$EXPERIMENT_ID"
echo "  UPLOAD_DATASET=$UPLOAD_DATASET"
echo ""

# Check API keys
echo "Checking API keys..."
[ -n "$OPENAI_API_KEY" ] && echo "  ✓ OPENAI_API_KEY set" || echo "  ✗ OPENAI_API_KEY not set"
[ -n "$ANTHROPIC_API_KEY" ] && echo "  ✓ ANTHROPIC_API_KEY set" || echo "  ✗ ANTHROPIC_API_KEY not set"
[ -n "$AZURE_API_BASE" ] && echo "  ✓ AZURE_API_BASE set" || echo "  ✗ AZURE_API_BASE not set"
[ -n "$AZURE_API_KEY" ] && echo "  ✓ AZURE_API_KEY set" || echo "  ✗ AZURE_API_KEY not set"
[ -n "$BRAINTRUST_API_KEY" ] && echo "  ✓ BRAINTRUST_API_KEY set" || echo "  ⚠️  BRAINTRUST_API_KEY not set (optional)"
echo ""

# Build pytest command with optional arguments
PYTEST_CMD="poetry run pytest tests/llm/ -m \"$TEST_MARKERS\""
[ -n "$K_FILTER" ] && PYTEST_CMD="$PYTEST_CMD -k \"$K_FILTER\""
[ -n "$PARALLEL" ] && PYTEST_CMD="$PYTEST_CMD -n $PARALLEL"
# Add strict setup mode for benchmarks to ensure all tests run properly (if enabled)
# Allow test 22_high_latency_dbi_down to fail setup (known flaky setup)
if [[ "$STRICT_SETUP" == "true" ]]; then
    PYTEST_CMD="$PYTEST_CMD --strict-setup-mode=true --strict-setup-exceptions=22_high_latency_dbi_down"
else
    PYTEST_CMD="$PYTEST_CMD --strict-setup-mode=false"
fi
PYTEST_CMD="$PYTEST_CMD --no-cov --tb=short -v -s --json-report --json-report-file=eval_results.json"

# Run evaluation benchmarks (same command as workflow)
echo "Running pytest command:"
echo "  $PYTEST_CMD"
echo ""
echo "=============================================="
echo ""

# Run the tests (with || true like in workflow to not fail on test failures)
eval "$PYTEST_CMD" || true  # Don't fail the script if tests fail

# Show test execution summary (same as workflow)
echo ""
echo "==== Test execution summary ===="
echo "Models: $MODELS"
echo "Markers: $TEST_MARKERS"
echo "Iterations: $ITERATIONS"
[ -n "$K_FILTER" ] && echo "K Filter: $K_FILTER"
[ -n "$PARALLEL" ] && echo "Parallel: $PARALLEL workers"
echo "================================"

# Generate benchmark report (same as workflow)
echo ""
echo "Generating benchmark report..."
if [ -f "tests/generate_eval_report.py" ]; then
    # Generate latest results
    poetry run python tests/generate_eval_report.py \
        --json-file eval_results.json \
        --output-file docs/development/evaluations/latest-results.md \
        --models "$MODELS"
    echo "✅ Report generated: docs/development/evaluations/latest-results.md"

    # Also generate timestamped version for history
    mkdir -p docs/development/evaluations/history
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    HISTORY_FILE="docs/development/evaluations/history/results_${TIMESTAMP}.md"
    poetry run python tests/generate_eval_report.py \
        --json-file eval_results.json \
        --output-file "$HISTORY_FILE" \
        --models "$MODELS"
    echo "📁 Saved historical copy: $HISTORY_FILE"
else
    echo "⚠️  Report generation script not found: tests/generate_eval_report.py"
fi

# Show generated files
echo ""
echo "Generated files:"
[ -f "eval_results.json" ] && echo "  ✓ eval_results.json ($(wc -l < eval_results.json) lines)"
[ -f "evals_report.md" ] && echo "  ✓ evals_report.md ($(wc -l < evals_report.md) lines)"
[ -f "docs/development/evaluations/latest-results.md" ] && echo "  ✓ docs/development/evaluations/latest-results.md ($(wc -l < docs/development/evaluations/latest-results.md) lines)"
[ -f "$HISTORY_FILE" ] && echo "  ✓ $HISTORY_FILE ($(wc -l < "$HISTORY_FILE") lines)"

echo ""
echo "=============================================="
echo "✅ Benchmark run complete!"
echo ""
echo "To commit results (like workflow would on main):"
echo "  git add docs/development/evaluations/latest-results.md"
echo "  git commit -m 'Update benchmark results [skip ci]'"
echo "=============================================="

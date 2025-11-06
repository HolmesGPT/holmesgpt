#!/bin/bash
# KAITO Holmes Evaluation Script
# Custom script for running Holmes evaluations with KAITO qwen2.5-coder-7b-instruct model

set -e  # Exit on error

# Default values for KAITO setup
DEFAULT_MODELS="qwen2.5-coder-7b-instruct"  # Correct KAITO model name
DEFAULT_MARKERS="easy"  # Start with easy tests
DEFAULT_ITERATIONS="1"

# Braintrust configuration
BRAINTRUST_API_KEY="${BRAINTRUST_API_KEY:-sk-sRJWnegTCpc7QdN4Xz3UGZkG905Rxc86NOHmsOVhks17m2oK}"
BRAINTRUST_ORG="${BRAINTRUST_ORG:-KAITO-HolmesGPT}"

# Parse command line arguments and handle --max-steps
MAX_STEPS="7"  # Default max steps
ARGS=()

# Process arguments to extract --max-steps
while [[ $# -gt 0 ]]; do
    case $1 in
        --max-steps)
            MAX_STEPS="$2"
            shift 2
            ;;
        --max-steps=*)
            MAX_STEPS="${1#*=}"
            shift
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

# Now set positional arguments from remaining args
MODELS="${ARGS[0]:-$DEFAULT_MODELS}"
TEST_MARKERS="${ARGS[1]:-$DEFAULT_MARKERS}"
ITERATIONS="${ARGS[2]:-$DEFAULT_ITERATIONS}"
K_FILTER="${ARGS[3]:-}"  # Optional -k filter for specific tests
ENABLE_BRAINTRUST="${ARGS[4]:-true}"  # Enable Braintrust by default

# Display help if requested
if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    echo "Usage: $0 [models] [test_markers] [iterations] [k_filter] [enable_braintrust] [--max-steps N]"
    echo ""
    echo "Run Holmes evaluations with KAITO qwen2.5-coder-7b-instruct model"
    echo ""
    echo "Arguments:"
    echo "  models            Model name (default: $DEFAULT_MODELS)"
    echo "  test_markers      pytest markers (default: $DEFAULT_MARKERS)"
    echo "  iterations        Number of iterations per test (default: $DEFAULT_ITERATIONS)"
    echo "  k_filter          Optional: Filter tests by name pattern (use '' to skip)"
    echo "  enable_braintrust Enable Braintrust tracking (default: true)"
    echo "  --max-steps N     Maximum tool call steps (default: 7)"
    echo ""
    echo "Braintrust Configuration:"
    echo "  BRAINTRUST_API_KEY: $BRAINTRUST_API_KEY"
    echo "  BRAINTRUST_ORG:     $BRAINTRUST_ORG"
    echo ""
    echo "Examples:"
    echo "  $0                                    # Run easy tests with defaults + Braintrust"
    echo "  $0 'openai/qwen2.5-coder-7b-instruct' 'easy' 1 '01_how_many_pods'  # Specific test"
    echo "  $0 'openai/qwen2.5-coder-7b-instruct' 'easy' 1 '' true --max-steps 10  # 10 steps + Braintrust"
    echo "  $0 'openai/qwen2.5-coder-7b-instruct' 'easy and kubernetes' 3 '' true  # Multiple iterations"
    echo "  $0 'openai/qwen2.5-coder-7b-instruct' 'easy' 1 '' false   # Disable Braintrust"
    echo ""
    exit 0
fi

echo "=============================================="
echo "🤖 KAITO Holmes Evaluation"
echo "=============================================="
echo "Holmes Model: $MODELS (port 8080)"
echo "Eval Model:   deepseek-r1-distill-qwen-14b (port 8081)"
echo "Markers:      $TEST_MARKERS"
echo "Iterations:   $ITERATIONS"
[ -n "$K_FILTER" ] && echo "K Filter:     $K_FILTER"
echo "Max Steps:    $MAX_STEPS"
echo "Toolsets:     2 (kubernetes/core + aks/core)"
if [[ "$ENABLE_BRAINTRUST" == "true" ]]; then
    echo "🧠 Braintrust:  Enabled (Org: $BRAINTRUST_ORG)"
else
    echo "🧠 Braintrust:  Disabled"
fi
echo "=============================================="
echo ""

# Verify KAITO endpoint is available
echo "Checking KAITO endpoint..."
if curl -s "http://localhost:8080/v1/models" > /dev/null; then
    echo "✅ KAITO endpoint is accessible"
else
    echo "❌ KAITO endpoint not accessible - make sure port forwarding is active"
    echo "   Run: kubectl port-forward service/workspace-qwen2-5-coder-7b-instruct 8080:80"
    exit 1
fi

echo "Checking DeepSeek endpoint..."
if curl -s "http://localhost:8081/v1/models" > /dev/null; then
    echo "✅ DeepSeek endpoint is accessible"
else
    echo "❌ DeepSeek endpoint not accessible - make sure port forwarding is active"
    echo "   Run: kubectl port-forward service/workspace-deepseek-r1-qwen-14b 8081:80"
    exit 1
fi
echo ""

# Check for Kubernetes cluster
if kubectl cluster-info &>/dev/null; then
    echo "✅ Kubernetes cluster is accessible"
else
    echo "⚠️  No Kubernetes cluster found. Tests require a cluster."
    exit 1
fi

# Set KAITO-specific environment variables for Holmes
export HOLMES_OPENAI_BASE_URL="http://localhost:8080/v1"
export HOLMES_OPENAI_API_KEY="fake-key-for-kaito"
export HOLMES_TOOL_CHOICE="required"  # Use required for proper tool calling
export KAITO_CONFIG_PATH="/Users/nickthevenin/holmes-aikit-eval/super-minimal-config.yaml"

# Set standard OpenAI env vars for the evaluation system
export OPENAI_API_KEY="fake-key-for-kaito"
export OPENAI_BASE_URL="http://localhost:8080/v1"
export OPENAI_API_BASE="http://localhost:8080/v1"

# Set up Braintrust tracking if enabled
if [[ "$ENABLE_BRAINTRUST" == "true" ]]; then
    export BRAINTRUST_API_KEY="$BRAINTRUST_API_KEY"
    export BRAINTRUST_ORG="$BRAINTRUST_ORG"
    echo "🧠 Braintrust tracking enabled"
    echo "   Organization: $BRAINTRUST_ORG"
    echo "   API Key: ${BRAINTRUST_API_KEY:0:8}..."
else
    # Unset Braintrust variables to disable tracking
    unset BRAINTRUST_API_KEY BRAINTRUST_ORG
    echo "🧠 Braintrust tracking disabled"
fi

# Force refresh toolsets to pick up new config
export REFRESH_TOOLSETS="true"

echo "Environment setup:"
echo "  HOLMES_OPENAI_BASE_URL: $HOLMES_OPENAI_BASE_URL"
echo "  HOLMES_OPENAI_API_KEY: $HOLMES_OPENAI_API_KEY"  
echo "  HOLMES_TOOL_CHOICE: $HOLMES_TOOL_CHOICE"
echo "  KAITO_CONFIG_PATH: $KAITO_CONFIG_PATH"
echo "  OPENAI_API_KEY: $OPENAI_API_KEY"
echo "  OPENAI_BASE_URL: $OPENAI_BASE_URL"
if [[ "$ENABLE_BRAINTRUST" == "true" ]]; then
    echo "  BRAINTRUST_API_KEY: ${BRAINTRUST_API_KEY:0:8}... (Enabled)"
    echo "  BRAINTRUST_ORG: $BRAINTRUST_ORG"
else
    echo "  BRAINTRUST_API_KEY: (Disabled)"
fi
echo ""# Set model environment variables  
export MODEL="$MODELS"  # Holmes query model (qwen on port 8080)
# Use correct DeepSeek model name for classifier endpoint
export CLASSIFIER_MODEL="deepseek-r1-distill-qwen-14b"  # Correct model name for DeepSeek endpoint

# Set up dual endpoints: Holmes on 8080, Classifier on 8081  
export CLASSIFIER_OPENAI_API_BASE="http://localhost:8081/v1"  # DeepSeek endpoint for evaluation
export CLASSIFIER_OPENAI_API_KEY="fake-key-for-kaito"

export RUN_LIVE="true"

# Optimize for accuracy with KAITO model
export TEMPERATURE="0.00000001"
export MAX_RETRIES="1"
export MAX_STEPS="$MAX_STEPS"  # Set max steps from command line

# Set experiment ID (fixed for grouping multiple test runs)
export EXPERIMENT_ID="kaito-eval-20251020-132146"
export UPLOAD_DATASET="true"

echo "Environment setup:"
echo "  OPENAI_API_BASE=$OPENAI_API_BASE"
echo "  OPENAI_API_KEY=$OPENAI_API_KEY"
echo "  HOLMES_TOOL_CHOICE=$HOLMES_TOOL_CHOICE"
echo "  MODEL=$MODEL"
echo "  ITERATIONS=$ITERATIONS"
echo "  RUN_LIVE=$RUN_LIVE"
echo "  CLASSIFIER_MODEL=$CLASSIFIER_MODEL"
echo "  CLASSIFIER_OPENAI_API_BASE=$CLASSIFIER_OPENAI_API_BASE"
echo "  EXPERIMENT_ID=$EXPERIMENT_ID"
echo "  KAITO_CONFIG_PATH=$KAITO_CONFIG_PATH"
echo ""

# Build test markers (always prepend "llm and")
TEST_MARKERS="llm and ($TEST_MARKERS)"

# Build pytest command
PYTEST_CMD="poetry run pytest tests/llm/test_ask_holmes.py -m \"$TEST_MARKERS\""
[ -n "$K_FILTER" ] && PYTEST_CMD="$PYTEST_CMD -k \"$K_FILTER\""
PYTEST_CMD="$PYTEST_CMD --no-cov --tb=short -v -s"

echo "Running pytest command:"
echo "  $PYTEST_CMD"
echo ""
echo "=============================================="
echo ""

# Run the tests (with || true to not fail script on test failures)
eval "$PYTEST_CMD" || true

# Show test execution summary
echo ""
echo "==== KAITO Test Execution Summary ===="
echo "Models: $MODELS"
echo "Markers: $TEST_MARKERS"
echo "Iterations: $ITERATIONS"
[ -n "$K_FILTER" ] && echo "K Filter: $K_FILTER"
echo "Max Steps: $MAX_STEPS"
echo "====================================="

# Show generated files
echo ""
echo "Generated files:"
echo "  ✓ Test output displayed above"

echo ""
echo "=============================================="
echo "✅ KAITO evaluation run complete!"
echo ""
echo "Next steps:"
echo "  - Review test output above for results"
if [[ "$ENABLE_BRAINTRUST" == "true" ]]; then
    echo "  - View detailed traces at: https://www.braintrust.dev/app/$BRAINTRUST_ORG"
fi
echo "  - Run with specific test: $0 '' '' 1 '01_how_many_pods'"
echo "  - Try more iterations: $0 '' '' 3"
echo "  - Disable Braintrust: $0 '' '' 1 '' false"
echo "=============================================="
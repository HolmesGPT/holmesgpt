import logging
import os
from contextlib import contextmanager
import pytest
from pytest_shared_session_scope import (
    shared_session_scope_json,
    SetupToken,
    CleanupToken,
)

from tests.llm.utils.test_results import TestResult
from tests.llm.utils.classifiers import create_llm_client
from holmes.common.env_vars import DEFAULT_MODEL
from tests.llm.utils.mock_toolset import (  # type: ignore[attr-defined]
    MockMode,
    MockGenerationConfig,
    report_mock_operations,
)
from tests.llm.utils.reporting.terminal_reporter import handle_console_output
from tests.llm.utils.reporting.github_reporter import handle_github_output
from tests.llm.utils.braintrust import get_braintrust_url
from tests.llm.utils.setup_cleanup import (
    run_all_test_setup,
    log,
    extract_llm_test_cases,
)
from tests.llm.utils.port_forward import (
    setup_all_port_forwards,
    extract_port_forwards_from_test_cases,
    cleanup_port_forwards_by_config,
    check_port_availability_early,
)
from tests.llm.utils.env_vars import is_run_live_enabled
from tests.llm.utils.test_case_utils import create_eval_llm, _model_list_exists
from tests.llm.utils.test_env_vars import (
    MODEL,
    CLASSIFIER_MODEL,
    OPENAI_API_KEY,
    ANTHROPIC_API_KEY,
    AZURE_API_KEY,
    AZURE_API_BASE,
    ASK_HOLMES_TEST_TYPE,
    BRAINTRUST_API_KEY,
)


# Configuration constants
DEBUG_SEPARATOR = "=" * 80
LLM_TEST_TYPES = ["test_ask_holmes", "test_investigate", "test_workload_health"]


def is_llm_test(nodeid: str) -> bool:
    """Check if a test nodeid is for an LLM test."""
    return any(
        [
            "test_ask_holmes" in nodeid,
            "test_investigate" in nodeid,
            "test_workload_health" in nodeid,
        ]
    )


@pytest.fixture(scope="session")
def mock_generation_config(request):
    """Session-scoped fixture that provides mock generation configuration and mode."""
    # Safely get options with defaults in case they're not registered
    generate_mocks = request.config.getoption("--generate-mocks")
    regenerate_all_mocks = request.config.getoption("--regenerate-all-mocks")

    # --regenerate-all-mocks implies --generate-mocks
    if regenerate_all_mocks:
        generate_mocks = True

    run_live = is_run_live_enabled()
    if generate_mocks and not run_live:
        print(
            "⚠️  WARNING: --generate-mocks is set but RUN_LIVE is not set. This will not generate mocks."
        )
        pytest.skip(
            "Skipping test case because --generate-mocks is set but RUN_LIVE is not set."
        )

    # Determine mode based on environment and options

    if generate_mocks:
        mode = MockMode.GENERATE  # live & generate
    elif run_live:
        mode = MockMode.LIVE
    else:
        mode = MockMode.MOCK

    return MockGenerationConfig(generate_mocks, regenerate_all_mocks, mode)


# Handles before_test and after_test
# see https://github.com/StefanBRas/pytest-shared-session-scope
@shared_session_scope_json()
def shared_test_infrastructure(request, mock_generation_config: MockGenerationConfig):
    """Shared session-scoped fixture for test infrastructure setup/cleanup coordination"""
    collect_only = request.config.getoption("--collect-only")
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", None)

    # If we're in collect-only mode or RUN_LIVE is not set, skip setup/cleanup entirely
    if collect_only or mock_generation_config.mode == MockMode.MOCK:
        log(
            f"\n⚙️ Skipping shared test infrastructure setup/cleanup on worker {worker_id} (mode: {mock_generation_config.mode}, collect_only: {collect_only})"
        )
        # Must yield twice even when skipping due to how pytest-shared-session-scope works
        initial = yield
        cleanup_token = yield {"test_cases_for_cleanup": []}
        return

    # First yield: get initial value (SetupToken.FIRST if first worker, data if subsequent)
    initial = yield

    if initial is SetupToken.FIRST:
        # This is the first worker to run the fixture
        # Extract all test cases (we need them all for port forwards)
        test_cases = extract_llm_test_cases(request.session)

        # Clear mock directories if --regenerate-all-mocks is set
        cleared_directories = []
        regenerate_all = request.config.getoption("--regenerate-all-mocks")

        if regenerate_all:
            from tests.llm.utils.mock_toolset import clear_all_mocks  # type: ignore[attr-defined]

            cleared_directories = clear_all_mocks(request.session)

        # Run setup unless --skip-setup is set
        # Check port availability BEFORE running any setup scripts
        # This returns a dict of test IDs to skip reasons
        tests_to_skip_port_conflicts = check_port_availability_early(test_cases)
        if tests_to_skip_port_conflicts:
            log(
                f"⚠️  {len(tests_to_skip_port_conflicts)} tests will be skipped due to port conflicts:"
            )
            for test_id, reason in tests_to_skip_port_conflicts.items():
                log(f"     • {test_id}: {reason}")

        # Filter out tests with port conflicts
        tests_to_run = [
            tc for tc in test_cases if tc.id not in tests_to_skip_port_conflicts
        ]

        # Check skip-setup option and only-cleanup option
        skip_setup = request.config.getoption("--skip-setup")
        only_cleanup = request.config.getoption("--only-cleanup", False)

        # Skip setup if --skip-setup or --only-cleanup is set
        if tests_to_run and not skip_setup and not only_cleanup:
            setup_failures = run_all_test_setup(tests_to_run)
        elif skip_setup:
            log("⚙️ Skipping test setup due to --skip-setup flag")
            setup_failures = {}
        elif only_cleanup:
            log("⚙️ Skipping test setup due to --only-cleanup flag")
            setup_failures = {}
        else:
            setup_failures = {}

        # Check strict setup mode
        strict_setup_mode_str = request.config.getoption("--strict-setup-mode", "false")
        strict_setup_mode = strict_setup_mode_str.lower() == "true"
        strict_setup_exceptions = request.config.getoption(
            "--strict-setup-exceptions", ""
        )

        if strict_setup_mode and setup_failures:
            # Parse exceptions list
            allowed_failures = set(
                [x.strip() for x in strict_setup_exceptions.split(",") if x.strip()]
            )

            # Check if any failures are not in the allowed list
            non_allowed_failures = {
                test_id: error
                for test_id, error in setup_failures.items()
                if test_id not in allowed_failures
            }

            if non_allowed_failures:
                log("\n" + "=" * 80, dark_red=True)
                log("❌ STRICT SETUP MODE: Setup failures detected!", dark_red=True)
                log("=" * 80, dark_red=True)
                log(
                    f"\nThe following {len(non_allowed_failures)} test(s) had setup failures:",
                    dark_red=True,
                )
                for test_id, error_msg in non_allowed_failures.items():
                    log(f"\n  • {test_id}", dark_red=True)
                    # Show first 3 lines of error for context
                    error_lines = error_msg.split("\n")[:3]
                    for line in error_lines:
                        if line.strip():
                            log(f"    {line}", dark_red=True)

                if allowed_failures:
                    allowed_with_failures = allowed_failures.intersection(
                        setup_failures.keys()
                    )
                    if allowed_with_failures:
                        log(
                            f"\n✓ The following test(s) were allowed to fail: {', '.join(allowed_with_failures)}",
                            error=False,
                        )

                log("\n" + "=" * 80, dark_red=True)
                log(
                    "Exiting pytest due to setup failures in strict mode.",
                    dark_red=True,
                )
                log("To proceed anyway, either:", dark_red=True)
                log("  1. Fix the setup issues and run again", dark_red=True)
                log("  2. Add test IDs to --strict-setup-exceptions", dark_red=True)
                log(
                    "  3. Use --strict-setup-mode=false (or remove the flag)",
                    dark_red=True,
                )
                log(
                    "  4. Run script with: ./run_benchmarks_local.sh <models> <markers> <iterations> <filter> <parallel> false",
                    dark_red=True,
                )
                log("=" * 80 + "\n", dark_red=True)

                # Skip port forwards and cleanup - just exit immediately
                log(
                    "\n⚙️ Skipping port forwards and cleanup due to strict setup failure",
                    error=False,
                )

                # Properly stop pytest execution across all workers
                # Use pytest.exit() which works correctly with xdist
                import pytest

                pytest.exit(
                    "Exiting due to setup failures in strict mode", returncode=1
                )

        # Check if we're in --only-setup mode
        only_setup = request.config.getoption("--only-setup", False)

        # Set up port forwards AFTER namespace/resources are created
        # Skip port forwards for both --only-cleanup and --only-setup modes
        if not only_cleanup and not only_setup:
            setup_all_port_forwards(tests_to_run)
        elif only_cleanup:
            log("⚙️ Skipping port forward setup due to --only-cleanup flag")
        elif only_setup:
            log("⚙️ Skipping port forward setup due to --only-setup flag")

        port_configs = extract_port_forwards_from_test_cases(tests_to_run)

        data = {
            "test_cases_for_cleanup": [tc.id for tc in tests_to_run],
            "cleared_mock_directories": cleared_directories,
            "setup_failures": setup_failures,
            # Store port forward configs for cleanup (not the manager object)
            "port_forward_configs": port_configs,
            # Store test IDs that should be skipped due to port conflicts
            "tests_to_skip_port_conflicts": tests_to_skip_port_conflicts,
        }
    else:
        log(f"⚙️ Skipping before_test/after_test on worker {worker_id}")
        # This is a worker using the fixture after the first worker
        data = initial

    # Actual test runs here when we yield - then we get back a cleanup token from pytest-shared-session-scope
    cleanup_token = yield data

    if cleanup_token is CleanupToken.LAST:
        # This is the last worker to exit - responsible for cleanup
        test_case_ids = data.get("test_cases_for_cleanup", [])
        if not isinstance(test_case_ids, list):
            test_case_ids = []

        # Check skip-cleanup option and only-cleanup/only-setup options
        skip_cleanup = request.config.getoption("--skip-cleanup")
        only_cleanup = request.config.getoption("--only-cleanup", False)
        only_setup = request.config.getoption("--only-setup", False)

        # Clean up port forwards only if NOT in --only-setup or --only-cleanup mode
        # (for --skip-cleanup and --skip-setup, we still clean up port forwards)
        if not only_setup and not only_cleanup:
            port_forward_configs = data.get("port_forward_configs", [])
            if port_forward_configs and isinstance(port_forward_configs, list):
                try:
                    # Kill any kubectl port-forward processes that match our configs
                    cleanup_port_forwards_by_config(port_forward_configs)
                except Exception as e:
                    log(f"⚠️ Error cleaning up port forwards: {e}")

        # Run cleanup if --only-cleanup is set OR if (not skipping cleanup AND not --only-setup)
        if test_case_ids and (only_cleanup or (not skip_cleanup and not only_setup)):
            # Reconstruct test cases from IDs
            from tests.llm.utils.test_case_utils import HolmesTestCase  # type: ignore[attr-defined]  # type: ignore[attr-defined]

            cleanup_test_cases = []

            for item in request.session.items:
                if (
                    item.get_closest_marker("llm")
                    and hasattr(item, "callspec")
                    and "test_case" in item.callspec.params
                ):
                    test_case = item.callspec.params["test_case"]
                    if (
                        isinstance(test_case, HolmesTestCase)
                        and test_case.id in test_case_ids
                        and test_case not in cleanup_test_cases
                    ):
                        cleanup_test_cases.append(test_case)

            if cleanup_test_cases:
                from tests.llm.utils.setup_cleanup import (
                    run_all_test_commands,
                    Operation,
                )

                # Only run the after_test commands, not port forward cleanup
                if only_cleanup:
                    log("⚙️ Running cleanup due to --only-cleanup flag")
                run_all_test_commands(cleanup_test_cases, Operation.CLEANUP)
        elif skip_cleanup:
            log("⚙️ Skipping test cleanup due to --skip-cleanup flag")


# TODO: do we actually need this?
@pytest.fixture(scope="session", autouse=True)
def test_infrastructure_coordination(shared_test_infrastructure):
    """Ensure the shared test infrastructure fixture is used (triggers setup/cleanup)"""
    # This fixture just ensures shared_test_infrastructure runs for all sessions
    # All the actual logic is in shared_test_infrastructure
    yield


@contextmanager
def force_pytest_output(request):
    """Context manager to force output display even when pytest captures stdout"""
    capman = request.config.pluginmanager.getplugin("capturemanager")
    if capman:
        capman.suspend_global_capture(in_=True)
    try:
        yield
    finally:
        if capman:
            capman.resume_global_capture()


def check_llm_api_with_test_call():
    """Check if LLM API is available by testing ALL models that will be used"""
    import litellm

    # Get all models that will be tested
    test_models = MODEL.split(",")

    # Also check the classifier model
    classifier_model = CLASSIFIER_MODEL
    if not classifier_model:
        # Parse MODEL to get first model for API key checking
        # Note: get_models() will enforce CLASSIFIER_MODEL requirement for multi-model tests
        models = [m.strip() for m in MODEL.split(",") if m.strip()]
        classifier_model = models[0] if models else DEFAULT_MODEL

    failed_models = []
    error_messages = []

    # Check each test model using LiteLLM's built-in functions
    for model_name in test_models:
        model_name = model_name.strip()

        llm = None
        if _model_list_exists():
            try:
                llm = create_eval_llm(model_name)
                model_name = llm.model
            except Exception:
                pass

        # Get provider info for better error messages
        provider_info = litellm.get_llm_provider(model_name)
        actual_provider = provider_info[1] if provider_info else "unknown"

        env_check = {"keys_in_environment": True}

        # only check env vars if we're not using a model list (credentials are in config, not env vars)
        if not llm:
            # validate_environment only checks for other keys
            if not (
                actual_provider == "bedrock"
                and "AWS_BEARER_TOKEN_BEDROCK" in os.environ
            ):
                env_check = litellm.validate_environment(model=model_name)

        if not env_check["keys_in_environment"]:
            # Environment is missing required keys
            failed_models.append(model_name)
            missing_keys = ", ".join(env_check["missing_keys"])

            # Build helpful message based on provider and what's missing
            if actual_provider == "azure":
                provider_msg = f"Missing environment variables for Azure (model: {model_name}): {missing_keys}"
            elif actual_provider == "anthropic":
                provider_msg = f"Missing environment variables for Anthropic (model: {model_name}): {missing_keys}"
            elif actual_provider == "openai":
                provider_msg = f"Missing environment variables for OpenAI (model: {model_name}): {missing_keys}. Note: AZURE_API_BASE is set but this model uses OpenAI, not Azure."
            elif actual_provider == "bedrock":
                provider_msg = f"Missing environment variables for bedrock (model: {model_name}): {missing_keys}. Note: You can alternatively define AWS_BEARER_TOKEN_BEDROCK."
            else:
                provider_msg = f"Missing environment variables for {actual_provider} (model: {model_name}): {missing_keys}"

            error_messages.append(provider_msg)
            continue  # Skip API test if env vars are missing

        # Step 2: Environment is OK, now test if the API actually works
        try:
            if llm:
                llm.completion(messages=[{"role": "user", "content": "test"}])
            else:
                litellm.completion(
                    model=model_name,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=1000,
                )
        except Exception as e:
            failed_models.append(model_name)
            error_str = str(e)

            # Build helpful message for API failures (env vars present but call failed)
            if actual_provider == "azure":
                provider_msg = f"Azure API call failed (model: {model_name}). Check AZURE_API_BASE, AZURE_API_KEY, AZURE_API_VERSION."
            elif actual_provider == "anthropic":
                provider_msg = f"Anthropic API call failed (model: {model_name}). Check ANTHROPIC_API_KEY."
            elif actual_provider == "openai":
                provider_msg = f"OpenAI API call failed (model: {model_name}). Check OPENAI_API_KEY. Note: AZURE_API_BASE is set but this model uses OpenAI, not Azure."
            else:
                provider_msg = (
                    f"{actual_provider} API call failed (model: {model_name})."
                )

            error_msg = f"{provider_msg}\n    Error: {error_str}"
            error_messages.append(error_msg)

    if _model_list_exists():
        # If model list exists, we don't need to check classifier model since its checked in the model list
        return True, None

    # Check classifier model (using the original logic for compatibility)
    try:
        client, model = create_llm_client()
        client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "test"}], max_tokens=1
        )
    except Exception as e:
        failed_models.append(f"classifier:{classifier_model}")
        # Build helpful provider-specific message for classifier
        # Note: create_llm_client() uses different logic than LiteLLM:
        # It uses Azure if AZURE_API_BASE is set, regardless of model name
        if AZURE_API_BASE:
            provider_msg = f"Tried to use Azure for classifier (model: {classifier_model}). Check AZURE_API_BASE, AZURE_API_KEY, AZURE_API_VERSION, or unset AZURE_API_BASE to use OpenAI."
        else:
            provider_msg = f"Tried to use OpenAI for classifier (model: {classifier_model}). Check OPENAI_API_KEY or set AZURE_API_BASE to use Azure."

        # Add helpful suggestion for gpt-5 models that may have parameter issues
        if "gpt-5" in classifier_model.lower():
            provider_msg += "\n    💡 Tip: If you're seeing parameter errors (e.g., 'max_tokens' not supported), try using: export CLASSIFIER_MODEL=gpt-4.1"
            logging.warning(
                f"Classifier model '{classifier_model}' contains 'gpt-5' and encountered an error. "
                f"If the error is about unsupported parameters, try: export CLASSIFIER_MODEL=gpt-4.1"
            )

        error_messages.append(f"{provider_msg}\n    Error: {str(e)}")

    # Report results
    if failed_models:
        # Gather environment info for better error message
        error_msg = "Failed to validate API access for the following models:\n\n"
        # Add spacing between error messages for better readability
        formatted_errors = []
        for msg in error_messages:
            # Each error message already has provider_msg\n    Error: format
            # Add bullet and proper indentation
            formatted_errors.append(f"  - {msg}")
        error_msg += "\n\n".join(formatted_errors)
        error_msg += "\n\nEnvironment status:\n"
        error_msg += f"  - OPENAI_API_KEY: {'set' if OPENAI_API_KEY else 'not set'}\n"
        error_msg += (
            f"  - ANTHROPIC_API_KEY: {'set' if ANTHROPIC_API_KEY else 'not set'}\n"
        )
        error_msg += f"  - AZURE_API_KEY: {'set' if AZURE_API_KEY else 'not set'}\n"
        error_msg += f"  - AZURE_API_BASE: {AZURE_API_BASE or 'not set'}\n"
        # Show classifier model - if CLASSIFIER_MODEL env var is unset, show the actual value being used
        if CLASSIFIER_MODEL:
            error_msg += f"  - CLASSIFIER_MODEL: {CLASSIFIER_MODEL}\n"
        else:
            error_msg += f"  - CLASSIFIER_MODEL: not set (using: {classifier_model})\n"

        return False, error_msg

    return True, None


def pytest_collection_modifyitems(config, items):
    """
    Hook to modify test collection. Runs BEFORE any tests start.
    This ensures we validate LLM availability before pytest starts executing tests.
    """
    # Don't validate during collection-only mode
    if config.getoption("--collect-only"):
        return

    # Check if LLM marker is being excluded
    markexpr = config.getoption("-m", default="")
    if "not llm" in markexpr:
        return

    # Find all LLM tests
    llm_tests = [item for item in items if item.get_closest_marker("llm")]

    if llm_tests:
        # Check API connectivity
        api_available, error_msg = check_llm_api_with_test_call()

        # Store the result in config to avoid re-checking later
        config._llm_api_available = api_available
        config._llm_api_error_msg = error_msg

        if not api_available:
            # Print skip message immediately
            print("\n" + "=" * 70)
            print(f"ℹ️  INFO: {len(llm_tests)} LLM evaluation tests will be skipped")
            print()
            print(f"  Reason: {error_msg}")
            print()
            print("To see all available evals:")
            print(
                "  poetry run pytest -m llm --collect-only -q --no-cov --disable-warnings"
            )
            print()
            print("To run a specific eval:")
            print("  poetry run pytest --no-cov -k 01_how_many_pods")
            print("=" * 70 + "\n")

            # Mark all LLM tests as skipped with the detailed error message
            for test in llm_tests:
                test.add_marker(pytest.mark.skip(reason=error_msg))


@pytest.fixture(scope="session", autouse=True)
def llm_availability_check(request):
    """Handle LLM test session setup: show warning message only"""
    # Don't show messages during collection-only mode
    # Check if we're in collect-only mode
    collect_only = request.config.getoption("--collect-only")

    if collect_only:
        return

    # Check if LLM marker is being excluded
    markexpr = request.config.getoption("-m", default="")
    if "not llm" in markexpr:
        return  # Don't show warning if explicitly excluding LLM tests

    # session.items contains the final filtered list of tests that will actually run
    session = request.session
    llm_tests = [item for item in session.items if item.get_closest_marker("llm")]

    if llm_tests:
        # Use the cached result from pytest_collection_modifyitems if available
        # Otherwise check now (this handles cases where the hook didn't run)
        if hasattr(request.config, "_llm_api_available"):
            api_available = request.config._llm_api_available
            error_msg = request.config._llm_api_error_msg
        else:
            api_available, error_msg = check_llm_api_with_test_call()

        # Only show messages if API is available (tests will run)
        # Skip message is already shown by pytest_collection_modifyitems hook
        if api_available:
            # API is available, tests will run, show warning
            with force_pytest_output(request):
                print("\n" + "=" * 70)
                print(f"⚠️  WARNING: About to run {len(llm_tests)} LLM evaluation tests")
                print(
                    "These tests use AI models and may take 10-30+ minutes when all evals run."
                )
                print()
                print("To see all available evals:")
                print(
                    "  poetry run pytest -m llm --collect-only -q --no-cov --disable-warnings"
                )
                print()
                print("To run just one eval for faster execution:")
                print("  poetry run pytest --no-cov -k 01_how_many_pods")
                print()
                print("Skip all LLM tests with: poetry run pytest -m 'not llm'")
                print()

                # Show ASK_HOLMES_TEST_TYPE if relevant for ask_holmes tests
                ask_holmes_tests = [
                    t for t in llm_tests if "test_ask_holmes" in t.nodeid
                ]
                if ask_holmes_tests:
                    test_type = ASK_HOLMES_TEST_TYPE.lower()
                    print(f"ASK_HOLMES_TEST_TYPE: {test_type} (use 'cli' or 'server')")
                    print()

                # Check if Braintrust is enabled
                if BRAINTRUST_API_KEY:
                    print(
                        f"✓ Braintrust is enabled - traces and results will be available at {get_braintrust_url()}"  # type: ignore[no-untyped-call]
                    )
                else:
                    print(
                        "NOTE: Braintrust is disabled. To see LLM traces and results in Braintrust,"
                    )
                    print(
                        "set BRAINTRUST_API_KEY environment variable with a key from https://braintrust.dev"
                    )
                print("=" * 70 + "\n")

    return


@pytest.fixture(autouse=True)
def braintrust_eval_link(request):
    """Automatically print Braintrust eval link after each LLM test if Braintrust is enabled."""
    yield  # Run the test

    # Only run for LLM tests and if Braintrust is enabled
    if not request.node.get_closest_marker("llm"):
        return

    if not BRAINTRUST_API_KEY:
        return

    # Extract span IDs from user properties
    span_id = None
    root_span_id = None
    if hasattr(request.node, "user_properties"):
        for key, value in request.node.user_properties:
            if key == "braintrust_span_id":
                span_id = value
            elif key == "braintrust_root_span_id":
                root_span_id = value

    # Construct Braintrust URL for this specific test
    braintrust_url = get_braintrust_url(span_id, root_span_id)

    with force_pytest_output(request):
        # Use ANSI escape codes to create a clickable link in terminals that support it
        # Format: \033]8;;URL\033\\TEXT\033]8;;\033\\
        clickable_url = f"\033]8;;{braintrust_url}\033\\{braintrust_url}\033]8;;\033\\"
        print(f"\n🔍 View eval result: \033[94m{clickable_url}\033[0m")
        print()


def show_llm_summary_report(terminalreporter, exitstatus, config):
    """Generate GitHub Actions report and Rich summary table from terminalreporter.stats (xdist compatible)"""
    if not hasattr(terminalreporter, "stats"):
        return

    # When using xdist, only the master process should display the summary
    # Check if we're in a worker process
    worker_id = (
        getattr(config, "workerinput", {}).get("workerid", None)
        if hasattr(config, "workerinput")
        else None
    )
    if worker_id is not None:
        # We're in a worker process, don't display summary
        return

    # Collect and sort test results from terminalreporter.stats
    sorted_results, mock_tracking_data = _collect_test_results_from_stats(
        terminalreporter
    )

    if not sorted_results:
        return

    # Handle GitHub/CI output (markdown + file writing)
    handle_github_output(sorted_results)

    # Handle console/developer output (Rich table + Braintrust links)
    handle_console_output(sorted_results, terminalreporter)

    # Report mock operation statistics
    report_mock_operations(config, mock_tracking_data, terminalreporter)

    # Display single Braintrust experiment link at the very end
    _display_braintrust_experiment_link(terminalreporter)


def _collect_test_results_from_stats(terminalreporter):
    """Collect and parse test results from terminalreporter.stats."""
    test_results = {}
    mock_tracking_data = {
        "generated_mocks": [],
        "cleared_directories": set(),
        "mock_failures": [],
    }

    MOCK_ERROR_TYPES = [
        "MockDataError",
        "MockDataNotFoundError",
        "MockDataCorruptedError",
    ]

    for status, reports in terminalreporter.stats.items():
        for report in reports:
            # For skipped tests, we need to look at 'setup' phase
            when = getattr(report, "when", None)
            if status == "skipped" and when == "setup":
                # Process skipped tests
                nodeid = getattr(report, "nodeid", "")
                if not is_llm_test(nodeid):
                    continue

                # Extract test type
                if "test_ask_holmes" in nodeid:
                    test_type = "ask"
                elif "test_investigate" in nodeid:
                    test_type = "investigate"
                elif "test_workload_health" in nodeid:
                    test_type = "workload_health"
                else:
                    test_type = "unknown"

                # Extract skip reason
                skip_reason = "Skipped"
                if hasattr(report, "longrepr") and report.longrepr:
                    # longrepr for skipped tests is typically a tuple (file, line, reason)
                    if isinstance(report.longrepr, tuple) and len(report.longrepr) >= 3:
                        skip_reason = str(report.longrepr[2])
                    else:
                        skip_reason = str(report.longrepr)

                # Store minimal result for skipped test
                test_results[nodeid] = {
                    "nodeid": nodeid,
                    "test_type": test_type,
                    "expected": "Test skipped",
                    "actual": skip_reason,
                    "tools_called": [],
                    "expected_correctness_score": 0.0,
                    "user_prompt": "",
                    "actual_correctness_score": 0.0,
                    "status": "skipped",
                    "outcome": "skipped",
                    "execution_time": getattr(report, "duration", None),
                    "mock_data_failure": False,
                    "braintrust_span_id": None,
                    "braintrust_root_span_id": None,
                    "clean_test_case_id": None,  # Not available for skipped tests
                }
                continue
            elif when != "call":
                # For other statuses, only process 'call' phase
                continue

            # Only process LLM evaluation tests
            nodeid = getattr(report, "nodeid", "")
            if not is_llm_test(nodeid):
                continue

            # Extract test data from user_properties
            user_props = dict(getattr(report, "user_properties", []))
            if not user_props:  # Skip if no user_properties
                continue

            # Collect mock tracking data
            mock_data_failure = user_props.get("mock_data_failure", False)

            if "generated_mock_file" in user_props:
                mock_tracking_data["generated_mocks"].append(
                    user_props["generated_mock_file"]
                )

            if "mocks_cleared" in user_props:
                folder, count = user_props["mocks_cleared"].split(":", 1)
                mock_tracking_data["cleared_directories"].add(folder)

            if "mock_failure" in user_props:
                mock_tracking_data["mock_failures"].append(user_props["mock_failure"])

            # Check for mock errors if not already found
            if not mock_data_failure:
                # Check in longrepr
                if hasattr(report, "longrepr") and report.longrepr:
                    longrepr_str = str(report.longrepr)
                    mock_data_failure = any(
                        error in longrepr_str for error in MOCK_ERROR_TYPES
                    )

                # Check in captured logs
                if not mock_data_failure and hasattr(report, "sections"):
                    for section_name, section_content in report.sections:
                        if "log" in section_name and any(
                            error in section_content for error in MOCK_ERROR_TYPES
                        ):
                            mock_data_failure = True
                            break

            # Extract test type
            if "test_ask_holmes" in nodeid:
                test_type = "ask"
            elif "test_investigate" in nodeid:
                test_type = "investigate"
            elif "test_workload_health" in nodeid:
                test_type = "workload_health"
            else:
                test_type = "unknown"

            # Handle error cases - if there's an error, show it instead of generic message
            actual_output = user_props.get("actual", "Unknown")
            if actual_output in ["Test not executed", "Unknown"]:
                # Check if we have error information
                error_type = user_props.get("error_type")
                error_message = user_props.get("error_message")
                if error_type and error_message:
                    # Format error for display - keep it concise for table
                    if len(error_message) > 80:
                        # Truncate long error messages but keep the important part
                        actual_output = f"{error_type}: {error_message[:80]}..."
                    else:
                        actual_output = f"{error_type}: {error_message}"
                elif error_type:
                    actual_output = f"Error: {error_type}"

            # Store result (use nodeid as key to avoid duplicates)
            test_results[nodeid] = {
                "nodeid": nodeid,
                "test_type": test_type,
                "expected": user_props.get("expected", "Unknown"),
                "actual": actual_output,
                "tools_called": user_props.get("tools_called", []),
                "expected_correctness_score": float(
                    user_props.get("expected_correctness_score", 1.0)
                ),
                "actual_correctness_score": float(
                    user_props.get("actual_correctness_score", 0.0)
                ),
                "status": status,
                "outcome": getattr(report, "outcome", "unknown"),
                "execution_time": getattr(report, "duration", None),
                "mock_data_failure": mock_data_failure,
                "user_prompt": user_props.get("user_prompt", ""),
                "is_setup_failure": user_props.get("is_setup_failure", False),
                # Throttling flags
                "failed_due_to_throttling": user_props.get(
                    "failed_due_to_throttling", False
                ),  # Terminal failure after max retries
                "encountered_throttling": user_props.get(
                    "encountered_throttling", False
                ),  # Any throttling during execution
                "model": user_props.get("model", "Unknown"),
                "clean_test_case_id": user_props.get("clean_test_case_id"),
                "braintrust_span_id": user_props.get("braintrust_span_id"),
                "braintrust_root_span_id": user_props.get("braintrust_root_span_id"),
                # Cost tracking
                "cost": user_props.get("cost", 0.0),
                "total_tokens": user_props.get("total_tokens", 0),
                "prompt_tokens": user_props.get("prompt_tokens", 0),
                "completion_tokens": user_props.get("completion_tokens", 0),
                # Tag tracking for performance analysis
                "tags": user_props.get("tags", []),
                # Error tracking for better reporting
                "error_type": user_props.get("error_type"),
                "error_message": user_props.get(
                    "error_message",
                    str(report.longrepr)
                    if hasattr(report, "longrepr") and report.longrepr
                    else None,
                ),
            }

    # Extract test case names for all results
    results_with_ids = []
    for result in test_results.values():
        # If we have a clean test case ID from the test, use it
        # This is set in test_ask_holmes.py, test_investigate.py, and test_workload_health.py
        # via: request.node.user_properties.append(("clean_test_case_id", test_case.id))
        # It provides the clean test case ID without model suffixes that pytest adds when
        # parameterizing with multiple models (e.g., "01_how_many_pods" instead of
        # "01_how_many_pods-gpt-4o" or "01_how_many_pods-anthropic/claude-3-5-sonnet")
        # Note: This won't be available for skipped tests (they never enter the test function body)
        # or tests that fail during early setup before user_properties are set
        if result.get("clean_test_case_id"):
            result["test_case_name"] = result["clean_test_case_id"]
        else:
            # Fallback: Create a temporary TestResult to extract test case name from nodeid
            temp_result = TestResult(
                nodeid=result["nodeid"],
                expected=result["expected"],
                actual=result["actual"],
                pass_fail="",  # Will be set later
                tools_called=result["tools_called"],
                logs="",  # Will be set later
                test_type=result["test_type"],
                execution_time=result["execution_time"],
                expected_correctness_score=result["expected_correctness_score"],
                user_prompt=result["user_prompt"],
                actual_correctness_score=result["actual_correctness_score"],
                mock_data_failure=result["mock_data_failure"],
            )
            # Add extracted test case name to the result dict
            result["test_case_name"] = temp_result.test_case_name

        results_with_ids.append(result)

    # Sort results by test_type then test_case_name for consistent ordering
    sorted_results = sorted(
        results_with_ids,
        key=lambda r: (
            r["test_type"],
            r["test_case_name"],
        ),
    )

    return sorted_results, mock_tracking_data


def _display_braintrust_experiment_link(terminalreporter):
    """Display a single Braintrust experiment link at the end of test output."""
    # Check if Braintrust is enabled
    if not BRAINTRUST_API_KEY:
        return

    # Build experiment URL
    experiment_url = get_braintrust_url()

    print("\n" + "=" * 70)
    print("🧠 Braintrust Experiment Summary")
    print("=" * 70)
    # Make it clickable in terminals that support it
    clickable_url = f"\033]8;;{experiment_url}\033\\{experiment_url}\033]8;;\033\\"
    print(f"View full experiment results: \033[94m{clickable_url}\033[0m")
    print("=" * 70 + "\n")

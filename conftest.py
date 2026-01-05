import logging
import os
import re
from unittest.mock import MagicMock, patch

import pytest
import responses as responses_

from holmes.core.tracing import get_active_branch_name, readable_timestamp
from tests.llm.conftest import show_llm_summary_report
from tests.llm.utils.braintrust import get_braintrust_url


def pytest_addoption(parser):
    """Add custom pytest command line options"""
    parser.addoption(
        "--generate-mocks",
        action="store_true",
        default=False,
        help="Generate mock data files during test execution instead of using existing mocks",
    )
    parser.addoption(
        "--regenerate-all-mocks",
        action="store_true",
        default=False,
        help="Regenerate all mock data files, replacing existing ones (implies --generate-mocks)",
    )
    parser.addoption(
        "--skip-setup",
        action="store_true",
        default=False,
        help="Skip running before_test commands for test cases (useful for iterative test development)",
    )
    parser.addoption(
        "--skip-cleanup",
        action="store_true",
        default=False,
        help="Skip running after_test commands for test cases (useful for debugging test failures)",
    )
    parser.addoption(
        "--only-setup",
        action="store_true",
        default=False,
        help="Only run before_test setup commands, skip the actual test execution",
    )
    parser.addoption(
        "--only-cleanup",
        action="store_true",
        default=False,
        help="Only run after_test cleanup commands, skip the actual test execution",
    )
    parser.addoption(
        "--retry-on-throttle",
        action="store_true",
        default=True,
        help="Retry tests when encountering API throttling/overload errors (default: True)",
    )
    parser.addoption(
        "--no-retry-on-throttle",
        action="store_false",
        dest="retry_on_throttle",
        help="Disable retrying tests on API throttling/overload errors",
    )
    parser.addoption(
        "--include-intermediate",
        action="store_true",
        default=True,
        help="Include intermediate LLM outputs when evaluating correctness (default: True)",
    )
    parser.addoption(
        "--no-include-intermediate",
        action="store_false",
        dest="include_intermediate",
        help="Exclude intermediate LLM outputs from evaluation (only use final answer)",
    )
    parser.addoption(
        "--strict-setup-mode",
        type=str,
        default="false",
        choices=["true", "false"],
        help="Fail entire pytest run if any test setup fails (true/false, default: false) - useful for benchmarks to ensure all tests run properly",
    )
    parser.addoption(
        "--strict-setup-exceptions",
        type=str,
        default="",
        help="Comma-separated list of test IDs that are allowed to have setup failures even with --strict-setup-mode",
    )


def pytest_configure(config):
    """Configure pytest settings"""
    # Configure worker-specific log files for xdist compatibility
    # worker_id = getattr(config, "workerinput", {}).get("workerid", "master")
    # if worker_id != "master":
    #    # Set worker-specific log file to avoid conflicts
    #    config.option.log_file = f"tests-{worker_id}.log"

    # Determine worker id
    # Also see: https://pytest-xdist.readthedocs.io/en/latest/how-to.html#creating-one-log-file-for-each-worker
    # worker_id = os.environ.get("PYTEST_XDIST_WORKER", default="gw0")

    # # Create logs folder
    # logs_folder = os.environ.get("LOGS_FOLDER", default="logs_folder")
    # os.makedirs(logs_folder, exist_ok=True)

    # # Create file handler to output logs into corresponding worker file
    # file_handler = logging.FileHandler(f"{logs_folder}/logs_worker_{worker_id}.log", mode="w")
    # file_handler.setFormatter(
    #     logging.Formatter(
    #         fmt="{asctime} {levelname}:{name}:{lineno}:{message}",
    #         style="{",
    #     )
    # )
    # # Create stream handler to output logs on console
    # # This is a workaround for a known limitation:
    # # https://pytest-xdist.readthedocs.io/en/latest/known-limitations.html
    # console_handler = logging.StreamHandler(sys.stderr)  # pytest only prints error logs
    # console_handler.setFormatter(
    #     logging.Formatter(
    #         # Include worker id in log messages, \r is needed to separate lines in console
    #         fmt="\r{asctime} " + worker_id + ":{levelname}:{name}:{lineno}:{message}",
    #         style="{",
    #     )
    # )
    # # Configure logging
    # logging.basicConfig(level=logging.INFO, force=True, handlers=[console_handler, file_handler])

    # Suppress noisy LiteLLM logs during testing
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    # Also suppress the verbose logger used by LiteLLM
    logging.getLogger("LiteLLM.verbose_logger").setLevel(logging.ERROR)
    # Suppress litellm sub-loggers
    logging.getLogger("litellm").setLevel(logging.ERROR)
    logging.getLogger("litellm.cost_calculator").setLevel(logging.ERROR)
    logging.getLogger("litellm.litellm_core_utils").setLevel(logging.ERROR)
    logging.getLogger("litellm.litellm_core_utils.litellm_logging").setLevel(
        logging.ERROR
    )
    # Suppress httpx HTTP request logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if not os.getenv("EXPERIMENT_ID"):
        try:
            username = os.getlogin()
        except OSError:
            # os.getlogin() fails in environments without a terminal (e.g., GitHub Actions)
            username = os.getenv("USER", "ci")
        git_branch = get_active_branch_name()

        # Include pytest parameters in experiment ID
        params = []
        if config.option.keyword:
            params.append(f"k={config.option.keyword}")
        if config.option.markexpr:
            params.append(f"m={config.option.markexpr}")

        params_str = f"-{'-'.join(params)}" if params else ""
        os.environ["EXPERIMENT_ID"] = (
            f"{username}-{git_branch}{params_str}-{readable_timestamp()}"
        )


def pytest_report_header(config):
    braintrust_api_key = os.environ.get("BRAINTRUST_API_KEY")
    if not braintrust_api_key:
        return ""

    experiment_url = get_braintrust_url()
    clickable_url = f"\033]8;;{experiment_url}\033\\{experiment_url}\033]8;;\033\\"
    return f"Eval results: (link valid once setup completes): {clickable_url}"


# due to pytest quirks, we need to define this in the main conftest.py - when defined in the llm conftest.py it
# is SOMETIMES picked up and sometimes not, depending on how the test was invokedr
pytest_terminal_summary = show_llm_summary_report


@pytest.fixture(autouse=True)
def patch_supabase(monkeypatch):
    monkeypatch.setattr("holmes.core.supabase_dal.ROBUSTA_ACCOUNT_ID", "test-cluster")
    monkeypatch.setattr(
        "holmes.core.supabase_dal.STORE_URL", "https://fakesupabaseref.supabase.co"
    )
    monkeypatch.setattr(
        "holmes.core.supabase_dal.STORE_API_KEY",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlhdCI6MTYzNTAwODQ4NywiZXhwIjoxOTUwNTg0NDg3fQ.l8IgkO7TQokGSc9OJoobXIVXsOXkilXl4Ak6SCX5qI8",
    )
    monkeypatch.setattr("holmes.core.supabase_dal.STORE_EMAIL", "mock_store_user")
    monkeypatch.setattr(
        "holmes.core.supabase_dal.STORE_PASSWORD", "mock_store_password"
    )


@pytest.fixture(autouse=True, scope="session")
def storage_dal_mock():
    with patch("holmes.config.SupabaseDal") as MockSupabaseDal:
        mock_supabase_dal_instance = MagicMock()
        MockSupabaseDal.return_value = mock_supabase_dal_instance
        mock_supabase_dal_instance.sign_in.return_value = "mock_supabase_user_id"
        mock_supabase_dal_instance.get_ai_credentials.return_value = (
            "mock_account_id",
            "mock_session_token",
        )
        yield mock_supabase_dal_instance


@pytest.fixture(autouse=True)
def responses():
    with responses_.RequestsMock() as rsps:
        rsps.add_passthru("https://www.braintrust.dev")
        rsps.add_passthru("https://api.braintrust.dev")  # Allow Braintrust API calls
        rsps.add_passthru(
            "https://api.newrelic.com/graphql"
        )  # Allow New Relic API calls
        rsps.add_passthru(
            "https://api.eu.newrelic.com/graphql"
        )  # Allow New Relic API calls
        rsps.add_passthru("http://localhost")

        # Allow all Datadog API calls to pass through (all regions and endpoints)
        rsps.add_passthru("https://api.datadoghq.com")
        rsps.add_passthru("https://api.datadoghq.eu")
        rsps.add_passthru("https://api.ddog-gov.com")
        rsps.add_passthru("https://api.us3.datadoghq.com")
        rsps.add_passthru("https://api.us5.datadoghq.com")
        rsps.add_passthru("https://api.ap1.datadoghq.com")
        rsps.add_passthru("https://app.datadoghq.com")
        rsps.add_passthru("https://app.datadoghq.eu")
        rsps.add_passthru("https://ng-api-http.eu2.coralogix.com")

        # Allow Elasticsearch/OpenSearch Cloud API calls (various hosting regions)
        rsps.add_passthru(re.compile(r"https://.*\.cloud\.es\.io"))  # Elastic Cloud
        rsps.add_passthru(re.compile(r"https://.*\.elastic-cloud\.com"))  # Azure-hosted
        rsps.add_passthru(re.compile(r"https://.*\.es\.amazonaws\.com"))  # AWS OpenSearch

        # Allow
        rsps.add_passthru("https://google.com")
        rsps.add_passthru("https://burgergooglenetworkspam.co.uk")
        rsps.add_passthru("https://www.google.com")
        rsps.add_passthru("https://www.burgergooglenetworkspam.co.uk")
        yield rsps

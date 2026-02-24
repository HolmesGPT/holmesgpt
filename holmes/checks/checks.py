"""Health check functionality for Holmes."""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests  # type:ignore
import yaml
from jinja2 import Template
from rich.console import Console

from holmes.checks.models import (
    Check,
    CheckMode,
    CheckResponse,
    CheckResult,
    ChecksConfig,
    CheckStatus,
    DestinationConfig,
)
from holmes.config import Config
from holmes.core.issue import Issue, IssueStatus
from holmes.core.tool_calling_llm import LLMResult, ToolCallingLLM
from holmes.plugins.destinations.pagerduty.plugin import PagerDutyDestination
from holmes.plugins.destinations.slack.plugin import SlackDestination

CHECK_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "check_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "rationale": {
                    "type": "string",
                    "description": "First, explain what you found and your reasoning",
                },
                "passed": {
                    "type": "boolean",
                    "description": "Based on your rationale above, does the check pass (true) or fail (false)?",
                },
            },
            "required": ["rationale", "passed"],
            "additionalProperties": False,
        },
    },
}
CHECK_PROMPT_TEMPLATE_PATH = Path(__file__).parent / "check_system_prompt.jinja2"

CHECK_STATUS_COLOR = {
    CheckStatus.PASS: "green",
    CheckStatus.FAIL: "red",
    CheckStatus.ERROR: "yellow",
}


def _get_check_prompt() -> str:
    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    template = Template(CHECK_PROMPT_TEMPLATE_PATH.read_text())
    return template.render(current_time=current_time)


def _execute_ai_check(check: Check, ai: ToolCallingLLM) -> LLMResult:
    system_message = _get_check_prompt()
    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": check.query},
    ]
    response: LLMResult = ai.call(messages, response_format=CHECK_RESPONSE_FORMAT)
    return response


def _parse_check_response(response: LLMResult) -> CheckResponse:
    try:
        result_json = json.loads(response.result or "{}")
        return CheckResponse(**result_json)
    except (json.JSONDecodeError, Exception) as parse_error:
        return CheckResponse(
            passed=False, rationale=f"Failed to parse response: {str(parse_error)}"
        )


def execute_check(
    check: Check,
    ai: ToolCallingLLM,
    verbose: bool = False,
    console: Optional[Console] = None,
) -> CheckResult:
    """
    Execute a single health check.

    This is the core check execution logic that can be reused by both
    the CLI runner and the API endpoint.

    Args:
        check: The check configuration
        ai: The LLM instance to use for evaluation
        verbose: Whether to print verbose output
        console: Optional console for output (only used if verbose=True)

    Returns:
        CheckResult with status, message, and metadata
    """
    if verbose and console:
        console.print(f"\n[cyan]Running check: {check.name}[/cyan]")
        console.print(f"  [bold]Query:[/bold] {check.query}")
        if check.description:
            console.print(f"  [dim]Description:[/dim] {check.description}")

    start_time = time.time()
    try:
        response = _execute_ai_check(check, ai)
        check_response = _parse_check_response(response)
        if verbose and console:
            status_str = "PASS" if check_response.passed else "FAIL"
            console.print(f"    Result: {status_str}")
            console.print(f"    Rationale: {check_response.rationale}")

        duration = time.time() - start_time

        if check_response.passed:
            result = CheckResult(
                check_name=check.name,
                status=CheckStatus.PASS,
                message=f"Check passed. {check_response.rationale}",
                query=check.query,
                duration=duration,
                rationale=check_response.rationale,
            )
        else:
            result = CheckResult(
                check_name=check.name,
                status=CheckStatus.FAIL,
                message=f"Check failed. {check_response.rationale}",
                query=check.query,
                duration=duration,
                rationale=check_response.rationale,
            )

    except Exception as e:
        duration = time.time() - start_time
        result = CheckResult(
            check_name=check.name,
            status=CheckStatus.ERROR,
            message=f"Check errored: {str(e)}",
            query=check.query,
            duration=duration,
            error=str(e),
        )

    if console:
        status_color = CHECK_STATUS_COLOR[result.status]
        console.print(
            f"    Result: [{status_color}]{result.status.value.upper()}[/{status_color}] - {result.message}"
        )
        console.print(f"    Rationale: {result.rationale}")

    return result


class CheckRunner:
    """Runs health checks using Holmes ask functionality."""

    def __init__(
        self,
        config: Config,
        console: Console,
        ai: ToolCallingLLM,
        verbose: bool = False,
        parallel: bool = False,
        destinations_config: Optional[Dict[str, DestinationConfig]] = None,
    ):
        self.config = config
        self.console = console
        self.verbose = verbose
        self.parallel = parallel
        self.ai: ToolCallingLLM = ai
        self._destinations_config: Dict[str, DestinationConfig] = {}
        if destinations_config:
            errors = self.validate_destinations(destinations_config)
            if errors:
                self.console.print(
                    "[bold red]Destination configuration errors:[/bold red]"
                )
                for error in errors:
                    self.console.print(f"  • {error}")
                self.console.print(
                    "\n[yellow]Fix these errors or use --mode monitor to skip alerts[/yellow]"
                )
            else:
                self._destinations_config = destinations_config

    # TODO: refactor this, why we suppose the name will be slack or pagerduty?
    def validate_destinations(
        self, destinations: Dict[str, DestinationConfig]
    ) -> List[str]:
        """Validate all configured destinations upfront."""
        errors = []

        for name, dest_config in destinations.items():
            if name == "slack":
                # If webhook URL is provided, no need to check for token/channel
                if dest_config.webhook_url:
                    continue

                # Check Slack configuration for token-based approach
                slack_token = self.config.slack_token
                slack_channel = self.config.slack_channel

                # Check for proper token format
                if slack_token:
                    try:
                        # Ensure it's a string and not SecretStr
                        token_str = slack_token.get_secret_value()
                        if not token_str or not token_str.strip():
                            errors.append(f"Slack destination '{name}': Token is empty")
                    except Exception as e:
                        errors.append(
                            f"Slack destination '{name}': Invalid token format - {e}"
                        )

                if not slack_channel:
                    errors.append(
                        f"Slack destination '{name}': Missing slack_channel in config"
                    )

            elif name == "pagerduty":
                # Check PagerDuty configuration
                if not dest_config.integration_key:
                    errors.append(
                        f"PagerDuty destination '{name}': Missing integration_key in destination config"
                    )

            else:
                # Unknown destination type
                errors.append(f"Unknown destination type: {name}")

        return errors

    def run_single_check(self, check: Check) -> CheckResult:
        """Run a single check."""
        return execute_check(
            check=check,
            ai=self.ai,
            verbose=self.verbose,
            console=self.console,
        )

    def _filter_checks(
        self,
        checks: List[Check],
        name_filter: Optional[str] = None,
        tag_filter: Optional[List[str]] = None,
    ) -> List[Check]:
        """Filter checks based on name and tag filters."""
        filtered_checks = checks
        if name_filter:
            filtered_checks = [c for c in filtered_checks if c.name == name_filter]
        if tag_filter:
            filtered_checks = [
                c for c in filtered_checks if any(tag in c.tags for tag in tag_filter)
            ]
        return filtered_checks

    def _validate_alert_destinations(self, filtered_checks):
        """Warn if any alert-mode checks have no destinations configured."""
        alert_checks_with_no_destinations = [
            c.name
            for c in filtered_checks
            if c.mode == CheckMode.ALERT
            and (
                not c.destinations or (not self._destinations_config and c.destinations)
            )
        ]
        if alert_checks_with_no_destinations:
            self.console.print(
                "[yellow]⚠️  Warning: Alert mode is enabled but the following checks have no destinations configured:[/yellow]"
            )
            for check_name in alert_checks_with_no_destinations:
                self.console.print(f"    • {check_name}")
            self.console.print(
                "[yellow]    No alerts will be sent for failed checks.[/yellow]"
            )
            self.console.print(
                "[yellow]    To fix: Use --mode monitor or configure destinations:[/yellow]"
            )
            self.console.print(
                "[yellow]    • For inline checks: --slack-webhook URL (standalone, no token needed)[/yellow]"
            )
            self.console.print(
                "[yellow]    • Or: --slack-channel #channel (requires SLACK_TOKEN env var)[/yellow]"
            )
            self.console.print(
                "[yellow]    • For YAML config: Add 'destinations' section with slack/pagerduty config[/yellow]\n"
            )

    def run_checks(
        self,
        checks: List[Check],
        name_filter: Optional[str] = None,
        tag_filter: Optional[List[str]] = None,
    ) -> List[CheckResult]:
        """Run multiple checks with optional filtering."""

        filtered_checks = self._filter_checks(checks, name_filter, tag_filter)
        if not filtered_checks:
            self.console.print("[yellow]No checks match the specified filters[/yellow]")
            return []

        self._validate_alert_destinations(filtered_checks)

        self.console.print(
            f"[bold]Running {len(filtered_checks)} checks{' in parallel' if self.parallel else ''}...[/bold]"
        )

        if self.parallel:
            # Run checks in parallel using threads
            results = []
            with ThreadPoolExecutor(max_workers=10) as executor:
                future_to_check = {
                    executor.submit(self.run_single_check, check): check
                    for check in filtered_checks
                }
                # Process results as they complete
                for future in as_completed(future_to_check):
                    check = future_to_check[future]
                    try:
                        result = future.result()
                        results.append(result)

                        # Send alerts if needed
                        if (
                            result.status == CheckStatus.FAIL
                            and check.mode == CheckMode.ALERT
                            and check.destinations
                        ):
                            self._send_alerts(check, result)

                    except Exception as e:
                        self.console.print(
                            f"[red]Error running check {check.name}: {e}[/red]"
                        )
                        results.append(
                            CheckResult(
                                check_name=check.name,
                                status=CheckStatus.ERROR,
                                message=f"Check errored: {str(e)}",
                                query=check.query,
                                duration=0,
                                error=str(e),
                            )
                        )
        else:
            # Run checks sequentially
            results = []
            for check in filtered_checks:
                result = self.run_single_check(check)
                results.append(result)

                # Send alerts if needed
                if (
                    result.status == CheckStatus.FAIL
                    and check.mode == CheckMode.ALERT
                    and check.destinations
                ):
                    self._send_alerts(check, result)

        return results

    def _send_alerts(self, check: Check, result: CheckResult):
        """Send alerts to configured destinations."""
        issue_status = (
            IssueStatus.OPEN
            if result.status == CheckStatus.FAIL
            else IssueStatus.CLOSED
        )
        issue = Issue(
            id=f"check-{check.name}",
            name=f"Health Check: {check.name}",
            source_type="holmes-check",
            presentation_status=issue_status,
            show_status_in_title=False,  # Don't append "- open/closed" for health checks
            raw={
                "check": check.name,
                "description": check.description,
                "query": check.query,
                "result": result.message,
                "tags": check.tags,
                "status": result.status.value,
            },
            source_instance_id="holmes-check",
        )

        # Create a mock LLM result
        llm_result = LLMResult(
            result=result.message,
            tool_calls=[],
        )

        for dest_name in check.destinations:
            if dest_name == "slack":
                # Check if we have a webhook URL in destinations config
                slack_dest_config: Optional[DestinationConfig] = (
                    self._destinations_config.get(dest_name)
                )
                if not slack_dest_config:
                    self.console.print(
                        "  [yellow]Slack not configured (missing webhook_url)[/yellow]"
                    )
                    continue

                webhook_url = slack_dest_config.webhook_url
                if webhook_url:
                    # Use webhook URL for posting
                    try:
                        webhook_payload = self._format_slack_webhook_payload(
                            check, result
                        )
                        response = requests.post(
                            webhook_url, json=webhook_payload, timeout=10
                        )
                        response.raise_for_status()

                        self.console.print(
                            "  [green]Alert sent to Slack via webhook[/green]"
                        )
                    except Exception as e:
                        self.console.print(
                            f"  [red]Failed to send Slack webhook alert: {str(e)}[/red]"
                        )
                    continue

                # Fall back to token-based approach
                slack_token = self.config.slack_token
                slack_channel = (
                    slack_dest_config.channel
                    if slack_dest_config and slack_dest_config.channel
                    else self.config.slack_channel
                )

                if not slack_token or not slack_channel:
                    if self.verbose:
                        self.console.print(
                            "  [yellow]Slack not configured (missing token or channel)[/yellow]"
                        )
                    continue

                try:
                    token_str: str = (
                        slack_token.get_secret_value() if slack_token else ""
                    )

                    slack = SlackDestination(token_str, slack_channel)
                    slack.send_issue(issue, llm_result)

                    self.console.print(
                        f"  [green]Alert sent to Slack channel {slack_channel}[/green]"
                    )
                except Exception as e:
                    self.console.print(
                        f"  [red]Failed to send Slack alert: {str(e)}[/red]"
                    )

            elif dest_name == "pagerduty":
                pagerduty_config: Optional[DestinationConfig] = (
                    self._destinations_config.get(dest_name)
                )
                if not pagerduty_config:
                    self.console.print(
                        "[yellow]PagerDuty not configured (missing integration_key)[/yellow]"
                    )
                    continue

                if not pagerduty_config or not pagerduty_config.integration_key:
                    if self.verbose:
                        self.console.print(
                            "  [yellow]PagerDuty not configured (missing integration_key)[/yellow]"
                        )
                    continue

                try:
                    pagerduty = PagerDutyDestination(pagerduty_config.integration_key)
                    pagerduty.send_issue(issue, llm_result)

                    if self.verbose:
                        self.console.print("  [green]Alert sent to PagerDuty[/green]")
                except Exception as e:
                    self.console.print(
                        f"  [red]Failed to send PagerDuty alert: {str(e)}[/red]"
                    )

            else:
                if self.verbose:
                    self.console.print(
                        f"  [yellow]Destination '{dest_name}' not yet implemented[/yellow]"
                    )

    def _format_slack_webhook_payload(self, check: Check, result: CheckResult) -> dict:
        """Format a consistent Slack message payload for webhook delivery."""
        # Determine color based on status
        color_map = {
            CheckStatus.PASS: "good",  # green
            CheckStatus.FAIL: "danger",  # red
            CheckStatus.ERROR: "warning",  # yellow
        }
        color = color_map.get(result.status, "danger")

        # Build fields
        fields = [
            {
                "title": "Query",
                "value": check.query,
                "short": False,
            }
        ]

        if check.description:
            fields.append(
                {
                    "title": "Description",
                    "value": check.description,
                    "short": False,
                }
            )

        if check.tags:
            fields.append(
                {
                    "title": "Tags",
                    "value": ", ".join(check.tags),
                    "short": True,
                }
            )

        # Build payload
        return {
            "text": f"Holmes Health Check: {check.name}",
            "attachments": [
                {
                    "color": color,
                    "title": check.name,
                    "text": result.message,
                    "fields": fields,
                    "footer": f"Holmes • {result.status.value.upper()}",
                    "ts": int(time.time()),
                }
            ],
        }


def load_checks_config(file_path: Path) -> ChecksConfig:
    """Load checks configuration from YAML file."""
    with open(file_path, "r") as f:
        data = yaml.safe_load(f)

    defaults = data.get("defaults", {})
    # Remove 'mode' from defaults if present - it should only be set via CLI or per-check
    defaults.pop("mode", None)

    checks = []
    for check_data in data.get("checks", []):
        # Apply defaults
        for key, value in defaults.items():
            if key not in check_data:
                check_data[key] = value

        checks.append(Check(**check_data))

    return ChecksConfig(
        version=data.get("version", 1),
        defaults=defaults,
        destinations=data.get("destinations", {}),
        checks=checks,
    )

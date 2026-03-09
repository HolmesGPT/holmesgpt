import json
import time
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from holmes.checks import (
    Check,
    CheckMode,
    CheckResult,
    CheckRunner,
    ChecksConfig,
    CheckStatus,
    DestinationConfig,
    load_checks_config,
)
from holmes.common.cli_commons import (
    opt_api_key,
    opt_config_file,
    opt_model,
    opt_verbose,
)
from holmes.config import Config
from holmes.utils.console.logging import init_logging

checks_app = typer.Typer(help="Health checks commands")

CHECK_STATUS_COLORS = {
    CheckStatus.PASS: "green",
    CheckStatus.FAIL: "red",
    CheckStatus.ERROR: "yellow",
}


@checks_app.command("run")
def check(
    checks_file: Optional[Path] = typer.Option(
        None,
        "--checks-file",
        help="Path to checks configuration file (defaults to ~/.holmes/checks.yaml)",
    ),
    inline_check: Optional[str] = typer.Option(
        None,
        "-c",
        "--check",
        help="Run a single inline check without a configuration file",
    ),
    slack_channel: Optional[str] = typer.Option(
        None,
        "--slack-channel",
        help="Slack channel for inline check alerts (e.g., #alerts). Requires SLACK_TOKEN environment variable or slack_token in config file. Cannot be used with --slack-webhook.",
    ),
    slack_webhook: Optional[str] = typer.Option(
        None,
        "--slack-webhook",
        help="Slack webhook URL for inline check alerts. Standalone option that doesn't require a bot token. Cannot be used with --slack-channel.",
    ),
    slack_token: Optional[str] = typer.Option(
        None,
        "--slack-token",
        help="Slack bot token for sending alerts. Can also be set via SLACK_TOKEN environment variable.",
        envvar="SLACK_TOKEN",
    ),
    mode: CheckMode = typer.Option(
        CheckMode.MONITOR,
        "--mode",
        help="Mode for running checks: 'alert' (send notifications) or 'monitor' (only log)",
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        help="Run specific check by name",
    ),
    tags: Optional[List[str]] = typer.Option(
        None,
        "--tags",
        help="Filter checks by tags (can specify multiple)",
    ),
    output: str = typer.Option(
        "table",
        "--output",
        help="Output format: 'table' or 'json'",
    ),
    watch: bool = typer.Option(
        False,
        "--watch",
        help="Run checks continuously",
    ),
    interval: int = typer.Option(
        60,
        "--interval",
        help="Interval in seconds for watch mode",
    ),
    parallel: bool = typer.Option(
        False,
        "--parallel",
        help="Run checks in parallel (faster but output may be interleaved)",
    ),
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    config_file: Optional[Path] = opt_config_file,
    verbose: Optional[List[bool]] = opt_verbose,
):
    """
    Run health checks and optionally send alerts
    """
    console = init_logging(verbose)

    # Validate mutually exclusive Slack options
    if slack_channel and slack_webhook:
        console.print(
            "[red]Error: --slack-channel and --slack-webhook are mutually exclusive.[/red]\n"
            "Use --slack-webhook for webhook-based alerts (no token required) OR\n"
            "Use --slack-channel with SLACK_TOKEN environment variable for bot-based alerts."
        )
        raise typer.Exit(1)

    if inline_check:
        checks_config = ChecksConfig(
            version=1,
            checks=[
                Check(name="Inline Check", query=inline_check),
            ],
        )
        if slack_webhook or slack_channel:
            # Use configured Slack token from config or webhook
            slack_config: dict = {}
            if slack_webhook:
                slack_config["webhook_url"] = slack_webhook
            if slack_channel:
                slack_config["channel"] = slack_channel
            checks_config.destinations = {"slack": DestinationConfig(**slack_config)}

            # Add destination to the check
            checks_config.checks[0].destinations = ["slack"]

    else:
        # Determine checks file location
        if checks_file is None:
            default_checks = Path.home() / ".holmes" / "checks.yaml"
            if default_checks.exists():
                checks_file = default_checks
            else:
                console.print(
                    "[red]No checks file specified and ~/.holmes/checks.yaml not found.[/red]\n"
                    "Please specify a checks file with --checks-file or use -c for inline checks"
                )
                raise typer.Exit(1)
        # Load checks configuration
        try:
            checks_config = load_checks_config(checks_file)
        except FileNotFoundError:
            console.print(f"[red]Checks file not found: {checks_file}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Error loading checks file: {e}[/red]")
            raise typer.Exit(1) from e

    # Load config
    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        slack_token=slack_token,
        slack_channel=slack_channel,
    )

    for check in checks_config.checks:
        check.mode = mode

    # Run checks
    exit_code = run_check_command(
        checks_config=checks_config,
        config=config,
        console=console,
        name_filter=name,
        tag_filter=tags,
        verbose=len(verbose) > 0 if verbose else False,
        output_format=output,
        watch=watch,
        watch_interval=interval,
        parallel=parallel,
    )
    raise typer.Exit(exit_code)


def run_check_command(
    checks_config: ChecksConfig,
    config: Config,
    console: Console,
    name_filter: Optional[str] = None,
    tag_filter: Optional[List[str]] = None,
    verbose: bool = False,
    output_format: str = "table",
    watch: bool = False,
    watch_interval: int = 60,
    parallel: bool = False,
):
    """Main entry point for check command."""

    # Create runner
    llm = config.create_console_toolcalling_llm()
    runner = CheckRunner(
        config,
        console,
        llm,
        verbose,
        parallel,
        destinations_config=checks_config.destinations,
    )

    # Run checks (with watch support)
    while True:
        console.print("\n[bold]Holmes Health Checks[/bold]")
        console.print()

        results = runner.run_checks(
            checks_config.checks,
            name_filter=name_filter,
            tag_filter=tag_filter,
        )

        if results:
            console.print()
            display_results_table(console, results, output_format)

            # Calculate exit code
            has_failures = any(r.status != CheckStatus.PASS for r in results)
            exit_code = 1 if has_failures else 0
        else:
            exit_code = 0

        if not watch:
            return exit_code

        console.print(
            f"\n[dim]Waiting {watch_interval} seconds before next run...[/dim]"
        )
        time.sleep(watch_interval)


def display_results_table(
    console: Console, results: List[CheckResult], output_format: str = "table"
):
    """Display check results in a table or JSON format."""
    if output_format == "json":
        output = []
        for result in results:
            output.append(
                {
                    "name": result.check_name,
                    "status": result.status.value,
                    "message": result.message,
                    "duration": result.duration,
                    "error": result.error,
                }
            )
        console.print(json.dumps(output, indent=2))
    else:
        table = Table(title="Check Results")
        table.add_column("Check Name", style="cyan")
        table.add_column("Status", style="bold")
        table.add_column("Message", max_width=80)
        table.add_column("Duration", style="dim")

        for result in results:
            status_color = CHECK_STATUS_COLORS[result.status]
            table.add_row(
                result.check_name,
                f"[{status_color}]{result.status.value.upper()}[/{status_color}]",
                f"[{status_color}]{result.message}[/{status_color}]",
                f"{result.duration:.2f}s",
            )
        console.print(table)

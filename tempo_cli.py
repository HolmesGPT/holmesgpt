#!/usr/bin/env python3
"""
Grafana Tempo CLI - Command line utility for interacting with Grafana Tempo API

Usage:
    tempo-cli [OPTIONS] COMMAND [ARGS]...

Environment Variables:
    TEMPO_URL: Base URL for Tempo instance
    TEMPO_API_KEY: API key for authentication
    GRAFANA_DATASOURCE_UID: Optional Grafana datasource UID for proxying
"""

import json
import os
from typing import Dict, Optional

import typer
from rich.console import Console
from rich.json import JSON

from holmes.plugins.toolsets.grafana.common import GrafanaTempoConfig
from holmes.plugins.toolsets.grafana.grafana_tempo_api import (
    GrafanaTempoAPI,
    TempoAPIError,
)

app = typer.Typer(help="Grafana Tempo CLI - Query traces and metrics")
console = Console()


def get_api_client(
    url: Optional[str] = None,
    api_key: Optional[str] = None,
    grafana_datasource_uid: Optional[str] = None,
) -> GrafanaTempoAPI:
    """Create and return a GrafanaTempoAPI client."""
    # Get from environment if not provided
    url = url or os.environ.get("TEMPO_URL")
    api_key = api_key or os.environ.get("TEMPO_API_KEY")
    grafana_datasource_uid = grafana_datasource_uid or os.environ.get(
        "GRAFANA_DATASOURCE_UID"
    )

    if not url:
        console.print(
            "[red]Error: URL is required. Set TEMPO_URL environment variable or use --url flag[/red]"
        )
        raise typer.Exit(1)

    config = GrafanaTempoConfig(
        url=url, api_key=api_key, grafana_datasource_uid=grafana_datasource_uid
    )

    return GrafanaTempoAPI(config)


def print_result(result: Dict, pretty: bool = True):
    """Print API result in JSON format."""
    if pretty:
        console.print(JSON.from_data(result))
    else:
        print(json.dumps(result))


def handle_api_error(e: Exception):
    """Handle API errors with detailed messages."""
    if isinstance(e, TempoAPIError):
        console.print(f"[red]API Error {e.status_code}: {e}[/red]")
        console.print(f"[dim]URL: {e.url}[/dim]")
    else:
        console.print(f"[red]Error: {e}[/red]")
    raise typer.Exit(1)


@app.command()
def echo(
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Tempo base URL"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-k", help="API key for authentication"
    ),
    grafana_datasource_uid: Optional[str] = typer.Option(
        None, "--grafana-uid", "-g", help="Grafana datasource UID"
    ),
):
    """Check Tempo status using the echo endpoint."""
    try:
        client = get_api_client(url, api_key, grafana_datasource_uid)
        result = client.query_echo_endpoint()
        if result:
            console.print("[green]✓ Tempo is responding[/green]")
        else:
            console.print("[red]✗ Tempo is not responding[/red]")
            raise typer.Exit(1)
    except Exception as e:
        handle_api_error(e)


@app.command()
def trace(
    trace_id: str = typer.Argument(..., help="Trace ID to retrieve"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Tempo base URL"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-k", help="API key for authentication"
    ),
    grafana_datasource_uid: Optional[str] = typer.Option(
        None, "--grafana-uid", "-g", help="Grafana datasource UID"
    ),
    start: Optional[int] = typer.Option(
        None, "--start", "-s", help="Start time in Unix epoch seconds"
    ),
    end: Optional[int] = typer.Option(
        None, "--end", "-e", help="End time in Unix epoch seconds"
    ),
    pretty: bool = typer.Option(
        True, "--pretty/--no-pretty", help="Pretty print JSON output"
    ),
):
    """Query a trace by its ID."""
    try:
        client = get_api_client(url, api_key, grafana_datasource_uid)
        result = client.query_trace_by_id_v2(trace_id, start, end)
        print_result(result, pretty)
    except Exception as e:
        handle_api_error(e)


@app.command("search-tags")
def search_tags(
    tags: str = typer.Argument(..., help="logfmt-encoded span/process attributes"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Tempo base URL"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-k", help="API key for authentication"
    ),
    grafana_datasource_uid: Optional[str] = typer.Option(
        None, "--grafana-uid", "-g", help="Grafana datasource UID"
    ),
    min_duration: Optional[str] = typer.Option(
        None, "--min-duration", help="Minimum trace duration (e.g., '5s')"
    ),
    max_duration: Optional[str] = typer.Option(
        None, "--max-duration", help="Maximum trace duration"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-l", help="Max number of traces to return"
    ),
    start: Optional[int] = typer.Option(
        None, "--start", "-s", help="Start time in Unix epoch seconds"
    ),
    end: Optional[int] = typer.Option(
        None, "--end", "-e", help="End time in Unix epoch seconds"
    ),
    spss: Optional[int] = typer.Option(None, "--spss", help="Spans per span set"),
    pretty: bool = typer.Option(
        True, "--pretty/--no-pretty", help="Pretty print JSON output"
    ),
):
    """Search for traces using tag-based search."""
    try:
        client = get_api_client(url, api_key, grafana_datasource_uid)
        result = client.search_traces_by_tags(
            tags=tags,
            min_duration=min_duration,
            max_duration=max_duration,
            limit=limit,
            start=start,
            end=end,
            spss=spss,
        )
        print_result(result, pretty)
    except Exception as e:
        handle_api_error(e)


@app.command("search-query")
def search_query(
    q: str = typer.Argument(..., help="TraceQL query"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Tempo base URL"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-k", help="API key for authentication"
    ),
    grafana_datasource_uid: Optional[str] = typer.Option(
        None, "--grafana-uid", "-g", help="Grafana datasource UID"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-l", help="Max number of traces to return"
    ),
    start: Optional[int] = typer.Option(
        None, "--start", "-s", help="Start time in Unix epoch seconds"
    ),
    end: Optional[int] = typer.Option(
        None, "--end", "-e", help="End time in Unix epoch seconds"
    ),
    spss: Optional[int] = typer.Option(None, "--spss", help="Spans per span set"),
    pretty: bool = typer.Option(
        True, "--pretty/--no-pretty", help="Pretty print JSON output"
    ),
):
    """Search for traces using TraceQL query."""
    try:
        client = get_api_client(url, api_key, grafana_datasource_uid)
        result = client.search_traces_by_query(
            q=q, limit=limit, start=start, end=end, spss=spss
        )
        print_result(result, pretty)
    except Exception as e:
        handle_api_error(e)


@app.command("tag-names")
def tag_names(
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Tempo base URL"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-k", help="API key for authentication"
    ),
    grafana_datasource_uid: Optional[str] = typer.Option(
        None, "--grafana-uid", "-g", help="Grafana datasource UID"
    ),
    scope: Optional[str] = typer.Option(
        None, "--scope", help="Scope filter: 'resource', 'span', or 'intrinsic'"
    ),
    q: Optional[str] = typer.Option(
        None, "--query", "-q", help="TraceQL query to filter tags"
    ),
    start: Optional[int] = typer.Option(
        None, "--start", "-s", help="Start time in Unix epoch seconds"
    ),
    end: Optional[int] = typer.Option(
        None, "--end", "-e", help="End time in Unix epoch seconds"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-l", help="Max number of tag names"
    ),
    max_stale_values: Optional[int] = typer.Option(
        None, "--max-stale-values", help="Max stale values parameter"
    ),
    pretty: bool = typer.Option(
        True, "--pretty/--no-pretty", help="Pretty print JSON output"
    ),
):
    """Search for available tag names."""
    try:
        client = get_api_client(url, api_key, grafana_datasource_uid)
        result = client.search_tag_names_v2(
            scope=scope,
            q=q,
            start=start,
            end=end,
            limit=limit,
            max_stale_values=max_stale_values,
        )
        print_result(result, pretty)
    except Exception as e:
        handle_api_error(e)


@app.command("tag-values")
def tag_values(
    tag: str = typer.Argument(..., help="Tag name to get values for"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Tempo base URL"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-k", help="API key for authentication"
    ),
    grafana_datasource_uid: Optional[str] = typer.Option(
        None, "--grafana-uid", "-g", help="Grafana datasource UID"
    ),
    q: Optional[str] = typer.Option(
        None, "--query", "-q", help="TraceQL query to filter values"
    ),
    start: Optional[int] = typer.Option(
        None, "--start", "-s", help="Start time in Unix epoch seconds"
    ),
    end: Optional[int] = typer.Option(
        None, "--end", "-e", help="End time in Unix epoch seconds"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-l", help="Max number of values"
    ),
    max_stale_values: Optional[int] = typer.Option(
        None, "--max-stale-values", help="Max stale values parameter"
    ),
    pretty: bool = typer.Option(
        True, "--pretty/--no-pretty", help="Pretty print JSON output"
    ),
):
    """Search for values of a specific tag."""
    try:
        client = get_api_client(url, api_key, grafana_datasource_uid)
        result = client.search_tag_values_v2(
            tag=tag,
            q=q,
            start=start,
            end=end,
            limit=limit,
            max_stale_values=max_stale_values,
        )
        print_result(result, pretty)
    except Exception as e:
        handle_api_error(e)


@app.command("metrics-instant")
def metrics_instant(
    q: str = typer.Argument(..., help="TraceQL metrics query"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Tempo base URL"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-k", help="API key for authentication"
    ),
    grafana_datasource_uid: Optional[str] = typer.Option(
        None, "--grafana-uid", "-g", help="Grafana datasource UID"
    ),
    start: Optional[str] = typer.Option(
        None, "--start", "-s", help="Start time (Unix seconds/nanoseconds/RFC3339)"
    ),
    end: Optional[str] = typer.Option(
        None, "--end", "-e", help="End time (Unix seconds/nanoseconds/RFC3339)"
    ),
    since: Optional[str] = typer.Option(
        None, "--since", help="Duration string (e.g., '1h')"
    ),
    pretty: bool = typer.Option(
        True, "--pretty/--no-pretty", help="Pretty print JSON output"
    ),
):
    """Query TraceQL metrics for an instant value."""
    try:
        client = get_api_client(url, api_key, grafana_datasource_uid)
        result = client.query_metrics_instant(q=q, start=start, end=end, since=since)
        print_result(result, pretty)
    except Exception as e:
        handle_api_error(e)


@app.command("metrics-range")
def metrics_range(
    q: str = typer.Argument(..., help="TraceQL metrics query"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Tempo base URL"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-k", help="API key for authentication"
    ),
    grafana_datasource_uid: Optional[str] = typer.Option(
        None, "--grafana-uid", "-g", help="Grafana datasource UID"
    ),
    step: Optional[str] = typer.Option(
        None, "--step", help="Time series granularity (e.g., '1m', '5m')"
    ),
    start: Optional[str] = typer.Option(
        None, "--start", "-s", help="Start time (Unix seconds/nanoseconds/RFC3339)"
    ),
    end: Optional[str] = typer.Option(
        None, "--end", "-e", help="End time (Unix seconds/nanoseconds/RFC3339)"
    ),
    since: Optional[str] = typer.Option(
        None, "--since", help="Duration string (e.g., '3h')"
    ),
    exemplars: Optional[int] = typer.Option(
        None, "--exemplars", help="Maximum number of exemplars to return"
    ),
    pretty: bool = typer.Option(
        True, "--pretty/--no-pretty", help="Pretty print JSON output"
    ),
):
    """Query TraceQL metrics for a time series range."""
    try:
        client = get_api_client(url, api_key, grafana_datasource_uid)
        result = client.query_metrics_range(
            q=q, step=step, start=start, end=end, since=since, exemplars=exemplars
        )
        print_result(result, pretty)
    except Exception as e:
        handle_api_error(e)


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-v", help="Show version"),
):
    """
    Grafana Tempo CLI - Command line utility for interacting with Grafana Tempo API

    Set environment variables:
    - TEMPO_URL: Base URL for Tempo instance
    - TEMPO_API_KEY: API key for authentication
    - GRAFANA_DATASOURCE_UID: Optional Grafana datasource UID

    Or provide them as command options.
    """
    if version:
        console.print("Tempo CLI v1.0.0")
        raise typer.Exit(0)


if __name__ == "__main__":
    app()

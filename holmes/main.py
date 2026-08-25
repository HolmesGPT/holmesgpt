# ruff: noqa: E402
import os

from holmes.utils.cert_utils import add_custom_certificate

ADDITIONAL_CERTIFICATE: str = os.environ.get("CERTIFICATE", "")
if add_custom_certificate(ADDITIONAL_CERTIFICATE):
    print("added custom certificate")

# DO NOT ADD ANY IMPORTS OR CODE ABOVE THIS LINE
# IMPORTING ABOVE MIGHT INITIALIZE AN HTTPS CLIENT THAT DOESN'T TRUST THE CUSTOM CERTIFICATE
import sys
from enum import Enum
from holmes.utils.colors import USER_COLOR
import json
import logging
import socket
import uuid
from pathlib import Path
from typing import Any, List, Optional

import typer
from rich.markdown import Markdown
from rich.rule import Rule

from holmes import get_version  # type: ignore
from holmes.config import (
    Config,
    SourceFactory,
    SupportedTicketSources,
)
from holmes.core.prompt import (
    PromptComponent,
    build_initial_ask_messages,
    build_system_prompt,
    generate_user_prompt,
)
from holmes.core.resource_instruction import ResourceInstructionDocument
from holmes.core.tool_calling_llm import LLMResult, ToolCallingLLM
from holmes.core.tools import PrerequisiteCacheMode, ToolsetTag, pretty_print_toolset_status
from holmes.core.tools_utils.filesystem_result_storage import tool_result_storage
from holmes.common.env_vars import DEFAULT_CLI_USER
from holmes.core.oauth_utils import enable_disk_token_store

# CLI runs as a single local identity. Setting this explicitly on every LLM
# invocation keeps the OAuth token manager mode-agnostic: it never has to
# guess whether a missing user_id means "CLI" or "buggy server caller".
_CLI_REQUEST_CONTEXT = {"user_id": DEFAULT_CLI_USER}
from holmes.core.tracing import SpanType, TracingFactory
from holmes.interactive import InitProgressRenderer, run_interactive_loop, silence_display_loggers
from holmes.plugins.destinations import DestinationType
from holmes.plugins.interfaces import Issue
from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.sources.opsgenie import OPSGENIE_TEAM_INTEGRATION_KEY_HELP
from holmes.utils.console.logging import init_logging
from holmes.utils.console.result import handle_result
from holmes.utils.file_utils import write_json_file
from holmes.checks.checks_cli import checks_app
from holmes.common.cli_commons import (
    opt_api_key,
    opt_config_file,
    opt_model,
    opt_verbose,
)
from holmes.toolset_config_tui import run_toolset_config_tui

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)



investigate_app = typer.Typer(
    add_completion=False,
    name="investigate",
    no_args_is_help=True,
    help="Investigate firing alerts or tickets",
)
app.add_typer(investigate_app, name="investigate")
generate_app = typer.Typer(
    add_completion=False,
    name="generate",
    no_args_is_help=True,
    help="Generate new integrations or test data",
)
app.add_typer(generate_app, name="generate")
toolset_app = typer.Typer(
    add_completion=False,
    name="toolset",
    no_args_is_help=True,
    help="Toolset management commands",
)
app.add_typer(toolset_app, name="toolset")

app.add_typer(checks_app, name="checks")


# Common cli options defined in holmes.common.cli_commons:
# opt_api_key, opt_model, opt_config_file, opt_verbose
# The defaults for options that are also in the config file MUST be None or else the cli defaults will override settings in the config file
opt_fast_model: Optional[str] = typer.Option(
    None, help="Optional fast model for summarization tasks"
)
opt_custom_toolsets: Optional[List[Path]] = typer.Option(
    [],
    "--custom-toolsets",
    "-t",
    help="Path to a custom toolsets. The status of the custom toolsets specified here won't be cached (can specify -t multiple times to add multiple toolsets)",
)
opt_max_steps: Optional[int] = typer.Option(
    100,
    "--max-steps",
    help="Advanced. Maximum number of steps the LLM can take to investigate the issue",
)
opt_log_costs: bool = typer.Option(
    False,
    "--log-costs",
    help="Show LLM cost information in the output",
)
opt_echo_request: bool = typer.Option(
    True,
    "--echo/--no-echo",
    help="Echo back the question provided to HolmesGPT in the output",
)
opt_destination: Optional[DestinationType] = typer.Option(
    DestinationType.CLI,
    "--destination",
    help="Destination for the results of the investigation (defaults to STDOUT)",
)
opt_slack_token: Optional[str] = typer.Option(
    None,
    "--slack-token",
    help="Slack API key if --destination=slack (experimental). Can generate with `pip install robusta-cli && robusta integrations slack`",
)
opt_slack_channel: Optional[str] = typer.Option(
    None,
    "--slack-channel",
    help="Slack channel if --destination=slack (experimental). E.g. #devops",
)
opt_json_output_file: Optional[str] = typer.Option(
    None,
    "--json-output-file",
    help="Save the complete output in json format in to a file",
    envvar="HOLMES_JSON_OUTPUT_FILE",
)

opt_documents: Optional[str] = typer.Option(
    None,
    "--documents",
    help="Additional documents to provide the LLM (typically URLs to runbooks)",
)


def parse_documents(documents: Optional[str]) -> List[ResourceInstructionDocument]:
    resource_documents = []

    if documents is not None:
        data = json.loads(documents)
        for item in data:
            resource_document = ResourceInstructionDocument(**item)
            resource_documents.append(resource_document)

    return resource_documents


def _investigate_issue(
    ai: ToolCallingLLM,
    issue: Issue,
    config: Config,
) -> LLMResult:
    """Investigate an issue using the standard ask system prompt with investigation additions."""
    # Enable disk-based OAuth token storage so MCP tokens previously
    # authorized via `holmes ask` are reused here (idempotent).
    enable_disk_token_store()
    investigation_additions = f"Provide a terse analysis of the following {issue.source_type} alert/issue and why it is firing."
    system_prompt = build_system_prompt(
        toolsets=ai.tool_executor.toolsets,
        skills=None,
        system_prompt_additions=investigation_additions,
        cluster_name=config.cluster_name,
        ask_user_enabled=False,
        prompt_component_overrides={},
    )
    user_prompt = generate_user_prompt(
        f"\n #This is context from the issue:\n{issue.raw}",
        context={},
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return ai.call(messages, request_context=_CLI_REQUEST_CONTEXT)


# TODO: add streaming output
@app.command()
def ask(
    prompt: Optional[str] = typer.Argument(
        None, help="What to ask the LLM (user prompt)"
    ),
    prompt_file: Optional[Path] = typer.Option(
        None,
        "--prompt-file",
        "-pf",
        help="File containing the prompt to ask the LLM (overrides the prompt argument if provided)",
    ),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    fast_model: Optional[str] = opt_fast_model,
    config_file: Optional[Path] = opt_config_file,
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,
    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
    log_costs: bool = opt_log_costs,
    # semi-common options
    destination: Optional[DestinationType] = opt_destination,
    slack_token: Optional[str] = opt_slack_token,
    slack_channel: Optional[str] = opt_slack_channel,
    show_tool_output: bool = typer.Option(
        False,
        "--show-tool-output",
        help="Advanced. Show the output of each tool that was called",
    ),
    include_file: Optional[List[Path]] = typer.Option(
        [],
        "--file",
        "-f",
        help="File to append to prompt (can specify -f multiple times to add multiple files)",
    ),
    json_output_file: Optional[str] = opt_json_output_file,
    echo_request: bool = opt_echo_request,
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        "-i/-n",
        help="Enter interactive mode after the initial question? For scripting, disable this with --no-interactive",
    ),
    refresh_toolsets: bool = typer.Option(
        False,
        "--refresh-toolsets",
        help="Refresh the toolsets status",
    ),
    trace: Optional[str] = typer.Option(
        None,
        "--trace",
        help="Enable tracing to the specified provider ('braintrust' or 'otel'). OTel auto-enables if OTEL_EXPORTER_OTLP_ENDPOINT is set.",
    ),
    system_prompt_additions: Optional[str] = typer.Option(
        None,
        "--system-prompt-additions",
        help="Additional content to append to the system prompt",
    ),
    bash_always_deny: bool = typer.Option(
        False,
        "--bash-always-deny",
        help="Auto-deny all bash commands not in allow list without prompting",
    ),
    bash_always_allow: bool = typer.Option(
        False,
        "--bash-always-allow",
        help="Bypass bash command approval checks. Recommended only for sandboxed environments",
    ),
    fast_mode: bool = typer.Option(
        False,
        "--fast-mode",
        help="Skip TodoWrite planning phase for faster responses",
    ),
):
    """
    Ask any question and answer using available tools
    """
    # Validate mutually exclusive flags
    if bash_always_deny and bash_always_allow:
        raise typer.BadParameter(
            "--bash-always-deny and --bash-always-allow are mutually exclusive. Choose one."
        )

    console = init_logging(verbose, log_costs)  # type: ignore
    # Detect and read piped input
    piped_data = None

    # when attaching a pycharm debugger sys.stdin.isatty() returns false and sys.stdin.read() is stuck
    running_from_pycharm = os.environ.get("PYCHARM_HOSTED", False)

    if not sys.stdin.isatty() and not running_from_pycharm:
        piped_data = sys.stdin.read().strip()
        if interactive:
            console.print(
                "[bold yellow]Interactive mode disabled when reading piped input[/bold yellow]"
            )
            interactive = False

    # Silence display loggers early for interactive mode so that
    # init messages are rendered via the InitProgressRenderer instead.
    if interactive:
        silence_display_loggers()

    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        fast_model=fast_model,
        max_steps=max_steps,
        custom_toolsets_from_cli=custom_toolsets,
        slack_token=slack_token,
        slack_channel=slack_channel,
    )

    # Enable disk-based OAuth token storage for CLI mode
    enable_disk_token_store()

    # Create tracer if trace option is provided
    tracer = TracingFactory.create_tracer(trace, project="HolmesGPT-CLI")
    tracer.start_experiment()

    if prompt_file and prompt:
        raise typer.BadParameter(
            "You cannot provide both a prompt argument and a prompt file. Please use one or the other."
        )
    elif prompt_file:
        if not prompt_file.is_file():
            raise typer.BadParameter(f"Prompt file not found: {prompt_file}")
        with prompt_file.open("r") as f:
            prompt = f.read()
        console.print(
            f"[bold yellow]Loaded prompt from file {prompt_file}[/bold yellow]"
        )
    elif not prompt and not interactive and not piped_data:
        raise typer.BadParameter(
            "Either the 'prompt' argument or the --prompt-file option must be provided (unless using --interactive mode)."
        )

    # Handle piped data
    if piped_data:
        if prompt:
            # User provided both piped data and a prompt
            prompt = f"Here's some piped output:\n\n{piped_data}\n\n{prompt}"
        else:
            # Only piped data, no prompt - ask what to do with it
            prompt = f"Here's some piped output:\n\n{piped_data}\n\nWhat can you tell me about this output?"

    if echo_request and not interactive and prompt:
        console.print(f"[bold {USER_COLOR}]User:[/bold {USER_COLOR}] {prompt}")

    # Build prompt component overrides for fast mode
    prompt_component_overrides = None
    if fast_mode:
        prompt_component_overrides = {
            PromptComponent.TODOWRITE_INSTRUCTIONS: False,
            PromptComponent.TODOWRITE_REMINDER: False,
        }

    with tool_result_storage() as tool_results_dir:
        init_renderer = None
        on_event = None
        if interactive:
            init_renderer = InitProgressRenderer(
                console, model_name=model or config.model or ""
            )
            on_event = init_renderer.on_event
            init_renderer.start()

        ai = config.create_toolcalling_llm(
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
            enable_all_toolsets_possible=True,
            prerequisite_cache=PrerequisiteCacheMode.FORCE_REFRESH if refresh_toolsets else PrerequisiteCacheMode.ENABLED,
            tracer=tracer,
            model=model,
            tool_results_dir=tool_results_dir,
            on_event=on_event,
        )

        if init_renderer is not None:
            init_renderer.stop()

        if interactive:
            run_interactive_loop(
                ai,
                console,
                prompt,
                include_file,
                show_tool_output,
                tracer,
                config.get_skill_catalog(),
                system_prompt_additions,
                json_output_file=json_output_file,
                bash_always_deny=bash_always_deny,
                bash_always_allow=bash_always_allow,
                prompt_component_overrides=prompt_component_overrides,
                config=config,
                config_file_path=config_file,
            )
            return

        if include_file:
            for file_path in include_file:
                console.print(
                    f"[bold yellow]Adding file {file_path} to context[/bold yellow]"
                )

        messages = build_initial_ask_messages(
            prompt,  # type: ignore
            include_file,
            ai.tool_executor,
            config.get_skill_catalog(),
            system_prompt_additions,
            prompt_component_overrides=prompt_component_overrides,
        )

        with tracer.start_trace(
            f'holmes ask "{prompt}"', span_type=SpanType.TASK
        ) as trace_span:
            trace_span.log(input=prompt, metadata={"type": "user_question"})
            response = ai.call(messages, trace_span=trace_span, request_context=_CLI_REQUEST_CONTEXT)
            trace_span.log(
                output=response.result,
            )
            trace_url = tracer.get_trace_url()

        messages = response.messages  # type: ignore # Update messages with the full history

        if json_output_file:
            write_json_file(json_output_file, response.model_dump())

        issue = Issue(
            id=str(uuid.uuid4()),
            name=prompt,  # type: ignore
            source_type="holmes-ask",
            raw={"prompt": prompt, "full_conversation": messages},
            source_instance_id=socket.gethostname(),
        )
        handle_result(
            response,
            console,
            destination,  # type: ignore
            config,
            issue,
            show_tool_output,
            False,  # type: ignore
            log_costs,
        )

        if trace_url:
            console.print(f"🔍 View trace: {trace_url}")


@investigate_app.command()
def alertmanager(
    alertmanager_url: Optional[str] = typer.Option(None, help="AlertManager url"),
    alertmanager_alertname: Optional[str] = typer.Option(
        None,
        help="Investigate all alerts with this name (can be regex that matches multiple alerts). If not given, defaults to all firing alerts",
    ),
    alertmanager_label: Optional[List[str]] = typer.Option(
        [],
        help="For filtering alerts with a specific label. Must be of format key=value. If --alertmanager-label is passed multiple times, alerts must match ALL labels",
    ),
    alertmanager_username: Optional[str] = typer.Option(
        None, help="Username to use for basic auth"
    ),
    alertmanager_password: Optional[str] = typer.Option(
        None, help="Password to use for basic auth"
    ),
    alertmanager_file: Optional[Path] = typer.Option(
        None, help="Load alertmanager alerts from a file (used by the test framework)"
    ),
    alertmanager_limit: Optional[int] = typer.Option(
        None, "-n", help="Limit the number of alerts to process"
    ),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    config_file: Optional[Path] = opt_config_file,  # type: ignore
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,

    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
    # advanced options for this command
    destination: Optional[DestinationType] = opt_destination,
    slack_token: Optional[str] = opt_slack_token,
    slack_channel: Optional[str] = opt_slack_channel,
    json_output_file: Optional[str] = opt_json_output_file,
):
    """
    Investigate a Prometheus/Alertmanager alert
    """
    console = init_logging(verbose)

    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        max_steps=max_steps,
        alertmanager_url=alertmanager_url,
        alertmanager_username=alertmanager_username,
        alertmanager_password=alertmanager_password,
        alertmanager_alertname=alertmanager_alertname,
        alertmanager_label=alertmanager_label,
        alertmanager_file=alertmanager_file,
        slack_token=slack_token,
        slack_channel=slack_channel,
        custom_toolsets_from_cli=custom_toolsets,
    )

    with tool_result_storage() as tool_results_dir:
        ai = config.create_toolcalling_llm(
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
            enable_all_toolsets_possible=True,
            model=model,
            tool_results_dir=tool_results_dir,
        )

        source = config.create_alertmanager_source()

        try:
            issues = source.fetch_issues()
        except Exception as e:
            logging.error("Failed to fetch issues from alertmanager", exc_info=e)
            return

        if alertmanager_limit is not None:
            console.print(
                f"[bold yellow]Limiting to {alertmanager_limit}/{len(issues)} issues.[/bold yellow]"
            )
            issues = issues[:alertmanager_limit]

        if alertmanager_alertname is not None:
            console.print(
                f"[bold yellow]Analyzing {len(issues)} issues matching filter.[/bold yellow] [red]Press Ctrl+C to stop.[/red]"
            )
        else:
            console.print(
                f"[bold yellow]Analyzing all {len(issues)} issues. (Use --alertmanager-alertname to filter.)[/bold yellow] [red]Press Ctrl+C to stop.[/red]"
            )
        results = []
        for i, issue in enumerate(issues):
            console.print(
                f"[bold yellow]Analyzing issue {i+1}/{len(issues)}: {issue.name}...[/bold yellow]"
            )
            result = _investigate_issue(ai, issue, config)
            results.append({"issue": issue.model_dump(), "result": result.model_dump()})
            handle_result(result, console, destination, config, issue, False, True)  # type: ignore

        if json_output_file:
            write_json_file(json_output_file, results)


@generate_app.command("alertmanager-tests")
def generate_alertmanager_tests(
    alertmanager_url: Optional[str] = typer.Option(None, help="AlertManager url"),
    alertmanager_username: Optional[str] = typer.Option(
        None, help="Username to use for basic auth"
    ),
    alertmanager_password: Optional[str] = typer.Option(
        None, help="Password to use for basic auth"
    ),
    output: Optional[Path] = typer.Option(
        None,
        help="Path to dump alertmanager alerts as json (if not given, output curl commands instead)",
    ),
    config_file: Optional[Path] = opt_config_file,  # type: ignore
    verbose: Optional[List[bool]] = opt_verbose,
):
    """
    Connect to alertmanager and dump all alerts as either a json file or curl commands to simulate the alert (depending on --output flag)
    """
    console = init_logging(verbose)  # type: ignore
    config = Config.load_from_file(
        config_file,
        alertmanager_url=alertmanager_url,
        alertmanager_username=alertmanager_username,
        alertmanager_password=alertmanager_password,
    )

    source = config.create_alertmanager_source()
    if output is None:
        source.output_curl_commands(console)
    else:
        source.dump_raw_alerts_to_file(output)


@investigate_app.command()
def jira(
    jira_url: Optional[str] = typer.Option(
        None,
        help="Jira url - e.g. https://your-company.atlassian.net",
        envvar="JIRA_URL",
    ),
    jira_username: Optional[str] = typer.Option(
        None,
        help="The email address with which you log into Jira",
        envvar="JIRA_USERNAME",
    ),
    jira_api_key: str = typer.Option(
        None,
        envvar="JIRA_API_KEY",
    ),
    jira_query: Optional[str] = typer.Option(
        None,
        help="Investigate tickets matching a JQL query (e.g. 'project=DEFAULT_PROJECT')",
    ),
    update: Optional[bool] = typer.Option(False, help="Update Jira with AI results"),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    config_file: Optional[Path] = opt_config_file,  # type: ignore
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,

    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
    json_output_file: Optional[str] = opt_json_output_file,
):
    """
    Investigate a Jira ticket
    """
    console = init_logging(verbose)

    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        max_steps=max_steps,
        jira_url=jira_url,
        jira_username=jira_username,
        jira_api_key=jira_api_key,
        jira_query=jira_query,
        custom_toolsets_from_cli=custom_toolsets,
    )
    source = config.create_jira_source()
    try:
        issues = source.fetch_issues()
    except Exception as e:
        logging.error("Failed to fetch issues from Jira", exc_info=e)
        return

    console.print(
        f"[bold yellow]Analyzing {len(issues)} Jira tickets.[/bold yellow] [red]Press Ctrl+C to stop.[/red]"
    )

    results = []
    with tool_result_storage() as tool_results_dir:
        ai = config.create_toolcalling_llm(
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
            enable_all_toolsets_possible=True,
            model=model,
            tool_results_dir=tool_results_dir,
        )
        for i, issue in enumerate(issues):
            console.print(
                f"[bold yellow]Analyzing Jira ticket {i+1}/{len(issues)}: {issue.name}...[/bold yellow]"
            )
            result = _investigate_issue(ai, issue, config)

            console.print(Rule())
            console.print(f"[bold green]AI analysis of {issue.url}[/bold green]")
            console.print(Markdown(result.result.replace("\n", "\n\n")), style="bold green")  # type: ignore
            console.print(Rule())
            if update:
                source.write_back_result(issue.id, result)
                console.print(f"[bold]Updated ticket {issue.url}.[/bold]")
            else:
                console.print(
                    f"[bold]Not updating ticket {issue.url}. Use the --update option to do so.[/bold]"
                )

            results.append({"issue": issue.model_dump(), "result": result.model_dump()})

    if json_output_file:
        write_json_file(json_output_file, results)


# Define supported sources


@investigate_app.command()
def ticket(
    prompt: str = typer.Argument(help="What to ask the LLM (user prompt)"),
    source: SupportedTicketSources = typer.Option(
        ...,
        help=f"Source system to investigate the ticket from. Supported sources: {', '.join(s.value for s in SupportedTicketSources)}",
    ),
    ticket_url: Optional[str] = typer.Option(
        None,
        help="URL - e.g. https://your-company.atlassian.net",
        envvar="TICKET_URL",
    ),
    ticket_username: Optional[str] = typer.Option(
        None,
        help="The email address with which you log into your Source",
        envvar="TICKET_USERNAME",
    ),
    ticket_api_key: Optional[str] = typer.Option(
        None,
        envvar="TICKET_API_KEY",
    ),
    ticket_id: Optional[str] = typer.Option(
        None,
        help="ticket ID to investigate (e.g., 'KAN-1')",
    ),
    update: Optional[bool] = typer.Option(False, help="Update ticket with AI results"),
    config_file: Optional[Path] = opt_config_file,  # type: ignore
    model: Optional[str] = opt_model,
):
    """
    Fetch and print a Jira ticket from the specified source.
    """

    console = init_logging([])

    # Validate source
    try:
        ticket_source = SourceFactory.create_source(
            source=source,
            config_file=config_file,
            ticket_url=ticket_url,
            ticket_username=ticket_username,
            ticket_api_key=ticket_api_key,
            ticket_id=ticket_id,
            model=model,
        )
    except Exception as e:
        console.print(f"[bold red]Error: {str(e)}[/bold red]")
        return

    try:
        issue_to_investigate = ticket_source.source.fetch_issue(id=ticket_id)  # type: ignore
        if issue_to_investigate is None:
            raise Exception(f"Issue {ticket_id} Not found")
    except Exception as e:
        logging.error(f"Failed to fetch issue from {source}", exc_info=e)
        console.print(
            f"[bold red]Error: Failed to fetch issue {ticket_id} from {source}.[/bold red]"
        )
        return

    with tool_result_storage() as tool_results_dir:
        ai = ticket_source.config.create_toolcalling_llm(
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
            enable_all_toolsets_possible=True,
            model=model,
            tool_results_dir=tool_results_dir,
        )

        # Enable disk-based OAuth token storage for CLI mode
        enable_disk_token_store()

        # Render ticket-specific additions
        ticket_additions = load_and_render_prompt(
            prompt="builtin://_ticket_additions.jinja2",
            context={
                "source": source,
                "output_instructions": ticket_source.output_instructions,
            },
        )

        system_prompt = build_system_prompt(
            toolsets=ai.tool_executor.toolsets,
            skills=None,
            system_prompt_additions=ticket_additions,
            cluster_name=ticket_source.config.cluster_name,
            ask_user_enabled=False,
            prompt_component_overrides={},
        )
        console.print(
            f"[bold yellow]Analyzing ticket: {issue_to_investigate.name}...[/bold yellow]"
        )
        prompt = (
            prompt
            + f" for issue '{issue_to_investigate.name}' with description:'{issue_to_investigate.description}'"
        )

        ticket_user_prompt = generate_user_prompt(prompt, context={})
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": ticket_user_prompt},
        ]
        result = ai.call(messages, request_context=_CLI_REQUEST_CONTEXT)

        console.print(Rule())
        console.print(
            f"[bold green]AI analysis of {issue_to_investigate.url} {prompt}[/bold green]"
        )
        console.print(result.result.replace("\n", "\n\n"), style="bold green")  # type: ignore
        console.print(Rule())

        if update:
            ticket_source.source.write_back_result(issue_to_investigate.id, result)
            console.print(f"[bold]Updated ticket {issue_to_investigate.url}.[/bold]")
        else:
            console.print(
                f"[bold]Not updating ticket {issue_to_investigate.url}. Use the --update option to do so.[/bold]"
            )


@investigate_app.command()
def github(
    github_url: str = typer.Option(
        "https://api.github.com",
        help="The GitHub api base url (e.g: https://api.github.com)",
    ),
    github_owner: Optional[str] = typer.Option(
        None,
        help="The GitHub repository Owner, eg: if the repository url is https://github.com/HolmesGPT/holmesgpt, the owner is HolmesGPT",
    ),
    github_pat: str = typer.Option(
        None,
    ),
    github_repository: Optional[str] = typer.Option(
        None,
        help="The GitHub repository name, eg: if the repository url is https://github.com/HolmesGPT/holmesgpt, the repository name is holmesgpt",
    ),
    update: Optional[bool] = typer.Option(False, help="Update GitHub with AI results"),
    github_query: Optional[str] = typer.Option(
        "is:issue is:open",
        help="Investigate tickets matching a GitHub query (e.g. 'is:issue is:open')",
    ),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    config_file: Optional[Path] = opt_config_file,  # type: ignore
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,

    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
):
    """
    Investigate a GitHub issue
    """
    console = init_logging(verbose)  # type: ignore

    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        max_steps=max_steps,
        github_url=github_url,
        github_owner=github_owner,
        github_pat=github_pat,
        github_repository=github_repository,
        github_query=github_query,
        custom_toolsets_from_cli=custom_toolsets,
    )
    source = config.create_github_source()
    try:
        issues = source.fetch_issues()
    except Exception as e:
        logging.error("Failed to fetch issues from GitHub", exc_info=e)
        return

    console.print(
        f"[bold yellow]Analyzing {len(issues)} GitHub Issues.[/bold yellow] [red]Press Ctrl+C to stop.[/red]"
    )
    with tool_result_storage() as tool_results_dir:
        ai = config.create_toolcalling_llm(
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
            enable_all_toolsets_possible=True,
            model=model,
            tool_results_dir=tool_results_dir,
        )
        for i, issue in enumerate(issues):
            console.print(
                f"[bold yellow]Analyzing GitHub issue {i+1}/{len(issues)}: {issue.name}...[/bold yellow]"
            )

            result = _investigate_issue(ai, issue, config)

            console.print(Rule())
            console.print(f"[bold green]AI analysis of {issue.url}[/bold green]")
            console.print(Markdown(result.result.replace("\n", "\n\n")), style="bold green")  # type: ignore
            console.print(Rule())
            if update:
                source.write_back_result(issue.id, result)
                console.print(f"[bold]Updated ticket {issue.url}.[/bold]")
            else:
                console.print(
                    f"[bold]Not updating issue {issue.url}. Use the --update option to do so.[/bold]"
                )


@investigate_app.command()
def pagerduty(
    pagerduty_api_key: str = typer.Option(
        None,
        help="The PagerDuty API key.  This can be found in the PagerDuty UI under Integrations > API Access Keys.",
    ),
    pagerduty_user_email: Optional[str] = typer.Option(
        None,
        help="When --update is set, which user will be listed as the user who updated the ticket. (Must be the email of a valid user in your PagerDuty account.)",
    ),
    pagerduty_incident_key: Optional[str] = typer.Option(
        None,
        help="If provided, only analyze a single PagerDuty incident matching this key",
    ),
    update: Optional[bool] = typer.Option(
        False, help="Update PagerDuty with AI results"
    ),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    config_file: Optional[Path] = opt_config_file,  # type: ignore
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,

    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
    json_output_file: Optional[str] = opt_json_output_file,
):
    """
    Investigate a PagerDuty incident
    """
    console = init_logging(verbose)

    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        max_steps=max_steps,
        pagerduty_api_key=pagerduty_api_key,
        pagerduty_user_email=pagerduty_user_email,
        pagerduty_incident_key=pagerduty_incident_key,
        custom_toolsets_from_cli=custom_toolsets,
    )
    source = config.create_pagerduty_source()
    try:
        issues = source.fetch_issues()
    except Exception as e:
        logging.error("Failed to fetch issues from PagerDuty", exc_info=e)
        return

    console.print(
        f"[bold yellow]Analyzing {len(issues)} PagerDuty incidents.[/bold yellow] [red]Press Ctrl+C to stop.[/red]"
    )

    results = []
    with tool_result_storage() as tool_results_dir:
        ai = config.create_toolcalling_llm(
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
            enable_all_toolsets_possible=True,
            model=model,
            tool_results_dir=tool_results_dir,
        )
        for i, issue in enumerate(issues):
            console.print(
                f"[bold yellow]Analyzing PagerDuty incident {i+1}/{len(issues)}: {issue.name}...[/bold yellow]"
            )

            result = _investigate_issue(ai, issue, config)

            console.print(Rule())
            console.print(f"[bold green]AI analysis of {issue.url}[/bold green]")
            console.print(Markdown(result.result.replace("\n", "\n\n")), style="bold green")  # type: ignore
            console.print(Rule())
            if update:
                source.write_back_result(issue.id, result)
                console.print(f"[bold]Updated alert {issue.url}.[/bold]")
            else:
                console.print(
                    f"[bold]Not updating alert {issue.url}. Use the --update option to do so.[/bold]"
                )
            results.append({"issue": issue.model_dump(), "result": result.model_dump()})

    if json_output_file:
        write_json_file(json_output_file, results)


@investigate_app.command()
def opsgenie(
    opsgenie_api_key: str = typer.Option(None, help="The OpsGenie API key"),
    opsgenie_team_integration_key: str = typer.Option(
        None, help=OPSGENIE_TEAM_INTEGRATION_KEY_HELP
    ),
    opsgenie_query: Optional[str] = typer.Option(
        None,
        help="E.g. 'message: Foo' (see https://support.atlassian.com/opsgenie/docs/search-queries-for-alerts/)",
    ),
    update: Optional[bool] = typer.Option(
        False, help="Update OpsGenie with AI results"
    ),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    config_file: Optional[Path] = opt_config_file,  # type: ignore
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,

    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
    documents: Optional[str] = opt_documents,
):
    """
    Investigate an OpsGenie alert
    """
    console = init_logging(verbose)  # type: ignore

    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        max_steps=max_steps,
        opsgenie_api_key=opsgenie_api_key,
        opsgenie_team_integration_key=opsgenie_team_integration_key,
        opsgenie_query=opsgenie_query,
        custom_toolsets_from_cli=custom_toolsets,
    )
    source = config.create_opsgenie_source()
    try:
        issues = source.fetch_issues()
    except Exception as e:
        logging.error("Failed to fetch issues from OpsGenie", exc_info=e)
        return

    console.print(
        f"[bold yellow]Analyzing {len(issues)} OpsGenie alerts.[/bold yellow] [red]Press Ctrl+C to stop.[/red]"
    )
    with tool_result_storage() as tool_results_dir:
        ai = config.create_toolcalling_llm(
            toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLI],
            enable_all_toolsets_possible=True,
            model=model,
            tool_results_dir=tool_results_dir,
        )
        for i, issue in enumerate(issues):
            console.print(
                f"[bold yellow]Analyzing OpsGenie alert {i+1}/{len(issues)}: {issue.name}...[/bold yellow]"
            )
            result = _investigate_issue(ai, issue, config)

            console.print(Rule())
            console.print(f"[bold green]AI analysis of {issue.url}[/bold green]")
            console.print(Markdown(result.result.replace("\n", "\n\n")), style="bold green")  # type: ignore
            console.print(Rule())
            if update:
                source.write_back_result(issue.id, result)
                console.print(f"[bold]Updated alert {issue.url}.[/bold]")
            else:
                console.print(
                    f"[bold]Not updating alert {issue.url}. Use the --update option to do so.[/bold]"
                )


@toolset_app.command("list")
def list_toolsets(
    verbose: Optional[List[bool]] = opt_verbose,
    config_file: Optional[Path] = opt_config_file,  # type: ignore
):
    """
    List build-in and custom toolsets status of CLI
    """
    console = init_logging(verbose)
    config = Config.load_from_file(config_file)
    cli_toolsets = config.toolset_manager.list_console_toolsets()

    pretty_print_toolset_status(cli_toolsets, console)


@toolset_app.command("refresh")
def refresh_toolsets(
    verbose: Optional[List[bool]] = opt_verbose,
    config_file: Optional[Path] = opt_config_file,  # type: ignore
):
    """
    Refresh build-in and custom toolsets status of CLI
    """
    console = init_logging(verbose)
    config = Config.load_from_file(config_file)
    cli_toolsets = config.toolset_manager.list_console_toolsets(refresh_status=True)
    pretty_print_toolset_status(cli_toolsets, console)


@toolset_app.command("config")
def config_toolset(
    verbose: Optional[List[bool]] = opt_verbose,
    config_file: Optional[Path] = opt_config_file,  # type: ignore
):
    """
    Interactive configuration editor for toolsets
    """
    console = init_logging(verbose)
    config = Config.load_from_file(config_file)
    run_toolset_config_tui(config, config_file, console)


class _ConfigFormat(str, Enum):
    HOLMES_HELM = "holmes-helm"
    ROBUSTA_HELM = "robusta-helm"


@toolset_app.command("generate-config")
def generate_config(
    config_format: _ConfigFormat = typer.Argument(
        ...,
        help="Target format: 'holmes-helm' for standalone HolmesGPT Helm chart, "
        "'robusta-helm' for Robusta Helm chart.",
    ),
    verbose: Optional[List[bool]] = opt_verbose,
    config_file: Optional[Path] = opt_config_file,  # type: ignore
    output_file: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the generated config to a file instead of stdout",
    ),
    include_disabled: bool = typer.Option(
        False,
        "--include-disabled",
        help="Include disabled toolsets (commented out) in the generated config",
    ),
):
    """
    Generate a Helm values file from the current CLI toolset and model state.

    The CLI auto-enables toolsets by default, but Helm deployments require
    explicit enablement. This command inspects your current CLI environment
    (enabled toolsets + configured LLM model) and generates a Helm values
    snippet for the target chart.

    You must specify the target format:

      holmes-helm   - Standalone HolmesGPT Helm chart

      robusta-helm  - Robusta Helm chart (toolsets nested under 'holmes:')
    """
    import os

    import yaml  # type: ignore

    from holmes.core.tools import ToolsetStatusEnum, ToolsetType

    init_logging(verbose)
    config = Config.load_from_file(config_file)
    cli_toolsets = config.toolset_manager.list_console_toolsets(refresh_status=True)

    # --- Build toolsets section ---
    toolsets_section: dict[str, Any] = {}
    disabled_toolsets: dict[str, str] = {}

    for toolset in cli_toolsets:
        if toolset.type == ToolsetType.MCP:
            continue

        entry: dict[str, Any] = {"enabled": True}
        if toolset.config is not None:
            config_data = toolset.config.model_dump() if hasattr(toolset.config, "model_dump") else {}
            if config_data:
                entry["config"] = config_data

        if toolset.enabled and toolset.status == ToolsetStatusEnum.ENABLED:
            toolsets_section[toolset.name] = entry
        elif include_disabled:
            disabled_toolsets[toolset.name] = toolset.status.value

    # --- Detect AI provider from current model ---
    model_name = config.model
    env_var_name, env_var_hint = _detect_ai_provider_env(model_name)

    additional_env_vars: list[dict[str, str]] = []
    model_list: dict[str, Any] = {}
    if model_name:
        model_entry: dict[str, Any] = {"model": model_name, "temperature": 0}
        if env_var_name:
            model_entry["api_key"] = f"{{{{ env.{env_var_name} }}}}"
            additional_env_vars.append(
                {"name": env_var_name, "value": env_var_hint}
            )
        if config.api_base:
            model_entry["api_base"] = config.api_base
            if env_var_name == "AZURE_API_KEY":
                additional_env_vars.append(
                    {"name": "AZURE_API_BASE", "value": config.api_base}
                )
        if config.api_version:
            model_entry["api_version"] = config.api_version

        # Use model name without provider prefix as the key
        display_name = model_name.split("/")[-1] if "/" in model_name else model_name
        model_list[display_name] = model_entry

    # --- Assemble output dict based on format ---
    output_dict: dict[str, Any] = {}
    if config_format == _ConfigFormat.HOLMES_HELM:
        if additional_env_vars:
            output_dict["additionalEnvVars"] = additional_env_vars
        if model_list:
            output_dict["modelList"] = model_list
        if toolsets_section:
            output_dict["toolsets"] = toolsets_section
    else:
        holmes_section: dict[str, Any] = {}
        if additional_env_vars:
            holmes_section["additionalEnvVars"] = additional_env_vars
        if model_list:
            holmes_section["modelList"] = model_list
        if toolsets_section:
            holmes_section["toolsets"] = toolsets_section
        if holmes_section:
            output_dict["holmes"] = holmes_section

    # --- Render YAML output ---
    lines: list[str] = []
    if config_format == _ConfigFormat.HOLMES_HELM:
        lines.append("# Generated by: holmes toolset generate-config holmes-helm")
        lines.append("# Helm values for the standalone HolmesGPT chart.")
    else:
        lines.append("# Generated by: holmes toolset generate-config robusta-helm")
        lines.append("# Helm values for the Robusta chart (HolmesGPT section).")
    lines.append("# The CLI auto-enables toolsets, but Helm deployments require explicit config.")
    lines.append("# See: https://holmesgpt.dev/data-sources/cli-vs-server-toolsets/")
    if env_var_name:
        lines.append(f"#")
        lines.append(f"# IMPORTANT: Replace the {env_var_name} placeholder value with your actual key,")
        lines.append(f"# or use a secretKeyRef to load it from a Kubernetes Secret.")
    lines.append("")

    if output_dict:
        lines.append(yaml.dump(output_dict, default_flow_style=False, sort_keys=False))
    else:
        lines.append("# No enabled toolsets or model found. Run 'holmes toolset list' to check status.")
        lines.append("")

    if include_disabled and disabled_toolsets:
        lines.append("# --- Disabled/failed toolsets (uncomment and configure to enable) ---")
        indent = "#     " if config_format == _ConfigFormat.ROBUSTA_HELM else "#   "
        for name, status in sorted(disabled_toolsets.items()):
            lines.append(f"{indent}{name}:  # status: {status}")
            lines.append(f"{indent}  enabled: true")
        lines.append("")

    output_text = "\n".join(lines)

    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(output_text)
        typer.echo(f"Config written to {output_file}")
    else:
        typer.echo(output_text)


_PROVIDER_ENV_MAP: list[tuple[str, str, str]] = [
    # (model_prefix, env_var_name, placeholder_hint)
    ("anthropic/", "ANTHROPIC_API_KEY", "<your-anthropic-api-key>"),
    ("azure/", "AZURE_API_KEY", "<your-azure-api-key>"),
    ("bedrock/", "", ""),  # IAM-based, no API key env var
    ("openrouter/", "OPENROUTER_API_KEY", "<your-openrouter-api-key>"),
    ("vertex_ai/", "", ""),  # IAM-based
    ("gemini/", "GEMINI_API_KEY", "<your-gemini-api-key>"),
]


def _detect_ai_provider_env(model_name: Optional[str]) -> tuple[str, str]:
    """Return (env_var_name, placeholder_hint) for the model's AI provider."""
    if not model_name:
        return "", ""

    for prefix, env_var, hint in _PROVIDER_ENV_MAP:
        if model_name.startswith(prefix):
            return env_var, hint

    # Default: assume OpenAI-compatible
    return "OPENAI_API_KEY", "<your-openai-api-key>"


@app.command()
def version() -> None:
    typer.echo(get_version())


def run():
    # Default to "ask" command when no subcommand is given
    if len(sys.argv) == 1:
        sys.argv.insert(1, "ask")
    app()


if __name__ == "__main__":
    run()
